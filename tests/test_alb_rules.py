"""공용 ALB 경로와 리스너 규칙 번호 (app/alb_rules.py)."""
import pytest

from app import alb_rules, models
from tests.conftest import TestingSession
from tests.test_app_spaces import create_space

HA = "sbh-workload-demo-vpc-ha01"


def make(client, route=None, infra_id=HA):
    body = {"name": "app", "repo_url": "https://github.com/org/shop", "branch": "main", "infra_id": infra_id}
    if route is not None:
        body["route_path"] = route
    return client.post("/api/app-spaces", json=body)


def assign(app_id):
    with TestingSession() as db:
        space = db.get(models.AppSpace, app_id)
        n = alb_rules.assign_priority(db, space)
        db.commit()
        return n


# 경로


def test_route_saved_and_returned(client):
    r = make(client, "/api")
    assert r.status_code == 201
    assert (r.json()["route_path"], r.json()["alb_rule_priority"]) == ("/api", None)


def test_route_is_optional(client):
    assert create_space(client).json()["route_path"] is None


@pytest.mark.parametrize("route", ["api", "/api/", "/API", "/api//v2", "/api?x=1", "/" + "a" * 100])
def test_bad_route(client, route):
    assert make(client, route).status_code == 422


@pytest.mark.parametrize("first,second", [("/api", "/api"), ("/api", "/api/v2"), ("/api/v2", "/api"), ("/", "/")])
def test_overlapping_route_on_same_infra(client, first, second):
    make(client, first)
    r = make(client, second)
    assert (r.status_code, r.json()["error"]) == (409, "route_path_taken")


@pytest.mark.parametrize("first,second", [("/api", "/"), ("/api", "/apix"), ("/api", "/shop")])
def test_separate_routes(client, first, second):
    make(client, first)
    assert make(client, second).status_code == 201


def test_same_route_on_other_infra(client):
    make(client, "/api")
    assert make(client, "/api", infra_id="sbh-workload-demo-vpc-public01").status_code == 201


def test_deleted_app_frees_route(client):
    old = make(client, "/api").json()
    assert client.delete(f"/api/app-spaces/{old['id']}").status_code == 204
    assert make(client, "/api").status_code == 201


# 규칙 번호


def test_paths_get_100s_and_root_gets_1000s(client):
    api, shop, web = (make(client, r).json()["id"] for r in ("/api", "/shop", "/"))
    assert (assign(api), assign(shop), assign(web)) == (100, 101, 1000)


def test_app_without_route_counts_as_root(client):
    assert assign(make(client).json()["id"]) == 1000


def test_priority_is_kept(client):
    api = make(client, "/api").json()["id"]
    assert assign(api) == assign(api) == 100
    assert client.get(f"/api/app-spaces/{api}").json()["alb_rule_priority"] == 100


def test_numbers_are_not_reused_after_delete(client):
    old = make(client, "/api").json()["id"]
    assign(old)
    client.delete(f"/api/app-spaces/{old}")
    assert assign(make(client, "/shop").json()["id"]) == 101


def test_numbers_are_per_infra(client):
    assign(make(client, "/api").json()["id"])
    assert assign(make(client, "/api", infra_id="sbh-workload-demo-vpc-public01").json()["id"]) == 100


def test_second_root_conflicts(client):
    assign(make(client, "/").json()["id"])
    other = make(client).json()["id"]  # 경로 없음 = 루트
    with pytest.raises(alb_rules.RouteConflict):
        assign(other)
