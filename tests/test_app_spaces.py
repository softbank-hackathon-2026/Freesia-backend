PUBLIC = "sbh-workload-demo-vpc-public01"
REPO = "https://github.com/org/todo"


def register(client, repo_url=REPO, branch="main"):
    return client.post("/api/repositories", json={"repo_url": repo_url, "branch": branch})


def create_space(client, infra_id=PUBLIC, repo_url=REPO, branch="main", register_repo=True):
    if register_repo:
        register(client, repo_url, branch)
    return client.post(
        "/api/app-spaces",
        json={"name": "todo", "repo_url": repo_url, "branch": branch, "infra_id": infra_id},
    )


def test_infra_list_and_detail(client):
    items = client.get("/api/infra-spaces").json()
    assert [i["id"] for i in items] == [PUBLIC, "sbh-workload-demo-vpc-private01", "sbh-workload-demo-vpc-ha01"]
    assert set(items[0]) == {"id", "provider", "name", "description", "network", "status", "computes", "app_count", "deployable_computes"}  # 내부 값은 숨김
    assert client.get(f"/api/infra-spaces/{PUBLIC}").json()["app_count"] == 0
    assert client.get("/api/infra-spaces/nope").json()["error"] == "infra_not_found"


def test_app_space_is_saved_and_counted(client):
    r = create_space(client)
    assert r.status_code == 201
    space = r.json()
    assert space["id"].startswith("app-")
    assert space["latest_deployment_id"] is None
    assert space["created_at"].endswith("Z") or space["created_at"].endswith("+00:00")
    assert client.get("/api/app-spaces").json()[0]["id"] == space["id"]
    assert client.get(f"/api/app-spaces/{space['id']}").json() == space
    assert client.get(f"/api/infra-spaces/{PUBLIC}").json()["app_count"] == 1


def test_app_space_list_is_newest_first(client):
    first = create_space(client).json()
    second = create_space(client, register_repo=False).json()
    assert [s["id"] for s in client.get("/api/app-spaces").json()] == [second["id"], first["id"]]


def test_repo_url_is_normalized_before_lookup(client):
    register(client)
    r = create_space(client, repo_url=REPO + ".git", register_repo=False)
    assert r.status_code == 201
    assert r.json()["repo_url"] == REPO


def _repository_id(app_space_id):
    from app import models
    from tests.conftest import TestingSession

    with TestingSession() as db:
        return db.get(models.AppSpace, app_space_id).repository_id


def test_registered_repository_is_linked(client):
    repo = register(client).json()
    space = create_space(client, register_repo=False).json()
    assert _repository_id(space["id"]) == repo["id"]


def test_unregistered_repository_is_allowed_for_now(client):
    # 프론트 통합 화면이 저장소 API에 연결되면 400 repository_not_registered로 바꾼다
    r = create_space(client, branch="dev", register_repo=False)
    assert r.status_code == 201
    assert _repository_id(r.json()["id"]) is None


def test_invalid_repo_url_is_rejected(client):
    r = create_space(client, repo_url="https://gitlab.com/org/todo", register_repo=False)
    assert (r.status_code, r.json()["error"]) == (422, "validation_error")


def test_unknown_infra_is_rejected(client):
    r = create_space(client, infra_id="nope")
    assert r.status_code == 400
    assert r.json()["error"] == "infra_not_found"


def test_app_survives_repository_unregister(client):
    repo = register(client).json()
    space = create_space(client, register_repo=False).json()
    assert client.delete(f"/api/repositories/{repo['id']}").status_code == 204
    assert client.get(f"/api/app-spaces/{space['id']}").status_code == 200


def test_unknown_app_space(client):
    assert client.get("/api/app-spaces/nope").json()["error"] == "app_space_not_found"


def test_old_paths_are_not_served(client):
    assert client.get("/health").status_code == 404
    assert client.get("/api/docs").status_code == 200
