def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_db(client):
    r = client.get("/health/db")
    assert r.status_code == 200
    assert r.json()["db"] == "ok"


def test_version(client):
    r = client.get("/version.txt")
    assert r.status_code == 200
    assert r.text == "test-sha"
