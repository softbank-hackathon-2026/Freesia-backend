import pytest

URL = "https://github.com/softbank-hackathon-2026/sample-app"


def _register(client, repo_url=URL, **extra):
    return client.post("/api/repositories", json={"repo_url": repo_url, **extra})


def test_register_list_delete(client):
    r = _register(client)
    assert r.status_code == 201
    repo = r.json()
    assert repo["id"].startswith("repo-")
    assert repo["name"] == "softbank-hackathon-2026/sample-app"
    assert repo["repo_url"] == URL
    assert repo["branch"] == "main"
    assert repo["created_at"].endswith("Z") or repo["created_at"].endswith("+00:00")

    items = client.get("/api/repositories").json()
    assert [i["id"] for i in items] == [repo["id"]]
    assert items[0]["created_at"].endswith("Z") or items[0]["created_at"].endswith("+00:00")

    assert client.delete(f"/api/repositories/{repo['id']}").status_code == 204
    assert client.get("/api/repositories").json() == []


def test_list_is_newest_first(client):
    first = _register(client, "https://github.com/org/one").json()
    second = _register(client, "https://github.com/org/two").json()
    assert [i["id"] for i in client.get("/api/repositories").json()] == [second["id"], first["id"]]


@pytest.mark.parametrize("raw", [URL + "/", URL + ".git", f"  {URL}  "])
def test_url_is_normalized(client, raw):
    assert _register(client, raw).json()["repo_url"] == URL


def test_same_repo_and_branch_is_rejected(client):
    assert _register(client).status_code == 201
    r = _register(client, URL + ".git")
    assert r.status_code == 409
    assert r.json()["error"] == "repository_exists"


def test_same_repo_other_branch_is_allowed(client):
    assert _register(client).status_code == 201
    assert _register(client, branch="dev").status_code == 201


@pytest.mark.parametrize(
    "bad",
    [
        "https://gitlab.com/org/app",
        "github.com/org/app",
        "https://github.com/org",
        "https://github.com/org/app/tree/main",
        "http://github.com/org/app",
        "https://github.com/org/..",
    ],
)
def test_invalid_url_is_rejected(client, bad):
    r = _register(client, bad)
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_branch_with_space_is_rejected(client):
    r = _register(client, branch="my branch")
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_delete_unknown_returns_404(client):
    r = client.delete("/api/repositories/repo-nope")
    assert r.status_code == 404
    assert r.json()["error"] == "repository_not_found"
