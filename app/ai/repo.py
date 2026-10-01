"""public GitHub 저장소를 읽어 분석할 파일을 고른다 (ADR-019, API 명세 7절 5번).

저장소는 커밋 기준 압축 파일(tarball) 하나로 받는다. 파일을 하나씩 받으면 토큰 없이 시간당 60회 제한에
금방 걸린다. 분석 한 번에 GitHub API를 2번(커밋 SHA, tarball) 부른다.
압축은 디스크에 풀지 않고 메모리에서 읽으므로 압축 안의 경로가 서버 파일을 건드릴 일이 없다.
"""
import io
import re
import tarfile
from urllib.parse import quote

import httpx

from app.schemas import parse_github_url

GITHUB_API = "https://api.github.com"
# 고객 저장소는 신뢰할 수 없는 입력이라 크기 상한을 둔다
MAX_TARBALL_BYTES = 30 * 1024 * 1024  # 내려받는 압축 파일
MAX_UNPACKED_BYTES = 100 * 1024 * 1024  # 압축을 푼 텍스트 파일 합계
MAX_MEMBER_BYTES = 1024 * 1024  # 이보다 큰 파일은 생성물·데이터로 보고 건너뛴다
MAX_FILE_CHARS = 20_000  # 모델에 넣는 파일 하나의 최대 글자 수. 넘으면 잘라서 truncated로 표시한다
STAGE2_BUDGET_CHARS = 100_000  # ponytail: 토큰 대신 글자 수로 어림. 모델 컨텍스트·응답 시간을 보고 조정

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target", ".next", "__pycache__", ".venv", "venv", "coverage"}
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock",
    "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock",
}

# 1단계 핵심 파일 4종 (ADR-019). 저장소 루트에서만 찾는다.
DEPENDENCY_FILES = {
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "Gemfile", "Cargo.toml", "composer.json",
}
START_FILES = {
    "Procfile", "start.sh", "run.sh", "entrypoint.sh", "docker-entrypoint.sh",
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
}

REPO_FAIL_MESSAGE = "저장소를 읽지 못했어요. 잠시 후 다시 시도해 주세요."


class RepoError(Exception):
    """저장소를 읽지 못함. 메시지는 그대로 화면(mascot_message)에 보여 줄 수 있는 문장이다."""


def fetch_repo(repo_url: str, branch: str, transport: httpx.BaseTransport | None = None) -> tuple[str, dict[str, str]]:
    """브랜치를 커밋 SHA로 확정하고, 그 커밋의 텍스트 파일을 {경로: 내용}으로 돌려준다.

    주소 형식이 틀리면 ValueError, GitHub에서 못 읽으면 RepoError. transport는 테스트용이다.
    """
    owner, repo = parse_github_url(repo_url)
    try:
        with httpx.Client(base_url=GITHUB_API, timeout=30, follow_redirects=True, transport=transport) as c:
            r = c.get(f"/repos/{owner}/{repo}/commits/{quote(branch, safe='/')}", headers={"Accept": "application/vnd.github.sha"})
            if r.status_code in (404, 422):
                raise RepoError("저장소나 브랜치를 찾을 수 없어요. public 저장소인지 확인해 주세요.")
            if r.status_code in (403, 429):
                raise RepoError("GitHub 요청 한도에 걸렸어요. 잠시 후 다시 시도해 주세요.")
            r.raise_for_status()
            sha = r.text.strip()
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise RepoError(REPO_FAIL_MESSAGE)
            data = bytearray()
            with c.stream("GET", f"/repos/{owner}/{repo}/tarball/{sha}") as r:
                r.raise_for_status()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > MAX_TARBALL_BYTES:
                        raise RepoError("저장소가 너무 커서 분석할 수 없어요.")
        return sha, _read_tarball(bytes(data))
    except (httpx.HTTPError, tarfile.TarError) as e:
        raise RepoError(REPO_FAIL_MESSAGE) from e


def _read_tarball(data: bytes) -> dict[str, str]:
    files: dict[str, str] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar:
            parts = m.name.split("/")[1:]  # 맨 위 폴더({owner}-{repo}-{sha 앞자리})를 뗀다
            if not m.isfile() or not parts or m.size > MAX_MEMBER_BYTES:
                continue
            if parts[-1] in LOCKFILES or any(p in SKIP_DIRS for p in parts[:-1]):
                continue
            total += m.size
            if total > MAX_UNPACKED_BYTES:
                raise RepoError("저장소가 너무 커서 분석할 수 없어요.")
            raw = tar.extractfile(m).read()
            if b"\0" in raw[:8000]:  # 이미지 같은 바이너리
                continue
            files["/".join(parts)] = raw.decode("utf-8", errors="replace")
    return files


def pick_stage1(files: dict[str, str]) -> tuple[list[str], list[str]]:
    """핵심 파일 4종을 루트에서 고른다. (고른 경로, 저장소에 없는 종류)를 돌려준다.

    없는 종류를 따로 알려야 모델이 "없음"과 "읽지 않음"을 구분한다.
    """
    root = sorted(p for p in files if "/" not in p)
    groups = {
        "Dockerfile": [p for p in root if p == "Dockerfile"],
        "의존성 파일": [p for p in root if p in DEPENDENCY_FILES],
        "README": [p for p in root if p.lower().startswith("readme")],
        "시작 스크립트": [p for p in root if p in START_FILES],
    }
    return [p for ps in groups.values() for p in ps], [name for name, ps in groups.items() if not ps]


def pick_stage2(files: dict[str, str]) -> list[str]:
    """전체 코드에서 예산만큼 고른다. 핵심 파일을 먼저 넣고, 나머지는 얕은 경로·작은 파일부터.

    ponytail: 경로 깊이·크기 순 단순 규칙. 핵심 코드가 깊은 곳에 있으면 빠질 수 있다.
    빠지는 일이 잦으면 Dockerfile CMD·start 스크립트가 가리키는 파일을 먼저 넣는다.
    """
    first, _ = pick_stage1(files)
    rest = sorted((p for p in files if p not in first), key=lambda p: (p.count("/"), len(files[p]), p))
    picked, used = [], 0
    for p in first + rest:
        size = min(len(files[p]), MAX_FILE_CHARS)
        if used + size > STAGE2_BUDGET_CHARS:
            continue  # 큰 파일 하나 때문에 멈추지 않고 뒤의 작은 파일은 계속 넣는다
        picked.append(p)
        used += size
    return picked
