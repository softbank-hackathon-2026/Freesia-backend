"""온프레미스(Proxmox) 인프라 조회. Proxmox는 가짜 응답으로 바꿔 끼운다."""
import httpx
import pytest

from app import infra_sync, models, onprem
from app.config import get_settings
from tests.conftest import TestingSession

# 실제 Proxmox 응답과 같은 모양 (태그는 소문자로 오고, 태그가 없으면 tags 키가 아예 없다)
VMS = [
    {"vmid": 152, "name": "vm-codex", "status": "running", "type": "qemu",
     "tags": "displayname-softbankservice;freesia;owner-jhw;project-sbh;scope-vm"},
    {"vmid": 110, "name": "cloudflared", "status": "running", "type": "lxc", "tags": "cloudflare;community-script;network"},
    {"vmid": 151, "name": "recrawler-server", "status": "running", "type": "lxc", "tags": " "},
    {"vmid": 254, "name": "coredns", "status": "running", "type": "lxc"},
]


@pytest.fixture
def proxmox(monkeypatch):
    """온프레미스 설정을 켜고 Proxmox를 가짜로 바꾼다. AWS에는 인프라가 없는 것으로 친다.
    state["vms"]에 VM 목록을 넣거나, 예외를 넣으면 그 예외가 난다."""
    settings = get_settings()
    for name, value in (
        ("onprem_api_url", "https://pve.test"),
        ("onprem_cf_client_id", "cf-id"),
        ("onprem_cf_client_secret", "cf-secret"),
        ("onprem_pve_token_id", "user@pve!tok"),
        ("onprem_pve_token_secret", "secret"),
    ):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(infra_sync, "_read_aws", lambda account: [])
    state = {"vms": list(VMS)}

    def fetch(*args):
        if isinstance(state["vms"], Exception):
            raise state["vms"]
        return state["vms"]

    monkeypatch.setattr(onprem, "fetch_vms", fetch)
    return state


def listed(client, monkeypatch):
    monkeypatch.setattr(infra_sync, "_next_run", 0.0)  # 5초가 지났다고 친다
    r = client.get("/api/infra-spaces")
    assert r.status_code == 200
    return {i["id"]: i for i in r.json()}


def test_freesia_vm_is_listed(client, proxmox, monkeypatch):
    got = listed(client, monkeypatch)
    assert set(got) == {"vm-codex"}  # freesia 태그가 없는 VM·컨테이너는 빠진다
    item = got["vm-codex"]
    assert (item["provider"], item["name"], item["network"], item["status"]) == ("onprem", "softbankservice", "vm", "ready")
    assert item["computes"] == ["vm"] and item["deployable_computes"] == ["vm"] and item["app_count"] == 0
    with TestingSession() as db:
        row = db.get(models.InfraSpace, "vm-codex")
        assert (row.aws_account_id, row.vpc_id, row.region) == (None, None, None)


def test_vm_without_displayname_uses_vm_name(client, proxmox, monkeypatch):
    proxmox["vms"] = [{"name": "svc-vm-02", "tags": "freesia"}]
    assert listed(client, monkeypatch)["svc-vm-02"]["name"] == "svc-vm-02"


def test_freesia_tag_must_be_the_whole_tag(client, proxmox, monkeypatch):
    proxmox["vms"] = [
        {"name": "a-vm", "tags": "freesia-extra"},  # 태그 이름이 freesia가 아니다
        {"name": "b-vm", "tags": "x;Freesia"},  # 대소문자는 가리지 않는다
        {"name": "c-vm", "tags": None},  # 태그가 비어 있어도 깨지지 않는다
    ]
    assert set(listed(client, monkeypatch)) == {"b-vm"}


def test_removed_vm_disappears(client, proxmox, monkeypatch):
    assert "vm-codex" in listed(client, monkeypatch)
    proxmox["vms"] = []
    assert "vm-codex" not in listed(client, monkeypatch)


def test_proxmox_failure_keeps_vm(client, proxmox, monkeypatch):
    assert "vm-codex" in listed(client, monkeypatch)
    proxmox["vms"] = httpx.ConnectError("down")
    assert "vm-codex" in listed(client, monkeypatch)  # 못 읽으면 DB 값을 그대로 둔다


def test_bad_vm_name_is_skipped(client, proxmox, monkeypatch):
    proxmox["vms"] = [{"name": "Bad_Name", "tags": "freesia"}]
    assert listed(client, monkeypatch) == {}


def test_not_configured_is_not_read(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "onprem_api_url", "")
    monkeypatch.setattr(infra_sync, "_read_aws", lambda account: [])

    def fail(*args):
        raise AssertionError("설정이 없으면 Proxmox를 부르지 않는다")

    monkeypatch.setattr(onprem, "fetch_vms", fail)
    assert listed(client, monkeypatch) == {}


def test_parse_tags():
    assert onprem.parse_tags("displayname-softbankservice;freesia;owner-jhw") == {"displayname": "softbankservice", "owner": "jhw"}
    assert onprem.parse_tags("displayName-onprem-vm") == {"displayname": "onprem-vm"}  # 키는 소문자, 값의 하이픈은 그대로
    assert onprem.parse_tags("") == {} and onprem.parse_tags(" ") == {}


def test_fetch_vms_sends_cloudflare_and_proxmox_headers(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers)
        return httpx.Response(200, json={"data": [{"name": "vm-codex"}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(onprem.httpx, "get", fake_get)
    assert onprem.fetch_vms("https://pve.test/", "cf-id", "cf-secret", "user@pve!tok", "secret") == [{"name": "vm-codex"}]
    assert seen["url"] == "https://pve.test/api2/json/cluster/resources"
    assert seen["params"] == {"type": "vm"}
    assert seen["headers"] == {
        "CF-Access-Client-Id": "cf-id",
        "CF-Access-Client-Secret": "cf-secret",
        "Authorization": "PVEAPIToken=user@pve!tok=secret",
    }


def test_fetch_vms_raises_on_error_status(monkeypatch):
    monkeypatch.setattr(onprem.httpx, "get", lambda url, **kwargs: httpx.Response(401, request=httpx.Request("GET", url)))
    with pytest.raises(httpx.HTTPStatusError):
        onprem.fetch_vms("https://pve.test", "cf-id", "cf-secret", "user@pve!tok", "secret")
