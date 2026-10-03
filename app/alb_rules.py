"""공용 ALB에서 앱을 나누는 경로와 리스너 규칙 번호 (Multi-AZ 프론트·백엔드 함께 배포 문서 6절, 10/3 박소정 님 합의).

한 인프라의 공용 ALB 리스너에 앱마다 경로 규칙을 하나씩 붙인다. ALB는 번호가 작은 규칙부터 보므로
`/api` 같은 구체 경로는 100번대, 나머지 전부(`/`)는 1000번대를 준다. 번호는 리스너 안에서 겹치면 안 되고,
한 번 정하면 재배포 때 그대로 쓴다. 삭제한 앱 번호도 다시 쓰지 않는다 (내리기에 실패해 규칙이 남아 있을 수 있다).
배포 템플릿(shared-alb)이 생기면 배포 시작 때 assign_priority를 불러 워크플로에 넘긴다.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

ROOT = "/"
PATH_BASE = 100  # 구체 경로
ROOT_BASE = 1000  # 나머지 전부
# 소문자·숫자·하이픈 조각을 /로 이은 경로. 끝의 /는 붙이지 않는다 (예: /api, /api/v2). 루트는 /
ROUTE_RE = re.compile(r"^/(?:[a-z0-9-]+(?:/[a-z0-9-]+)*)?$")


class RouteConflict(Exception):
    """같은 인프라에서 경로가 겹친다. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def overlaps(a: str, b: str) -> bool:
    """두 경로 중 하나가 다른 하나를 포함하면 겹친다 (/api 와 /api/v2). 루트는 나머지 전부라 겹치지 않는다."""
    if ROOT in (a, b):
        return a == b
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def check_route(db: Session, infra_id: str, route: str, exclude_app_id: str | None = None) -> None:
    """같은 인프라의 살아 있는 앱과 경로가 겹치면 RouteConflict."""
    taken = db.scalars(
        select(models.AppSpace.route_path).where(
            models.AppSpace.infra_id == infra_id,
            models.AppSpace.deleted_at.is_(None),
            models.AppSpace.route_path.is_not(None),
            models.AppSpace.id != (exclude_app_id or ""),
        )
    )
    for other in taken:
        if overlaps(route, other):
            raise RouteConflict(f"이 인프라에서 이미 쓰는 경로({other})와 겹칩니다.")


def default_route(repo_url: str) -> str:
    """경로를 고르지 않은 앱의 경로. 저장소 이름이 -api로 끝나면 /api, 아니면 / (10/3 박태원 결정, 시연용 규칙)."""
    return "/api" if repo_url.rstrip("/").rsplit("/", 1)[-1].lower().endswith("-api") else ROOT


def ensure_route(db: Session, space: models.AppSpace) -> str:
    """앱 경로를 정해 저장하고(flush) 돌려준다. 같은 인프라에서 겹치면 RouteConflict."""
    if space.route_path is None:
        route = default_route(space.repo_url)
        check_route(db, space.infra_id, route, exclude_app_id=space.id)
        space.route_path = route
        db.flush()
    return space.route_path


def path_pattern(route: str) -> str:
    """ALB 경로 규칙. /api → /api/*, / → /*."""
    return "/*" if route == ROOT else f"{route}/*"


def health_path_under(route: str, health: str) -> str:
    """헬스체크 경로는 앱이 맡은 경로 안이어야 한다 (공용 주소로 확인하므로). 밖이면 경로 앞에 붙인다: /health → /api/health."""
    if route == ROOT or health.startswith(route + "/"):
        return health
    return route + health


def assign_priority(db: Session, space: models.AppSpace) -> int:
    """앱의 리스너 규칙 번호. 처음이면 새로 정해 저장하고(flush), 있으면 그대로 돌려준다. commit은 부르는 쪽."""
    if space.alb_rule_priority is not None:
        return space.alb_rule_priority
    route = space.route_path or ROOT
    check_route(db, space.infra_id, route, exclude_app_id=space.id)
    base = ROOT_BASE if route == ROOT else PATH_BASE
    used = db.scalars(
        select(models.AppSpace.alb_rule_priority).where(
            models.AppSpace.infra_id == space.infra_id,
            models.AppSpace.alb_rule_priority >= base,
            models.AppSpace.alb_rule_priority < base * 10,
        )
    ).all()
    space.alb_rule_priority = max(used, default=base - 1) + 1
    db.flush()
    return space.alb_rule_priority
