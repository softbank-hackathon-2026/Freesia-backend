"""가짜 데이터 (프론트 연동용).

실제 기능(DB 저장, AI 분석, 배포 파이프라인)이 붙기 전까지 API가 이 값을 돌려준다.
응답 형식은 app/schemas.py를 그대로 따르므로, 실제 기능으로 바꿔도 프론트는 고칠 것이 없다.
저장은 메모리에만 하므로 서버를 재시작하면 사라진다.
"""
from app.ids import new_id, now  # noqa: F401  라우터가 mock_data.new_id / mock_data.now로 쓴다
from app.schemas import Analysis, AppSpace, Candidate, Deployment, Evidence, InfraSpace

INFRA_SPACES: list[InfraSpace] = [
    InfraSpace(
        id="sbh-workload-demo-vpc-public01",
        name="공개 웹 서비스용",
        description="인터넷에서 바로 접속하는 웹 서비스. 퍼블릭 서브넷 + ALB",
        network="public",
        computes=["ecs-fargate", "lambda", "ec2"],
        app_count=2,
    ),
    InfraSpace(
        id="sbh-workload-demo-vpc-private01",
        name="내부 API용",
        description="외부 노출 없이 내부에서만 쓰는 API. 프라이빗 서브넷",
        network="private",
        computes=["ecs-fargate", "lambda"],
        app_count=0,
    ),
    InfraSpace(
        id="sbh-workload-demo-vpc-ha01",
        name="고가용성 서비스용",
        description="멀티 AZ로 장애에 강한 구성",
        network="ha",
        computes=["ecs-fargate", "ec2"],
        app_count=1,
    ),
]

APP_SPACES: dict[str, AppSpace] = {}
DEPLOYMENTS: dict[str, Deployment] = {}
# 배포별로 SSE가 어디까지 보냈는지 (다시 연결하면 이어서 보내기 위함)
PROGRESS: dict[str, int] = {}


def sample_analysis() -> Analysis:
    return Analysis(
        status="done",
        requirements=["Node.js 20 실행 환경", "HTTP 3000 포트", "상시 실행 웹 서비스"],
        evidence=[
            Evidence(file="Dockerfile", finding="컨테이너 이미지로 실행 가능", certain=True),
            Evidence(file="package.json", finding="express 웹 서버, start 스크립트 있음", certain=True),
            Evidence(file="README.md", finding="트래픽이 꾸준할 것으로 보임", certain=False),
        ],
        candidates=[
            Candidate(
                compute="ecs-fargate",
                state="selected",
                reason="Dockerfile이 있고 상시 실행되는 웹 서비스라 컨테이너 방식이 가장 자연스러움",
                cons=["요청이 없어도 최소 비용 발생"],
            ),
            Candidate(
                compute="lambda",
                state="alternative",
                reason="가능하지만 express 서버를 함수 형태로 바꿔야 함",
                cons=["콜드 스타트", "코드 수정 필요"],
            ),
            Candidate(
                compute="ec2",
                state="unsuitable",
                reason="이 규모에서는 서버 관리 부담이 이점보다 큼",
                cons=["OS 패치·스케일링 직접 관리"],
            ),
        ],
        mascot_message="Dockerfile이 있어서 컨테이너로 바로 올릴 수 있어요! Fargate를 추천해요.",
    )


# 배포 진행 시나리오 (status, step, message, progress)
DEPLOY_STEPS = [
    ("pending", "queued", "배포 요청을 받았어요", 0),
    ("building", "checkout", "저장소 코드를 가져오는 중", 10),
    ("building", "image-build", "컨테이너 이미지를 빌드하는 중", 30),
    ("building", "image-push", "이미지를 레지스트리에 올리는 중", 50),
    ("deploying", "terraform-plan", "Terraform plan 실행 중", 60),
    ("deploying", "terraform-apply", "Terraform apply 실행 중", 75),
    ("deploying", "health-check", "앱이 정상 응답하는지 확인하는 중", 90),
    ("success", "done", "배포 완료!", 100),
]
