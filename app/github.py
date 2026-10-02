"""GitHub 호출: 배포할 커밋 확정과 배포 레포 워크플로 실행 (ADR-009).

워크플로(workload-deploy)는 항상 떠 있는 서버가 아니다. 백엔드가 GitHub에 실행을 요청하면
GitHub이 그때 실행한다(workflow_dispatch). 실행 요청에는 GITHUB_DEPLOY_TOKEN이 필요하다.
"""
import logging
import re
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.schemas import parse_github_url

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
# 테스트에서 httpx.MockTransport로 바꿔 끼운다
TRANSPORT: httpx.BaseTransport | None = None


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
