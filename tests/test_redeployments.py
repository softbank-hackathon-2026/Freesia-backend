"""Redeployment reuses a successful plan and pins the reviewed branch commit."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import analysis, models
from app.ids import new_id, now
from app.routers import app_spaces
from tests.conftest import FAKE_SHA, TestingSession
from tests.test_app_spaces import create_space
from tests.test_dispatch import dispatches, real_mode  # noqa: F401

OLD_SHA = "a" * 40


def source_app(client):
    space = create_space(client).json()
    plan = client.post(f"/api/app-spaces/{space['id']}/plans", json={"compute": "ecs-fargate"}).json()["plans"][0]
    with TestingSession() as db:
        source = models.Deployment(
            id=new_id("dep"), app_space_id=space["id"], compute="ecs-fargate", plan_id=plan["id"],
            commit_sha=OLD_SHA, status="success", step="done", run_id=123,
            created_at=now() - timedelta(minutes=5), finished_at=now() - timedelta(minutes=4),
        )
        db.add(source)
        db.get(models.AppSpace, space["id"]).latest_deployment_id = source.id
        db.commit()
        return space, source.id, plan


def preview(client, space):
    return client.get(f"/api/app-spaces/{space['id']}/redeploy-context")


def submit(client, space, source_id, **extra):
    return client.post(f"/api/app-spaces/{space['id']}/redeployments", json={
        "source_deployment_id": source_id, "target_commit_sha": FAKE_SHA, **extra,
    })


def counts():
    with TestingSession() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (
            models.Analysis, models.Plan, models.Deployment, models.DeploymentEvent,
        ))


def test_preview_is_read_only_and_execution_pins_head_not_stale_analysis(client, real_mode, gh, monkeypatch):
    space, source_id, plan = source_app(client)
    with TestingSession() as db:
        db.add(models.Analysis(id=new_id("ana"), app_space_id=space["id"], infra_id=space["infra_id"],
                               commit_sha=OLD_SHA, status="done", model_id="test", created_at=now()))
        db.commit()
    def forbidden(*args, **kwargs):
        pytest.fail("Redeploy must not consult/create analysis or create a plan")
    monkeypatch.setattr(analysis, "latest", forbidden)
    monkeypatch.setattr(analysis, "start", forbidden)
    monkeypatch.setattr(app_spaces, "_new_plan", forbidden)
    before = counts()
    response = preview(client, space)
    assert response.status_code == 200
    assert response.json() == {
        "app_space_id": space["id"], "repo_url": space["repo_url"], "branch": space["branch"],
        "source_deployment_id": source_id, "source_commit_sha": OLD_SHA,
        "target_commit_sha": FAKE_SHA, "compute": "ecs-fargate",
        "plan": {key: plan[key] for key in ("id", "template", "values")},
    }
    assert counts() == before
    assert dispatches(gh) == []
    result = submit(client, space, source_id)
    assert result.status_code == 201
    dep = result.json()
    assert dep["id"] != source_id
    assert (dep["commit_sha"], dep["plan_id"], dep["source_deployment_id"]) == (FAKE_SHA, plan["id"], source_id)
    inputs = json.loads(dispatches(gh)[0].content)["inputs"]
    assert (inputs["commit_sha"], inputs["plan_id"], inputs["compute"]) == (FAKE_SHA, plan["id"], "ecs-fargate")
    assert counts() == (before[0], before[1], before[2] + 1, before[3] + 1)
    assert client.get(f"/api/deployments/{dep['id']}").json() == dep
    assert client.get(f"/api/app-spaces/{space['id']}").json()["latest_deployment_id"] == dep["id"]


def test_later_failed_attempt_does_not_replace_successful_source(client, real_mode):
    space, source_id, plan = source_app(client)
    with TestingSession() as db:
        failed = models.Deployment(id=new_id("dep"), app_space_id=space["id"], compute="lambda",
                                   status="failed", step="build", created_at=now(), run_id=456)
        db.add(failed)
        db.get(models.AppSpace, space["id"]).latest_deployment_id = failed.id
        db.commit()
    assert preview(client, space).json()["source_deployment_id"] == source_id
    assert submit(client, space, source_id).json()["plan_id"] == plan["id"]


@pytest.mark.parametrize("invalid", ["missing", "cross_app", "wrong_compute", "no_plan"])
def test_latest_success_invalid_plan_does_not_fall_back(client, real_mode, gh, invalid):
    space, older_id, plan = source_app(client)
    other = create_space(client).json()
    with TestingSession() as db:
        bad_plan_id = None if invalid == "no_plan" else "missing"
        if invalid in {"cross_app", "wrong_compute"}:
            source_plan = db.get(models.Plan, plan["id"])
            source_plan.app_space_id = other["id"] if invalid == "cross_app" else space["id"]
            source_plan.compute = "lambda" if invalid == "wrong_compute" else "ecs-fargate"
            bad_plan_id = source_plan.id
        newer = models.Deployment(id=new_id("dep"), app_space_id=space["id"], compute="ecs-fargate",
                                  plan_id=bad_plan_id, status="success", step="done", run_id=456, created_at=now())
        db.add(newer)
        db.commit()
    before = counts()
    for response in (preview(client, space), submit(client, space, older_id)):
        assert (response.status_code, response.json()["error"]) == (409, "redeploy_unavailable")
    assert counts() == before
    assert gh["requests"] == []


@pytest.mark.parametrize("change,error", [("source", "redeploy_source_changed"), ("target", "redeploy_target_changed")])
def test_reviewed_context_must_still_match(client, real_mode, gh, change, error):
    space, source_id, plan = source_app(client)
    assert preview(client, space).status_code == 200
    if change == "source":
        with TestingSession() as db:
            db.add(models.Deployment(id=new_id("dep"), app_space_id=space["id"], compute="ecs-fargate",
                                     plan_id=plan["id"], status="success", step="done", run_id=456, created_at=now()))
            db.commit()
    before = counts()
    response = submit(client, space, source_id, **({"target_commit_sha": "d" * 40} if change == "target" else {}))
    assert (response.status_code, response.json()["error"]) == (409, error)
    assert counts() == before
    assert dispatches(gh) == []


@pytest.mark.parametrize("state,error", [
    ("pending", "deployment_in_progress"), ("teardown", "teardown_in_progress"),
    ("torn_down", "not_deployed"), ("simulated", "redeploy_unavailable"),
])
def test_unavailable_or_busy_context_cannot_create_deployment(client, real_mode, gh, state, error):
    space, source_id, _ = source_app(client)
    with TestingSession() as db:
        source = db.get(models.Deployment, source_id)
        row = db.get(models.AppSpace, space["id"])
        if state == "pending":
            source.status = "pending"
        elif state == "simulated":
            source.run_id = None
        else:
            row.teardown_status = "requested" if state == "teardown" else "success"
            row.teardown_requested_at = now()
            row.teardown_finished_at = now() if state == "torn_down" else None
        db.commit()
    before = counts()
    for response in (preview(client, space), submit(client, space, source_id)):
        assert (response.status_code, response.json()["error"]) == (409, error)
    assert counts() == before
    assert gh["requests"] == []


def test_null_source_sha_is_honestly_returned(client, real_mode):
    space, source_id, _ = source_app(client)
    with TestingSession() as db:
        db.get(models.Deployment, source_id).commit_sha = None
        db.commit()
    assert preview(client, space).json()["source_commit_sha"] is None


@pytest.mark.parametrize("extra", [{"plan_id": "evil"}, {"compute": "lambda"}, {"commit_sha": OLD_SHA},
                                    {"target_commit_sha": "main"}, {"source_deployment_id": ""}])
def test_execution_rejects_unexpected_fields_and_non_sha_targets(client, real_mode, gh, extra):
    space, source_id, _ = source_app(client)
    before = counts()
    assert submit(client, space, source_id, **extra).status_code == 422
    assert counts() == before
    assert gh["requests"] == []


def test_cross_app_source_rejected(client, real_mode, gh):
    space, _, _ = source_app(client)
    other, other_source, _ = source_app(client)
    response = submit(client, space, other_source)
    assert (response.status_code, response.json()["error"]) == (409, "redeploy_source_changed")
    assert dispatches(gh) == []


def test_github_resolution_failure_leaves_no_attempt(client, real_mode, gh):
    space, source_id, _ = source_app(client)
    gh["commit_status"] = 404
    before = counts()
    for response in (preview(client, space), submit(client, space, source_id)):
        assert (response.status_code, response.json()["error"]) == (502, "github_error")
    assert counts() == before
    assert dispatches(gh) == []


def test_dispatch_failure_is_persisted_with_redeploy_provenance(client, real_mode, gh):
    space, source_id, plan = source_app(client)
    gh["dispatch_status"] = 422
    response = submit(client, space, source_id)
    assert response.status_code == 201
    dep = client.get(f"/api/deployments/{response.json()['id']}").json()
    assert dep["status"] == "failed"
    assert (dep["source_deployment_id"], dep["commit_sha"], dep["plan_id"]) == (source_id, FAKE_SHA, plan["id"])
    assert preview(client, space).json()["source_deployment_id"] == source_id


def test_new_attempt_blocks_duplicate_and_teardown(client, real_mode, gh):
    space, source_id, _ = source_app(client)
    assert submit(client, space, source_id).status_code == 201
    assert submit(client, space, source_id).json()["error"] == "deployment_in_progress"
    assert client.post(f"/api/app-spaces/{space['id']}/teardown").json()["error"] == "deployment_in_progress"
    assert len(dispatches(gh)) == 1


def test_app_without_success_has_no_redeploy_context(client, real_mode, gh):
    space = create_space(client).json()
    assert preview(client, space).json()["error"] == "redeploy_unavailable"
    assert submit(client, space, "dep-missing").json()["error"] == "redeploy_unavailable"
    assert gh["requests"] == []


def test_unknown_and_deleted_apps_are_not_redeployable(client, real_mode, gh):
    space, source_id, _ = source_app(client)
    with TestingSession() as db:
        db.get(models.AppSpace, space["id"]).deleted_at = now()
        db.commit()
    for candidate in (space, {"id": "missing"}):
        for response in (preview(client, candidate), submit(client, candidate, source_id)):
            assert (response.status_code, response.json()["error"]) == (404, "app_space_not_found")
    assert gh["requests"] == []


@pytest.mark.parametrize("failure,error", [("compute", "compute_not_supported"), ("infra", "infra_not_ready")])
def test_redeploy_rechecks_current_infra_readiness(client, real_mode, gh, failure, error):
    space, source_id, _ = source_app(client)
    with TestingSession() as db:
        infra = db.get(models.InfraSpace, space["infra_id"])
        if failure == "compute":
            infra.computes = ["lambda"]
        else:
            infra.vpc_id = None
        db.commit()
    for response in (preview(client, space), submit(client, space, source_id)):
        assert (response.status_code, response.json()["error"]) == (400, error)
    assert gh["requests"] == []


def test_only_success_after_completed_teardown_restores_eligibility(client, real_mode):
    space, source_id, plan = source_app(client)
    with TestingSession() as db:
        row = db.get(models.AppSpace, space["id"])
        row.teardown_status = "success"
        row.teardown_requested_at = now() - timedelta(minutes=2)
        row.teardown_finished_at = now() - timedelta(minutes=1)
        attempt = models.Deployment(id=new_id("dep"), app_space_id=space["id"], compute="ecs-fargate",
                                    plan_id=plan["id"], status="failed", step="done", run_id=456, created_at=now())
        db.add(attempt)
        db.commit()
        new_id_value = attempt.id
    assert preview(client, space).json()["error"] == "not_deployed"
    with TestingSession() as db:
        db.get(models.Deployment, new_id_value).status = "success"
        db.commit()
    assert preview(client, space).json()["source_deployment_id"] == new_id_value
    assert submit(client, space, new_id_value).status_code == 201

@pytest.mark.parametrize('teardown_state', ['failed', 'success'])
def test_any_teardown_attempt_invalidates_older_success(client, real_mode, gh, teardown_state):
    space, source_id, plan = source_app(client)
    with TestingSession() as db:
        row = db.get(models.AppSpace, space['id'])
        row.teardown_status = teardown_state
        row.teardown_requested_at = now() - timedelta(minutes=2)
        row.teardown_finished_at = now() - timedelta(minutes=1)
        db.commit()
    assert preview(client, space).json()['error'] == 'not_deployed'
    assert submit(client, space, source_id).json()['error'] == 'not_deployed'
    assert gh['requests'] == []
    with TestingSession() as db:
        restored = models.Deployment(id=new_id('dep'), app_space_id=space['id'], compute='ecs-fargate',
            plan_id=plan['id'], status='success', step='done', run_id=789, created_at=now())
        db.add(restored)
        db.commit()
        restored_id = restored.id
    assert preview(client, space).json()['source_deployment_id'] == restored_id


@pytest.mark.parametrize('teardown_state', ['requested', 'success', 'failed'])
def test_missing_teardown_timestamp_does_not_allow_old_success(client, real_mode, gh, teardown_state):
    space, source_id, _ = source_app(client)
    with TestingSession() as db:
        row = db.get(models.AppSpace, space['id'])
        row.teardown_status = teardown_state
        row.teardown_requested_at = None
        db.commit()
    assert preview(client, space).json()['error'] == 'redeploy_unavailable'
    assert submit(client, space, source_id).json()['error'] == 'redeploy_unavailable'
    assert gh['requests'] == []
