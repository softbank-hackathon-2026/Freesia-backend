"""GitHub 호출: 배포할 커밋 확정, 배포 레포 워크플로 실행, 템플릿 자원 목록 읽기 (ADR-009).

워크플로(workload-deploy)는 항상 떠 있는 서버가 아니다. 백엔드가 GitHub에 실행을 요청하면
GitHub이 그때 실행한다(workflow_dispatch). 실행 요청에는 GITHUB_DEPLOY_TOKEN이 필요하다.
"""
import logging
import re
import time
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.schemas import parse_github_url

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
# 테스트에서 httpx.MockTransport로 바꿔 끼운다
TRANSPORT: httpx.BaseTransport | None = None

# 템플릿 .tf의 자원 블록. 템플릿은 module·count·for_each를 쓰지 않아 주소가 "타입.이름" 그대로다
RESOURCE_RE = re.compile(r'^resource\s+"([\w-]+)"\s+"([\w-]+)"', re.M)
TEMPLATE_CACHE_SECONDS = 600
# 배포 시작 요청 안에서 읽으므로 짧게 기다린다. 넘으면 미리 채우기만 건너뛴다
TEMPLATE_TIMEOUT_SECONDS = 5
# (템플릿, ref) → (읽은 시각, [(타입, 주소)]). 서버(프로세스)마다 따로 기억한다
_template_cache: dict[tuple[str, str], tuple[float, list[tuple[str, str]]]] = {}


class GitHubError(Exception):
    """GitHub 호출 실패. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def _client() -> httpx.Client:
    return httpx.Client(base_url=GITHUB_API, timeout=15, transport=TRANSPORT)


def latest_commit(repo_url: str, branch: str) -> str:
    """public 저장소 브랜치의 최신 커밋 SHA. 토큰 없이 부른다 (사용자 저장소는 public만, 규칙 7)."""
    owner, repo = parse_github_url(repo_url)
    try:
        with _client() as c:
            r = c.get(
                f"/repos/{owner}/{repo}/commits/{quote(branch, safe='/')}",
                headers={"Accept": "application/vnd.github.sha"},
            )
    except httpx.HTTPError as e:
        raise GitHubError("GitHub에 연결하지 못했습니다.") from e
    if r.status_code in (404, 422):
        raise GitHubError("저장소나 브랜치를 찾을 수 없습니다. public 저장소인지 확인해 주세요.")
    sha = r.text.strip()
    if r.status_code != 200 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GitHubError("배포할 커밋을 확인하지 못했습니다.")
    return sha


def dispatch(workflow: str, inputs: dict[str, str]) -> None:
    """배포 레포의 워크플로를 실행한다. GitHub은 실행을 접수했다는 204만 돌려준다 (진행은 콜백으로 온다)."""
    s = get_settings()
    if not s.github_deploy_token:
        raise GitHubError("GitHub 실행 토큰이 설정되지 않았습니다.")
    try:
        with _client() as c:
            r = c.post(
                f"/repos/{s.deploy_repo}/actions/workflows/{workflow}/dispatches",
                json={"ref": s.deploy_ref, "inputs": inputs},
                headers={
                    "Authorization": f"Bearer {s.github_deploy_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as e:
        raise GitHubError("GitHub에 연결하지 못했습니다.") from e
    if r.status_code != 204:
        # 토큰은 로그에 남기지 않는다. GitHub 응답 본문(오류 설명)만 남긴다
        logger.warning("workflow_dispatch 실패: %s %s %s", workflow, r.status_code, r.text[:500])
        raise GitHubError(f"배포 워크플로를 실행하지 못했습니다 (GitHub {r.status_code}).")


def template_resources(template: str) -> list[tuple[str, str]]:
    """배포 레포 templates/<template>/*.tf에 적힌 자원을 [(타입, 주소)]로 돌려준다. 못 읽으면 GitHubError.

    배포 트리를 시작부터 보여 주려고 읽는다. 템플릿이 바뀌어도 백엔드를 고치지 않게 목록을 코드에 적지 않는다.
    배포 레포는 public이지만 토큰이 있으면 붙인다 (토큰 없는 한도 시간당 60번을 저장소 분석과 나눠 쓰므로).
    """
    s = get_settings()
    key = (template, s.deploy_ref)
    cached = _template_cache.get(key)
    if cached and time.monotonic() - cached[0] < TEMPLATE_CACHE_SECONDS:
        return cached[1]
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    if s.github_deploy_token:
        headers["Authorization"] = f"Bearer {s.github_deploy_token}"
    try:
        with _client() as c:
            r = c.get(
                f"/repos/{s.deploy_repo}/contents/templates/{template}",
                params={"ref": s.deploy_ref}, headers=headers, timeout=TEMPLATE_TIMEOUT_SECONDS,
            )
            if r.status_code != 200:
                raise GitHubError(f"템플릿 {template}을 찾지 못했습니다 (GitHub {r.status_code}).")
            paths = sorted(f["path"] for f in r.json() if f.get("type") == "file" and f["name"].endswith(".tf"))
            found = []
            for path in paths:
                r = c.get(
                    f"/repos/{s.deploy_repo}/contents/{path}",
                    params={"ref": s.deploy_ref},
                    headers=headers | {"Accept": "application/vnd.github.raw"},
                    timeout=TEMPLATE_TIMEOUT_SECONDS,
                )
                if r.status_code != 200:
                    raise GitHubError(f"템플릿 파일 {path}을 읽지 못했습니다 (GitHub {r.status_code}).")
                found += [(t, f"{t}.{n}") for t, n in RESOURCE_RE.findall(r.text)]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise GitHubError("템플릿을 읽지 못했습니다.") from e
    if not found:
        raise GitHubError(f"템플릿 {template}에 자원이 없습니다.")
    _template_cache[key] = (time.monotonic(), found)
    return found
