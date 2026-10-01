"""저장소 읽기와 파일 고르기. GitHub는 httpx.MockTransport로 흉내 낸다."""
import io
import tarfile

import httpx
import pytest

from app.ai import repo
from app.ai.repo import RepoError, fetch_repo, pick_stage1, pick_stage2

URL = "https://github.com/org/app"
SHA = "a" * 40
SAMPLE = {
    "Dockerfile": b'FROM node:20\nCMD ["node", "src/index.js"]',
    "package.json": b'{"scripts": {"start": "node src/index.js"}}',
    "README.md": b"# app",
    "src/index.js": b"app.listen(3000)",
    "node_modules/x/index.js": b"skip",
    "package-lock.json": b"{}",
    "logo.png": b"\x89PNG\x00\x00",
}


def make_tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"org-app-aaaaaaa/{path}")  # GitHub tarball처럼 맨 위 폴더 하나
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def github(commit_status=200, tarball=None):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if "/commits/" in req.url.path:
            return httpx.Response(commit_status, text=SHA)
        return httpx.Response(200, content=tarball or make_tarball(SAMPLE))

    return httpx.MockTransport(handler), seen


def test_fetch_repo_reads_text_files_at_commit():
    transport, seen = github()
    sha, files = fetch_repo(URL, "feat/x", transport)
    assert sha == SHA
    assert seen == ["/repos/org/app/commits/feat/x", f"/repos/org/app/tarball/{SHA}"]
    # node_modules, 락파일, 바이너리는 뺀다
    assert sorted(files) == ["Dockerfile", "README.md", "package.json", "src/index.js"]


@pytest.mark.parametrize("status,word", [(404, "찾을 수 없어요"), (422, "찾을 수 없어요"), (403, "한도"), (500, "읽지 못했어요")])
def test_fetch_repo_github_errors(status, word):
    with pytest.raises(RepoError, match=word):
        fetch_repo(URL, "main", github(commit_status=status)[0])


def test_fetch_repo_rejects_large_tarball(monkeypatch):
    monkeypatch.setattr(repo, "MAX_TARBALL_BYTES", 10)
    with pytest.raises(RepoError, match="너무 커서"):
        fetch_repo(URL, "main", github()[0])


def test_fetch_repo_rejects_large_unpacked(monkeypatch):
    monkeypatch.setattr(repo, "MAX_UNPACKED_BYTES", 50)
    with pytest.raises(RepoError, match="너무 커서"):
        fetch_repo(URL, "main", github(tarball=make_tarball({"a.txt": b"x" * 40, "b.txt": b"x" * 40}))[0])


def test_fetch_repo_bad_url():
    with pytest.raises(ValueError):
        fetch_repo("https://gitlab.com/org/app", "main", github()[0])


def test_pick_stage1_root_core_files():
    files = {p: "x" for p in ["Dockerfile", "package.json", "README.md", "src/index.js", "src/README.md"]}
    assert pick_stage1(files) == (["Dockerfile", "package.json", "README.md"], ["시작 스크립트"])


def test_pick_stage2_core_first_then_within_budget(monkeypatch):
    monkeypatch.setattr(repo, "STAGE2_BUDGET_CHARS", 30)
    files = {"Dockerfile": "x" * 10, "big.js": "x" * 25, "src/deep/a.js": "x" * 5, "b.js": "x" * 5}
    # 핵심 파일 먼저, 그다음 얕은 경로·작은 파일. 예산을 넘는 big.js는 건너뛰고 뒤의 작은 파일은 넣는다
    assert pick_stage2(files) == ["Dockerfile", "b.js", "src/deep/a.js"]
