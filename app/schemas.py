"""API 요청·응답 형식. 프론트(김동윤)·AI(강효승)와 합의할 계약 초안이다."""
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# https://github.com/{owner}/{repo} (끝의 / 와 .git은 허용하고 저장할 때 뗀다)
GITHUB_REPO_URL = re.compile(r"^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")


def parse_github_url(url: str) -> tuple[str, str]:
    """GitHub 저장소 주소에서 (owner, repo)를 뽑는다. 형식이 틀리면 ValueError."""
    m = GITHUB_REPO_URL.match(url.strip())
    if not m or m.group(2) in {".", ".."}:
        raise ValueError("https://github.com/{owner}/{repo} 형식이어야 합니다.")
    return m.group(1), m.group(2)


def normalize_repo_url(url: str) -> str:
    owner, repo = parse_github_url(url)
    return f"https://github.com/{owner}/{repo}"


def normalize_branch(branch: str) -> str:
    branch = branch.strip()
    if not branch or any(c.isspace() for c in branch):
        raise ValueError("브랜치 이름에 공백을 쓸 수 없습니다.")
    return branch


def _assume_utc(v: datetime) -> datetime:
    # SQLite는 시간대 정보를 저장하지 않는다. 저장할 때 UTC로 넣으므로 UTC로 돌려준다.
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(_assume_utc)]

DeploymentStatus = Literal["pending", "building", "deploying", "success", "failed"]
# 배포 단계 (API 명세 9-3절, ADR-009). 이 순서로만 진행한다.
DeploymentStep = Literal["queued", "prepare", "build", "deploy", "verify", "done"]
Compute = Literal["ecs-fargate", "lambda", "ec2"]
AnalysisStatus = Literal["pending", "running", "done", "failed"]
# 모니터링 상태 (API 명세 12절). not_deployed: 지금 AWS에 떠 있는 실제 배포가 없음, waiting: 떠 있지만 아직 값이 없음
MonitoringStatus = Literal["ok", "waiting", "not_deployed", "unsupported", "error"]
# 내리기 상태 (API 명세 9-5절). requested 뒤에 Destroy 워크플로 콜백으로 success·failed가 된다
TeardownStatus = Literal["requested", "success", "failed"]
CandidateState = Literal["selected", "alternative", "unsuitable"]
ResourceState = Literal["pending", "in_progress", "done", "failed"]
# 트리에 보이는 상태. 내리기에 성공하면 그 앱의 자원은 모두 deleted가 된다 (워크플로는 보내지 않음)
ResourceViewState = Literal["pending", "in_progress", "done", "failed", "deleted"]


class InfraSpace(BaseModel):
    """인프라 관리자가 미리 만들어 둔 인프라 (건물). 플랫폼은 조회만 한다."""

    id: str = Field(examples=["sbh-workload-demo-vpc-public01"])
    name: str = Field(examples=["공개 웹 서비스용"])
    description: str
    network: Literal["public", "db-isolated", "multi-az", "private", "ha"] = Field(
        description="public / db-isolated / multi-az. private·ha는 예전 임시 값"
    )
    status: Literal["ready", "preparing", "unavailable"] = Field(
        "ready", description="ready: 배포 가능, preparing: 앱용 퍼블릭 서브넷이 부족함, unavailable: AWS에서 사라짐(목록에 안 나옴)"
    )
    computes: list[str] = Field(description="이 인프라에 올릴 수 있는 컴퓨팅 (입점 형태). AI가 이 안에서 후보를 고른다")
    app_count: int = Field(description="이 인프라에 올라간 앱 수")
    deployable_computes: list[str] = Field(
        description="computes 중 배포 템플릿이 준비돼 지금 배포할 수 있는 것. 나머지는 화면에 '준비 중'으로 보인다"
    )


class RepositoryCreate(BaseModel):
    """통합 메뉴에서 public GitHub 저장소를 등록한다. 새 저장소를 만드는 것이 아니다."""

    repo_url: Annotated[str, AfterValidator(normalize_repo_url)] = Field(
        examples=["https://github.com/softbank-hackathon-2026/sample-app"]
    )
    branch: Annotated[str, AfterValidator(normalize_branch)] = Field("main", min_length=1, max_length=255)


