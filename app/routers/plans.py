"""배포 워크플로가 plan_id로 템플릿·값·인프라를 받아 가는 API (API 명세 8-1절, ADR-009). 프론트는 부르지 않는다."""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import catalog, models, signing
from app.db import get_db
from app.schemas import PlanInfra, WorkflowPlan

router = APIRouter(prefix="/plans", tags=["plans"])


# 값이 없는 칸은 뺀다. 워크플로는 aws_account_id가 비어 있으면 거절하고, 없으면 Workload로 배포한다 (배포 레포 plan.py)
@router.get("/{plan_id}", response_model=WorkflowPlan, response_model_exclude_none=True, summary="구성안 값 (워크플로 전용)")
def get_plan_for_workflow(
    plan_id: str,
    signature: str | None = Header(None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
) -> WorkflowPlan:
    """헤더 `X-Hub-Signature-256: sha256=<plan_id 문자열의 HMAC-SHA256 hex>`가 맞아야 준다 (콜백과 같은 키).

    VPC·서브넷은 DB의 인프라 값이다. 배포 레포 spaces/ 파일을 대신한다 (박소정 님 10/2).
    """
    # 서명이 맞기 전에는 구성안이 있는지도 알려 주지 않는다
    signing.verify(plan_id.encode(), signature)
    plan = db.get(models.Plan, plan_id)
    if plan is None:
        raise HTTPException(404, detail={"error": "plan_not_found", "message": "구성안을 찾을 수 없습니다."})
    infra = db.get(models.InfraSpace, db.get(models.AppSpace, plan.app_space_id).infra_id)
    if plan.compute == "vm":
        # 온프레미스: VM 주소만 넘긴다 (배포 레포 ansible/README.md "인프라 Space가 주는 값")
        return WorkflowPlan(id=plan.id, template=plan.template, values=plan.values,
                            infra=PlanInfra(id=infra.id, vm_host=infra.vm_host))
    shared = plan.template == catalog.SHARED_ALB.name
    return WorkflowPlan(
        id=plan.id,
        template=plan.template,
        values=plan.values,
        infra=PlanInfra(
            id=infra.id,
            vpc_id=infra.vpc_id,
            public_subnet_ids=infra.public_subnet_ids or [],
            # shared-alb는 앱을 프라이빗 서브넷에 둔다. db 서브넷에 뜨지 않게 앱 서브넷만 넘긴다
            private_subnet_ids=(infra.app_subnet_ids if shared else infra.private_subnet_ids) or [],
            aws_account_id=infra.aws_account_id or None,
            alb_listener_arn=infra.alb_listener_arn,
            alb_security_group_id=infra.alb_security_group_id,
            alb_base_url=infra.alb_base_url,
        ),
    )
