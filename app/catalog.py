"""배포 템플릿 목록 (ADR-012 Option B).

템플릿 본체(Terraform)는 배포 레포 workload-deploy의 templates/ 아래에 있고, 백엔드는 이름과
채울 값의 범위만 안다. 배포 레포에 템플릿이 생기면 여기에 추가하고 ready를 켠다.
범위는 배포 레포 templates/<이름>/variables.tf와 똑같이 맞춘다 (워크플로도 apply 전에 다시 검사한다).
"""
import re
from dataclasses import dataclass, field
from typing import Any, Callable

PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,254}$")

# Fargate는 cpu마다 고를 수 있는 memory가 정해져 있다 (variables.tf)
FARGATE_MEMORY = {
    256: [512, 1024, 2048],
    512: [1024, 2048, 3072, 4096],
    1024: [2048, 3072, 4096, 5120, 6144, 7168, 8192],
}


def _fargate_values(raw: dict[str, Any]) -> dict[str, Any]:
    port = raw.get("container_port", 80)
    if not (isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
        raise ValueError("container_port는 1~65535 정수여야 합니다.")
    cpu = raw.get("cpu", 256)
    if cpu not in FARGATE_MEMORY:
        raise ValueError("cpu는 256, 512, 1024 중 하나여야 합니다.")
    # memory가 비어 있으면 그 cpu에서 가장 작은 값. 템플릿 기본값(512)은 cpu 256에서만 맞는다
    memory = raw.get("memory", FARGATE_MEMORY[cpu][0])
    if memory not in FARGATE_MEMORY[cpu]:
        raise ValueError(f"cpu {cpu}에서 memory는 {FARGATE_MEMORY[cpu]} 중 하나여야 합니다.")
    path = raw.get("health_check_path", "/")
    if not (isinstance(path, str) and PATH_RE.match(path)):
        raise ValueError("health_check_path는 /로 시작하는 URL 경로여야 합니다.")
    return {"container_port": port, "cpu": cpu, "memory": memory, "health_check_path": path}


@dataclass(frozen=True)
class Template:
    compute: str
    name: str | None  # 배포 레포 templates/ 아래 폴더 이름. 준비 중이면 None
    ready: bool
    fill: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # 화면에 보일 구성안 설명. AI가 값을 채우게 되면 AI 설명으로 바꿀 수 있다
    plan_name: str = ""
    summary: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)


TEMPLATES: dict[str, Template] = {
    "ecs-fargate": Template(
        compute="ecs-fargate",
        name="ecs-fargate/basic",
        ready=True,
        fill=_fargate_values,
        plan_name="기본형",
        summary="Task 1개와 앱 전용 로드밸런서로 시작하는 가장 단순한 구성",
        pros=["서버 관리 없이 컨테이너를 바로 실행", "비용이 가장 적은 크기로 시작"],
        cons=["트래픽이 늘면 크기나 Task 수를 직접 늘려야 함"],
    ),
    # 배포 레포에 템플릿이 생기면 name과 fill을 채우고 ready를 켠다 (4일차 회의: 정호원 님 작성 예정)
    "lambda": Template(compute="lambda", name=None, ready=False),
    "ec2": Template(compute="ec2", name=None, ready=False),
}


def is_ready(compute: str) -> bool:
    template = TEMPLATES.get(compute)
    return bool(template and template.ready)


def fill_values(compute: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """빠진 값은 기본값으로 채우고 범위를 검사한다. 범위를 벗어나면 ValueError."""
    template = TEMPLATES[compute]
    if not template.ready or template.fill is None:
        raise ValueError(f"{compute} 템플릿은 아직 준비 중입니다.")
    return template.fill(raw or {})
