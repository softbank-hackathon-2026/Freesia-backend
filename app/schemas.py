"""API 요청·응답 형식. 프론트(김동윤)·AI(강효승)와 합의할 계약 초안이다."""
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# https://github.com/{owner}/{repo} (끝의 / 와 .git은 허용하고 저장할 때 뗀다)
GITHUB_REPO_URL = re.compile(r"^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")


def parse_github_url(url: str) -> tuple[str, str]:
    """GitHub 저장소 주소에서 (owner, repo)를 뽑는다. 형식이 틀리면 ValueError."""
    m = GITHUB_REPO_URL.match(url.strip())
    if not m or m.group(2) in {".", ".."}:
        raise ValueError("https://github.com/{owner}/{repo} 형식이어야 합니다.")
    return m.group(1), m.group(2)

DeploymentStatus = Literal["pending", "building", "deploying", "success", "failed"]
AnalysisStatus = Literal["pending", "running", "done", "failed"]
CandidateState = Literal["selected", "alternative", "unsuitable"]


class InfraSpace(BaseModel):
    """인프라 관리자가 미리 만들어 둔 인프라 (건물). 플랫폼은 조회만 한다."""

    id: str = Field(examples=["sbh-workload-demo-vpc-public01"])
    name: str = Field(examples=["공개 웹 서비스용"])
    description: str
    network: Literal["public", "private", "ha"]
    computes: list[str] = Field(description="이 인프라에 올릴 수 있는 컴퓨팅 (입점 형태)")
    app_count: int = Field(description="이 인프라에 올라간 앱 수")


class RepositoryCreate(BaseModel):
    """통합 메뉴에서 public GitHub 저장소를 등록한다. 새 저장소를 만드는 것이 아니다."""

    repo_url: str = Field(examples=["https://github.com/softbank-hackathon-2026/sample-app"])
    branch: str = Field("main", min_length=1, max_length=255)

    @field_validator("repo_url")
    @classmethod
    def normalize_repo_url(cls, v: str) -> str:
        owner, repo = parse_github_url(v)
        return f"https://github.com/{owner}/{repo}"

    @field_validator("branch")
    @classmethod
    def strip_branch(cls, v: str) -> str:
        v = v.strip()
        if not v or any(c.isspace() for c in v):
            raise ValueError("브랜치 이름에 공백을 쓸 수 없습니다.")
        return v


class Repository(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(examples=["repo-8a1f3c90b2d4"])
    name: str = Field(examples=["softbank-hackathon-2026/sample-app"], description="owner/repo")
    repo_url: str
    branch: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def assume_utc(cls, v: datetime) -> datetime:
        # SQLite는 시간대 정보를 저장하지 않는다. 저장할 때 UTC로 넣으므로 UTC로 돌려준다.
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


class AppSpaceCreate(BaseModel):
    name: str = Field(examples=["my-todo-app"])
    repo_url: str = Field(examples=["https://github.com/softbank-hackathon-2026/sample-app"])
    branch: str = "main"
    infra_id: str = Field(examples=["sbh-workload-demo-vpc-public01"])


class AppSpace(BaseModel):
    id: str
    name: str
    repo_url: str
    branch: str
    infra_id: str
    created_at: datetime
    latest_deployment_id: str | None = None


class Evidence(BaseModel):
    """AI가 무엇을 보고 판단했는지."""

    file: str = Field(examples=["Dockerfile"])
    finding: str = Field(examples=["컨테이너 이미지로 실행 가능"])
    certain: bool = Field(description="파일에서 확실히 확인했으면 true, 추정이면 false")


class Candidate(BaseModel):
    compute: str = Field(examples=["ecs-fargate"])
    state: CandidateState
    reason: str
    cons: list[str] = []


class Analysis(BaseModel):
    status: AnalysisStatus
    requirements: list[str] = []
    evidence: list[Evidence] = []
    candidates: list[Candidate] = []
    mascot_message: str | None = Field(None, description="마스코트가 말할 한 줄 설명")


class DeploymentCreate(BaseModel):
    compute: str = Field(examples=["ecs-fargate"])


class Deployment(BaseModel):
    id: str
    app_space_id: str
    compute: str
    status: DeploymentStatus
    url: str | None = None
    reason: str | None = Field(None, description="실패 사유")
    created_at: datetime


class DeploymentEvent(BaseModel):
    """SSE로 보내는 진행 이벤트 한 건."""

    status: DeploymentStatus
    step: str = Field(examples=["image-build"])
    message: str
    progress: int = Field(ge=0, le=100)
    url: str | None = None
    at: datetime
