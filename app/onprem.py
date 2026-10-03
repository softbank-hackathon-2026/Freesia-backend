"""온프레미스(Proxmox) 인프라 조회.

cloudflared 터널 뒤의 Proxmox API를 Cloudflare Access(Service Token)를 거쳐 읽고,
freesia 태그가 붙은 VM을 인프라 Space 응답 형식으로 바꾼다. ECS는 NAT Gateway로 나가 HTTPS로 부르기만 한다.
"""
import httpx

MANAGED_TAG = "freesia"  # 이 태그가 붙은 VM만 플랫폼이 관리할 인프라로 가져온다


def parse_tags(raw: str) -> dict[str, str]:
    """Proxmox 태그 문자열을 딕셔너리로 만든다. ';'로 태그를 나누고, 태그마다 첫 하이픈에서 키와 값으로 나눈다.

    Proxmox는 태그를 소문자로 저장하므로 키도 소문자로 쓴다.
    'displayname-onprem;freesia;owner-jhw' -> {'displayname': 'onprem', 'owner': 'jhw'}. 하이픈 없는 태그는 건너뛴다.
    """
    tags: dict[str, str] = {}
    for item in raw.split(";"):
        key, sep, value = item.strip().partition("-")
        if sep and key and value:
            tags[key.lower()] = value
    return tags


def to_infra_space(vm: dict, tags: dict[str, str]) -> dict:
    """VM 하나를 인프라 Space 형식으로 바꾼다. id는 VM 이름, 화면 이름은 태그 displayname (없으면 VM 이름)."""
    return {
        "id": vm["name"],
        "provider": "onprem",
        "name": tags.get("displayname") or vm["name"],
        "description": "VM에 Ansible로 백엔드를 배포합니다",
        "network": "vm",
        "status": "ready",
        "computes": ["vm"],
        "app_count": 0,
        "deployable_computes": ["vm"],
    }


def fetch_vms(base_url: str, cf_client_id: str, cf_client_secret: str, pve_token_id: str, pve_token_secret: str) -> list[dict]:
    """Cloudflare Access를 거쳐 Proxmox API에서 VM 목록을 읽는다 (읽기만). 실패하면 httpx 예외."""
    response = httpx.get(
        f"{base_url.rstrip('/')}/api2/json/cluster/resources",
        params={"type": "vm"},
        headers={
            "CF-Access-Client-Id": cf_client_id,
            "CF-Access-Client-Secret": cf_client_secret,
            "Authorization": f"PVEAPIToken={pve_token_id}={pve_token_secret}",
        },
        timeout=httpx.Timeout(10.0, connect=3.0),
    )
    response.raise_for_status()
    return response.json()["data"]


def list_onprem_spaces(base_url: str, cf_client_id: str, cf_client_secret: str, pve_token_id: str, pve_token_secret: str) -> list[dict]:
    """freesia 태그가 붙은 VM만 골라 인프라 Space 형식 목록으로 돌려준다."""
    spaces: list[dict] = []
    for vm in fetch_vms(base_url, cf_client_id, cf_client_secret, pve_token_id, pve_token_secret):
        raw: str = vm.get("tags") or ""
        if MANAGED_TAG in [name.strip().lower() for name in raw.split(";")]:
            spaces.append(to_infra_space(vm, parse_tags(raw)))
    return spaces
