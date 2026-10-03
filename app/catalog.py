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


# Lambda는 root가 아니라 1024 미만 포트를 열 수 없다 (배포 레포 templates/lambda/basic/README.md)
LAMBDA_MIN_PORT = 1024
EC2_INSTANCE_TYPES = ["t3.micro", "t3.small", "t3.medium"]


def _port(raw: dict[str, Any], default: int, low: int = 1) -> int:
    port = raw.get("container_port", default)
    if not (isinstance(port, int) and not isinstance(port, bool) and low <= port <= 65535):
        raise ValueError(f"container_port는 {low}~65535 정수여야 합니다.")
    return port


def _int_in(raw: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    v = raw.get(name, default)
    if not (isinstance(v, int) and not isinstance(v, bool) and low <= v <= high):
        raise ValueError(f"{name}는 {low}~{high} 정수여야 합니다.")
    return v


def _path(raw: dict[str, Any]) -> str:
    path = raw.get("health_check_path", "/")
    if not (isinstance(path, str) and PATH_RE.match(path)):
        raise ValueError("health_check_path는 /로 시작하는 URL 경로여야 합니다.")
    return path


def _fargate_values(raw: dict[str, Any]) -> dict[str, Any]:
    port = _port(raw, 80)
    cpu = raw.get("cpu", 256)
    if cpu not in FARGATE_MEMORY:
        raise ValueError("cpu는 256, 512, 1024 중 하나여야 합니다.")
    # memory가 비어 있으면 그 cpu에서 가장 작은 값. 템플릿 기본값(512)은 cpu 256에서만 맞는다
    memory = raw.get("memory", FARGATE_MEMORY[cpu][0])
    if memory not in FARGATE_MEMORY[cpu]:
        raise ValueError(f"cpu {cpu}에서 memory는 {FARGATE_MEMORY[cpu]} 중 하나여야 합니다.")
    return {"container_port": port, "cpu": cpu, "memory": memory, "health_check_path": _path(raw)}


def _lambda_values(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "container_port": _port(raw, 8080, LAMBDA_MIN_PORT),
        "memory": _int_in(raw, "memory", 512, 128, 10240),
        "timeout": _int_in(raw, "timeout", 30, 1, 900),
        "health_check_path": _path(raw),
    }


def _ec2_values(raw: dict[str, Any]) -> dict[str, Any]:
    instance_type = raw.get("instance_type", "t3.micro")
    if instance_type not in EC2_INSTANCE_TYPES:
        raise ValueError(f"instance_type은 {', '.join(EC2_INSTANCE_TYPES)} 중 하나여야 합니다.")
    return {"container_port": _port(raw, 80), "instance_type": instance_type, "health_check_path": _path(raw)}


# 온프레미스 VM (배포 레포 ansible/playbooks/deploy.yml, 10/3 박소정 님). 범위·기본값은 scripts/vm_plan.py check_values와 같다
VM_RUNTIMES = ["python", "node", "java"]
VM_JAVA_VERSIONS = ["17", "21"]
VM_JAVA_SERVERS = ["none", "tomcat"]


def _one_of(raw: dict[str, Any], name: str, default: Any, allowed: list[Any]) -> Any:
    v = raw.get(name, default)
    if v not in allowed:
        raise ValueError(f"{name}는 {', '.join(map(str, allowed))} 중 하나여야 합니다.")
    return v


def _line(raw: dict[str, Any], name: str, default: str) -> str:
    v = raw.get(name, default)
    if not (isinstance(v, str) and "\n" not in v and len(v) <= 500):
        raise ValueError(f"{name}는 500자 이하 한 줄 문자열이어야 합니다.")
    return v


def _vm_values(raw: dict[str, Any]) -> dict[str, Any]:
    """runtime·start_command는 기본값이 없어서 비어 있어도 통과시키고(AI 값을 칸마다 넣어 보는 fit_values 때문),
    구성안을 만들 때 vm_missing으로 막는다. env·tomcat_version은 AI가 채우지 않아 배포 레포 기본값에 맡긴다."""
    runtime = raw.get("runtime")
    if runtime is not None and runtime not in VM_RUNTIMES:
        raise ValueError(f"runtime은 {', '.join(VM_RUNTIMES)} 중 하나여야 합니다.")
    return {
        "runtime": runtime,
        "app_port": _int_in(raw, "app_port", 8080, 1024, 65535),  # 앱은 일반 사용자로 실행된다
        "health_check_path": _path(raw),
        "build_command": _line(raw, "build_command", ""),
        "start_command": _line(raw, "start_command", ""),
        "runtime_version": _one_of(raw, "runtime_version", "21", VM_JAVA_VERSIONS),
        "java_server": _one_of(raw, "java_server", "none", VM_JAVA_SERVERS),
        "war_file": _line(raw, "war_file", "target/*.war"),
    }


def vm_missing(values: dict[str, Any]) -> list[str]:
    """VM 배포에 꼭 필요한데 기본값이 없는 값. 비어 있지 않으면 구성안을 만들 수 없다."""
    missing = [] if values.get("runtime") else ["runtime"]
    tomcat = values.get("runtime") == "java" and values.get("java_server") == "tomcat"
    if not values.get("start_command") and not tomcat:
        missing.append("start_command")
    return missing


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
    # 10/2 박소정 님 추가. 둘 다 Space의 퍼블릭 서브넷(EC2) 또는 VPC 밖(Lambda)에 앱 전용 입구를 만든다
    "lambda": Template(
        compute="lambda",
        name="lambda/basic",
        ready=True,
        fill=_lambda_values,
        plan_name="기본형",
        summary="컨테이너 이미지를 Lambda로 실행하고 함수 주소(function URL)로 바로 공개하는 구성",
        pros=["요청이 없으면 비용이 거의 없음", "서버·로드밸런서 관리 없음"],
        cons=["오래 쉬다 첫 요청이 오면 느림(콜드 스타트)", "앱이 1024 이상 포트를 써야 함"],
    ),
    "ec2": Template(
        compute="ec2",
        name="ec2/basic",
        ready=True,
        fill=_ec2_values,
        plan_name="기본형",
        summary="퍼블릭 서브넷의 EC2 서버 1대에서 Docker로 앱을 실행하는 구성",
        pros=["서버를 직접 들여다볼 수 있음", "작은 서버로 시작해 비용이 예측 가능"],
        cons=["서버 1대라 장애나 재배포 때 잠깐 멈춤", "재배포하면 주소가 바뀜"],
    ),
    # 10/3 온프레미스. Terraform이 아니라 배포 레포 deploy-vm.yml(Ansible)이 실행한다. 이름은 폴더가 아니라 구분용.
    "onprem": Template(
        compute="onprem",
        name="onprem",
        ready=True,
        fill=_vm_values,
        plan_name="기본형",
        summary="온프레미스 VM에 언어 런타임을 설치하고 소스를 빌드해 서비스(systemd)로 실행하는 구성",
        pros=["Dockerfile 없이 소스 그대로 배포", "사내 서버에 그대로 올라가 데이터가 밖으로 나가지 않음"],
        cons=["Python·Node·Java만 지원하고 Python·Node 버전은 고를 수 없음", "VM 1대라 장애나 재배포 때 잠깐 멈춤"],
    ),
}


