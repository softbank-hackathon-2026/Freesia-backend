"""분석 때 AI가 함께 채우는 배포 템플릿 값의 양식 (ADR-012 Option B).

컴퓨팅마다 배포 레포 workload-deploy의 templates/<이름>/variables.tf에서 "Filled per app"으로 표시된 변수와
이름을 똑같이 쓴다. 여기에는 AI에게 물을 것(타입, 고를 수 있는 값, 설명)만 두고, 범위 검사와 기본값은
app/catalog.py가 한다. 템플릿이 catalog에 등록된 뒤 여기에 추가한다.
온프레미스 vm은 Terraform이 아니라 배포 레포 ansible/playbooks/deploy.yml이고, 값 이름·범위는 scripts/vm_plan.py
check_values를 따르고, 검사·기본값은 다른 컴퓨팅처럼 catalog(_vm_values)가 한다.
필드 순서는 fit_values가 값을 넣어 보는 순서라, 다른 값에 따라 범위가 바뀌는 값(Fargate memory)을 뒤에 둔다.
enum은 템플릿이 허용하는 값을 그대로 옮길 때만 건다. 범위를 AI 쪽에서 좁히지 않는다 (상한은 catalog·템플릿이 정한다).
"""
import logging
from typing import Any

from app.catalog import EC2_INSTANCE_TYPES, FARGATE_MEMORY, LAMBDA_MIN_PORT, TEMPLATES, VM_JAVA_SERVERS, VM_JAVA_VERSIONS, VM_RUNTIMES

logger = logging.getLogger(__name__)

# AWS Fargate의 cpu별 memory 규칙. 템플릿이 허용하는 cpu(256·512·1024)에서는 catalog 표가 AWS 표와 같다
_MEMORY_TABLE = " / ".join(f"cpu {cpu} → {', '.join(map(str, mems))}" for cpu, mems in FARGATE_MEMORY.items())

# 컴퓨팅마다 같은 설명
_PORT_HOW = "Dockerfile의 EXPOSE → ENV PORT → 코드의 listen 순서로 확인합니다. 코드로 확인하지 못하면 그 프레임워크의 기본 포트를 씁니다."
_PORT = {"type": "integer", "description": f"앱이 컨테이너 안에서 요청을 받는 포트(1~65535 정수). {_PORT_HOW}"}
_HEALTH = {
    "type": "string",
    "description": "배포 뒤 앱 상태를 확인할 HTTP 경로. 2xx·3xx를 돌려준다고 코드에서 확인한 GET 경로(예: /health)만 씁니다. "
    "확인한 경로가 없으면 /입니다.",
}