class Repository(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(examples=["repo-8a1f3c90b2d4"])
    name: str = Field(examples=["softbank-hackathon-2026/sample-app"], description="owner/repo")
    repo_url: str
    branch: str
    created_at: UtcDatetime


class AppSpaceCreate(BaseModel):
    """등록된 저장소(repo_url + branch)와 인프라로 앱을 만든다."""

    name: str = Field(min_length=1, max_length=100, examples=["my-todo-app"])
    repo_url: Annotated[str, AfterValidator(normalize_repo_url)] = Field(
        examples=["https://github.com/softbank-hackathon-2026/sample-app"]
    )
    branch: Annotated[str, AfterValidator(normalize_branch)] = Field("main", min_length=1, max_length=255)
    infra_id: str | None = Field(
        None,
        examples=["sbh-workload-demo-vpc-public01"],
        description="비우면 기본 인프라(VPC 태그 DefaultInfra=true). 기본 인프라가 없으면 400 no_default_infra",
    )
    route_path: str | None = Field(
        None,
        pattern=r"^/(?:[a-z0-9-]+(?:/[a-z0-9-]+)*)?$",
        max_length=100,
        examples=["/api"],
        description="공용 ALB 뒤에 붙을 때 이 앱이 받을 경로. `/`는 나머지 전부. 같은 인프라에서 겹치면 409",
    )


class AppSpace(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    repo_url: str
    branch: str
    infra_id: str
    created_at: UtcDatetime
    latest_deployment_id: str | None = None
    route_path: str | None = Field(None, description="공용 ALB 경로. 공용 ALB를 안 쓰면 null")
    alb_rule_priority: int | None = Field(None, description="공용 ALB 리스너 규칙 번호. 처음 배포할 때 백엔드가 정한다")
    teardown_status: TeardownStatus | None = Field(None, description="내리기 상태. 없으면 내린 적 없음")
    teardown_requested_at: UtcDatetime | None = Field(None, description="내리기를 요청한 시각")
    teardown_finished_at: UtcDatetime | None = Field(None, description="내리기가 끝난 시각 (success·failed)")
    teardown_reason: str | None = Field(None, description="내리기 실패 이유")


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
    evidence_files: list[str] = Field([], description="이 후보를 판단한 근거 파일. evidence[].file 중에서 고른다 (ADR-020)")


class Analysis(BaseModel):
    status: AnalysisStatus
    requirements: list[str] = []
    evidence: list[Evidence] = []
    candidates: list[Candidate] = []
    mascot_message: str | None = Field(None, description="마스코트가 말할 한 줄 설명")
    template_values: dict[str, dict[str, Any]] = Field(
        {},
        description="컴퓨팅별 배포 템플릿 값 (ADR-012). 검사를 통과한 값만 있고, 구성안을 만들 때 쓴다",
        examples=[{"ecs-fargate": {"container_port": 3000, "cpu": 256, "memory": 512, "health_check_path": "/health"}}],
    )


class PlanCreate(BaseModel):
    compute: Compute


class Plan(BaseModel):
    """구성안: 템플릿 하나와 거기에 넣을 값 (API 명세 8절, ADR-012 Option B)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(examples=["plan-51c0e7a9d2f1"])
    name: str = Field(examples=["기본형"])
    summary: str
    pros: list[str]
    cons: list[str]
    template: str = Field(examples=["ecs-fargate/basic"], description="배포 레포 templates/ 아래 폴더 이름")
    values: dict[str, Any] = Field(
        examples=[{"container_port": 80, "cpu": 256, "memory": 512, "health_check_path": "/"}],
        description="템플릿에 넣을 값. 템플릿마다 다르다",
    )


class PlanSet(BaseModel):
    status: Literal["done"] = "done"
    compute: Compute
    plans: list[Plan]


class PlanInfra(BaseModel):
    """워크플로가 앱을 올릴 인프라 값 (DB의 인프라 정보)."""

    id: str
    vpc_id: str | None
    public_subnet_ids: list[str]
    private_subnet_ids: list[str] = Field(description="shared-alb 구성안이면 앱 서브넷(db 제외)만")
    aws_account_id: str | None = Field(None, description="배포할 계정. 없으면 칸을 빼고, 워크플로는 Workload로 배포")
    alb_listener_arn: str | None = None
    alb_security_group_id: str | None = None
    alb_base_url: str | None = None


class WorkflowPlan(BaseModel):
    """배포 워크플로가 plan_id로 받아 가는 값 (API 명세 8-1절)."""

    id: str
    template: str
    values: dict[str, Any]
    infra: PlanInfra


class Teardown(BaseModel):
    """배포된 앱 내리기 요청 결과. 실제 삭제는 배포 레포 Destroy 워크플로가 한다."""

    app_space_id: str
    status: Literal["requested"] = "requested"
    requested_at: UtcDatetime


class AppMetrics(BaseModel):
    """앱 지표 (API 명세 12절). 최근 1분 값. 값이 없으면 null이고, 0이나 정상으로 대신 채우지 않는다."""

    status: MonitoringStatus
    message: str | None = Field(None, description="status가 ok가 아닐 때 화면에 보일 문장")
    compute: Compute | None = Field(None, description="떠 있는 배포의 컴퓨팅. 어떤 칸을 보여 줄지 고를 때 쓴다")
    cpu_percent: float | None = Field(None, examples=[24.1], description="CPU 사용률 (%). Fargate·EC2")
    memory_percent: float | None = Field(None, examples=[38.0], description="메모리 사용률 (%). Fargate")
    response_time_ms: float | None = Field(
        None, examples=[12.5], description="평균 응답 시간 (ms). Fargate는 로드밸런서 → 앱, Lambda는 처리 시간"
    )
    request_count: int | None = Field(None, examples=[42], description="1분 동안 받은 요청 수. Lambda는 호출 수")
    error_count: int | None = Field(
        None, examples=[0], description="1분 동안 오류 수. Fargate는 로드밸런서가 받은 5xx 응답, Lambda는 함수 오류"
    )
    measured_at: UtcDatetime | None = Field(None, description="가장 최근 값의 시각")


class LogLine(BaseModel):
    at: UtcDatetime
    message: str


class AppLogs(BaseModel):
    """앱 실행 로그 (API 명세 12절). 최근 7일에서 마지막 limit줄, 오래된 것부터."""

    status: MonitoringStatus
    message: str | None = None
    lines: list[LogLine] = []


class TeardownCallback(BaseModel):
    """Destroy 워크플로 → 백엔드 내리기 결과 (API 명세 9-5절)."""

    status: Literal["success", "failed"]
    reason: str | None = Field(None, description="실패 이유")


class DeploymentCreate(BaseModel):
    compute: Compute
    plan_id: str | None = Field(None, max_length=32, description="고른 구성안 (API 명세 8절). 지금은 저장만 한다")


class RedeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_deployment_id: str = Field(min_length=1, max_length=32)
    target_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class RedeployPlan(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    template: str
    values: dict[str, Any]


class RedeployContext(BaseModel):
    app_space_id: str
    repo_url: str
    branch: str
    source_deployment_id: str
    source_commit_sha: str | None
    target_commit_sha: str
    compute: Compute
    plan: RedeployPlan


class Deployment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    app_space_id: str
    compute: str
    status: DeploymentStatus
    url: str | None = None
    reason: str | None = Field(None, description="실패 사유")
    commit_sha: str | None = None
    plan_id: str | None = None
    source_deployment_id: str | None = None
    created_at: UtcDatetime


class DeploymentEvent(BaseModel):
    """SSE로 보내는 진행 이벤트 한 건."""

    model_config = ConfigDict(from_attributes=True)

    status: DeploymentStatus
    step: DeploymentStep
    message: str
    progress: int = Field(ge=0, le=100)
    url: str | None = None
    at: UtcDatetime


class CallbackResource(BaseModel):
    """워크플로가 보내는 자원 하나의 상태 (트리용, workload-deploy scripts/callback.py)."""

    address: str = Field(min_length=1, max_length=500, examples=["aws_lb.app"])
    type: str = Field(min_length=1, max_length=100, examples=["aws_lb"])
    action: str = Field(max_length=20, examples=["create"], description="create / update / replace / delete / no-op")
    state: ResourceState
    reason: str | None = None


# 문구와 이유는 길어도 거절하지 않고 저장할 때 자른다. 거절하면 진행 상황이 통째로 사라진다.
class DeploymentCallback(BaseModel):
    """배포 워크플로 → 백엔드 진행 보고 (API 명세 9-4절)."""

    status: DeploymentStatus
    step: DeploymentStep
    message: str | None = None
    run_id: int | None = Field(None, description="GitHub Actions 실행 ID. 첫 콜백에 온다")
    url: str | None = Field(None, max_length=500, description="성공 시 앱 주소")
    reason: str | None = Field(None, description="실패 이유")
    resources: list[CallbackResource] = Field([], description="자원별 상태. deploy 단계에서만 온다")


class DeploymentResource(BaseModel):
    """트리용 자원 하나. 화면은 type으로 묶어서 보여 준다."""

    model_config = ConfigDict(from_attributes=True)

    address: str
    type: str
    action: str
    state: ResourceViewState
    reason: str | None = None
    updated_at: UtcDatetime