# Multi-AZ처럼 공용 ALB가 있는 인프라의 Fargate (배포 레포 templates/ecs-fargate/shared-alb, 10/3 박소정 님).
# AI가 채우는 값은 basic과 같고, 백엔드가 path_pattern·rule_priority를 더 넣는다 (app/alb_rules.py)
SHARED_ALB = Template(
    compute="ecs-fargate",
    name="ecs-fargate/shared-alb",
    ready=True,
    fill=_fargate_values,
    plan_name="공용 ALB형",
    summary="인프라의 공용 ALB(HTTPS) 뒤에 경로로 붙고, 앱은 프라이빗 서브넷에서 실행하는 구성",
    pros=["로드밸런서를 새로 만들지 않아 배포가 빠름", "공용 HTTPS 주소 하나로 여러 앱을 경로로 나눔", "앱이 인터넷에 직접 노출되지 않음"],
    cons=["앱 코드가 맡은 경로(예: /api)로 응답해야 함", "프라이빗 서브넷이라 NAT가 켜져 있어야 이미지를 받음"],
)


def template_for(compute: str, infra: Any) -> Template:
    """인프라에 맞는 템플릿. 공용 ALB와 앱 서브넷 2개 이상이 있는 인프라의 Fargate는 shared-alb, 나머지는 기본."""
    if compute == "ecs-fargate" and infra is not None and infra.alb_listener_arn and len(infra.app_subnet_ids or []) >= 2:
        return SHARED_ALB
    return TEMPLATES[compute]


def is_ready(compute: str) -> bool:
    template = TEMPLATES.get(compute)
    return bool(template and template.ready)


def fill_values(compute: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """빠진 값은 기본값으로 채우고 범위를 검사한다. 범위를 벗어나면 ValueError."""
    template = TEMPLATES[compute]
    if not template.ready or template.fill is None:
        raise ValueError(f"{compute} 템플릿은 아직 준비 중입니다.")
    return template.fill(raw or {})