TEMPLATE_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "ecs-fargate": {
        "container_port": _PORT,
        "health_check_path": _HEALTH,
        "cpu": {
            "type": "integer",
            "enum": list(FARGATE_MEMORY),
            "description": "Fargate CPU 단위(256 = 0.25 vCPU). 기본은 256이고, JVM·머신러닝 라이브러리처럼 무거운 런타임일 때만 키웁니다.",
        },
        "memory": {
            "type": "integer",
            "enum": sorted({m for mems in FARGATE_MEMORY.values() for m in mems}),
            "description": f"Fargate 메모리(MiB). AWS 규칙상 cpu에 따라 고를 수 있는 값이 정해져 있습니다: {_MEMORY_TABLE}. "
            "반드시 고른 cpu의 값 중에서 고르고, 보통은 그중 가장 작은 값을 씁니다.",
        },
    },
    "lambda": {
        "container_port": {
            "type": "integer",
            "description": f"앱이 컨테이너 안에서 요청을 받는 포트({LAMBDA_MIN_PORT}~65535 정수). {_PORT_HOW} "
            f"Lambda는 {LAMBDA_MIN_PORT} 미만 포트를 열 수 없어서, 확인한 포트가 그보다 작으면 앱이 PORT 환경변수를 읽을 때만 8080을 씁니다.",
        },
        "health_check_path": _HEALTH,
        "memory": {
            "type": "integer",
            "description": "Lambda 메모리(MiB, 128~10240 정수). CPU도 메모리에 비례해 커집니다. 기본은 512이고, "
            "JVM·머신러닝 라이브러리처럼 무거운 런타임일 때만 1024~2048로 키웁니다.",
        },
        "timeout": {
            "type": "integer",
            "description": "요청 하나가 실행될 수 있는 최대 시간(초, 1~900 정수). 기본은 30이고, "
            "파일 변환처럼 오래 걸리는 요청을 코드에서 확인했을 때만 늘립니다.",
        },
    },
    "ec2": {
        "container_port": _PORT,
        "health_check_path": _HEALTH,
        "instance_type": {
            "type": "string",
            "enum": list(EC2_INSTANCE_TYPES),
            "description": "EC2 서버 크기. 기본은 t3.micro이고, JVM·머신러닝 라이브러리처럼 무거운 런타임일 때만 t3.small·t3.medium으로 키웁니다.",
        },
    },
    # 온프레미스 VM. Docker 없이 VM(Ubuntu)에 런타임을 apt로 설치하고 소스를 빌드해 systemd로 실행한다.
    # ponytail: env(앱 환경변수)는 뺐다. 열린 키 객체는 스키마 출력에서 막힐 수 있고 비밀값을 AI가 채우면 안 된다
    "vm": {
        "runtime": {
            "type": "string",
            "enum": VM_RUNTIMES,
            "description": "앱 언어. 의존성 파일(requirements.txt·pyproject.toml / package.json / pom.xml·build.gradle)로 정합니다.",
        },
        "app_port": {
            "type": "integer",
            "description": "앱이 요청을 받는 포트(1024~65535 정수). 앱은 일반 사용자로 실행되고 PORT 환경변수로 이 값을 받습니다. "
            "코드의 listen·설정 파일로 확인하고, 코드가 PORT 환경변수를 읽으면 확인한 기본 포트를 그대로 씁니다. "
            "1024 미만이면 앱이 PORT를 읽을 때만 8080을 씁니다. Java Tomcat(java_server=tomcat)이면 8080입니다.",
        },
        "health_check_path": _HEALTH,
        "build_command": {
            "type": "string",
            "description": "앱 폴더에서 한 번 실행할 설치·빌드 명령(한 줄). 예: npm ci && npm run build / "
            "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt / ./mvnw -q package -DskipTests. "
            "필요 없으면 빈 문자열입니다.",
        },
        "start_command": {
            "type": "string",
            "description": "앱을 실행하는 명령(한 줄). systemd가 앱 폴더에서 실행하고 포그라운드로 계속 떠 있어야 합니다. "
            "예: npm start / .venv/bin/gunicorn -b 0.0.0.0:$PORT app:app / java -jar target/app.jar. "
            "build_command에서 만든 가상환경·빌드 결과 경로를 맞춰 씁니다. java_server=tomcat이면 빈 문자열입니다.",
        },
        "runtime_version": {
            "type": "string",
            "enum": VM_JAVA_VERSIONS,
            "description": "Java 버전(pom.xml·build.gradle의 java 버전에 가까운 값). Java가 아니면 21입니다. "
            "Python·Node는 버전을 고를 수 없고 Ubuntu 기본 패키지를 씁니다.",
        },
        "java_server": {
            "type": "string",
            "enum": VM_JAVA_SERVERS,
            "description": "Java 앱을 WAR로 Tomcat에 올릴 때만 tomcat입니다(pom.xml packaging이 war). 그 외에는 none입니다.",
        },
        "war_file": {
            "type": "string",
            "description": "java_server=tomcat일 때 빌드가 만드는 WAR 경로(glob 가능). 그 외에는 target/*.war입니다.",
        },
    },
}


def fit_values(compute: str, raw: Any) -> dict[str, Any]:
    """AI 값을 필드 순서대로 하나씩 넣어 보며 검사(catalog의 템플릿별 검사, AWS 규칙 포함)를 통과하는 것만 남긴다.

    틀린 값 하나 때문에 맞는 값(예: 포트)까지 기본값으로 돌아가지 않게 한다.
    돌려주는 값은 검사를 통과한 AI 값만이고 기본값은 채우지 않는다. 기본값은 구성안을 만들 때 채운다
    (AI가 확인하지 못한 포트는 비워 두어 배포 워크플로가 Dockerfile EXPOSE를 쓰게 하려고).
    """
    raw = raw if isinstance(raw, dict) else {}
    kept: dict[str, Any] = {}
    for name in TEMPLATE_FIELDS[compute]:
        if name not in raw:
            continue
        try:
            # ready와 상관없이 검사한다. ready는 배포 가능 여부라 준비 전(vm)이어도 분석 값은 남긴다
            TEMPLATES[compute].fill({**kept, name: raw[name]})
        except ValueError as e:
            logger.warning("템플릿 값을 버립니다 (%s.%s=%r): %s", compute, name, raw[name], e)
            continue
        kept[name] = raw[name]
    return kept


def fit_all(raw: Any) -> dict[str, dict[str, Any]]:
    """모든 컴퓨팅의 값을 fit_values로 맞춘다. AI가 빠뜨린 컴퓨팅은 빈 값이다."""
    raw = raw if isinstance(raw, dict) else {}
    return {compute: fit_values(compute, raw.get(compute)) for compute in TEMPLATE_FIELDS}


def prompt_guide() -> str:
    """시스템 프롬프트에 붙일 필드 안내. 스키마 출력이 description을 모델에 보여 주는지 몰라서 프롬프트에도 쓴다."""
    lines = [f"- {compute}.{name}: {spec['description']}" for compute, fields in TEMPLATE_FIELDS.items() for name, spec in fields.items()]
    return "template_values 필드 안내:\n" + "\n".join(lines)
