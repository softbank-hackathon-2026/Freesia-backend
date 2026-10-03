"""배포 트리 미리 채우기: 배포 시작 때 템플릿 자원을 "대기"로 넣고, 워크플로 계획이 오면 그걸로 바꾼다."""
from app import github
from tests.test_deployments import callback, real_mode, start  # noqa: F401 (real_mode는 fixture)

MAIN_TF = '''
data "aws_region" "current" {}

resource "aws_ecs_cluster" "app" {
  name = "x"
}

resource "aws_lb" "app" {
  # resource "aws_fake" "comment" 는 줄 맨 앞이 아니라 무시된다
}
'''
SERVICE_TF = 'resource "aws_ecs_service" "app" {\n}\n'


def tree(client, dep_id):
    return [(r["address"], r["state"]) for r in client.get(f"/api/deployments/{dep_id}/resources").json()]


def plan(*items):
    return {
        "status": "deploying", "step": "deploy", "message": "만들 자원",
        "resources": [{"address": a, "type": a.split(".")[0], "action": act, "state": st} for a, act, st in items],
    }


def one(address, state, action="create"):
    return {"status": "deploying", "step": "deploy", "message": "m",
            "resources": [{"address": address, "type": address.split(".")[0], "action": action, "state": state}]}


def fargate(gh):
    gh["templates"]["ecs-fargate/basic"] = {"main.tf": MAIN_TF, "service.tf": SERVICE_TF}


def test_deploy_start_prefills_template_resources(client, real_mode, gh):
    fargate(gh)
    dep = start(client).json()
    # data 블록은 빼고, 파일 이름 순서·파일 안 순서대로
    assert tree(client, dep["id"]) == [
        ("aws_ecs_cluster.app", "pending"), ("aws_lb.app", "pending"), ("aws_ecs_service.app", "pending"),
    ]
    # 배포 레포 main 브랜치 템플릿을 토큰과 함께 읽는다
    reqs = [r for r in gh["requests"] if "/contents/" in r.url.path]
    assert reqs[0].url.path == "/repos/softbank-hackathon-2026/workload-deploy/contents/templates/ecs-fargate/basic"
    assert reqs[0].url.params["ref"] == "main"
    assert reqs[0].headers["Authorization"] == "Bearer test-token"


def test_plan_replaces_prefilled_and_drops_unplanned(client, real_mode, gh):
    fargate(gh)
    dep_id = start(client).json()["id"]
    # 재배포처럼 안 바뀌는 자원은 done(no-op)으로, 계획에만 있는 자원은 끝에 붙고, 계획에 없는 예상 자원은 사라진다
    r = callback(client, dep_id, plan(
        ("aws_lb.app", "create", "pending"),
        ("aws_ecs_cluster.app", "no-op", "done"),
        ("aws_iam_role.execution", "create", "pending"),
    ))
    assert r.status_code == 204
    assert tree(client, dep_id) == [
        ("aws_ecs_cluster.app", "done"), ("aws_lb.app", "pending"), ("aws_iam_role.execution", "pending"),
    ]
    # 이후 apply는 하나씩 온다. 하나짜리 보고는 다른 자원을 지우지 않는다
    callback(client, dep_id, one("aws_lb.app", "in_progress"))
    assert ("aws_lb.app", "in_progress") in tree(client, dep_id)
    assert len(tree(client, dep_id)) == 3


def test_build_failure_clears_prefilled(client, real_mode, gh):
    fargate(gh)
    dep_id = start(client).json()["id"]
    callback(client, dep_id, {"status": "failed", "step": "build", "message": "m", "reason": "빌드 실패"})
    assert tree(client, dep_id) == []


def test_success_without_plan_callback_drops_leftover_predictions(client, real_mode, gh):
    """계획 콜백이 유실되고 apply 보고만 온 경우: 보고된 자원만 남는다."""
    fargate(gh)
    dep_id = start(client).json()["id"]
    callback(client, dep_id, one("aws_lb.app", "done"))
    callback(client, dep_id, {"status": "success", "step": "done", "message": "m", "url": "https://a.example"})
    assert tree(client, dep_id) == [("aws_lb.app", "done")]


def test_failure_during_apply_keeps_reported_resources(client, real_mode, gh):
    fargate(gh)
    dep_id = start(client).json()["id"]
    callback(client, dep_id, plan(("aws_lb.app", "create", "pending"), ("aws_ecs_cluster.app", "create", "pending")))
    callback(client, dep_id, one("aws_lb.app", "failed"))
    callback(client, dep_id, {"status": "failed", "step": "deploy", "message": "m", "reason": "x"})
    # 계획으로 보고된 자원은 실패해도 남는다 (못 만든 자원은 "대기"로 보인다)
    assert tree(client, dep_id) == [("aws_ecs_cluster.app", "pending"), ("aws_lb.app", "failed")]


def test_template_read_failure_still_deploys(client, real_mode, gh):
    # 템플릿이 없으면(404) 미리 채우지 않고 배포는 그대로 진행된다
    r = start(client)
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    assert tree(client, r.json()["id"]) == []


def test_dispatch_failure_does_not_prefill(client, real_mode, gh):
    fargate(gh)
    gh["dispatch_status"] = 500
    dep = start(client).json()
    assert dep["status"] == "failed"
    assert tree(client, dep["id"]) == []
    assert not [r for r in gh["requests"] if "/contents/" in r.url.path]


def test_template_resources_are_cached(real_mode, gh):
    fargate(gh)
    first = github.template_resources("ecs-fargate/basic")
    count = len(gh["requests"])
    assert github.template_resources("ecs-fargate/basic") == first
    assert len(gh["requests"]) == count


def test_simulated_deploy_does_not_prefill(client, gh):
    fargate(gh)
    dep_id = start(client).json()["id"]
    assert tree(client, dep_id) == []
    assert not [r for r in gh["requests"] if "/contents/" in r.url.path]
