"""가짜 데이터 (프론트 연동용).

실제 기능이 붙기 전까지 API가 이 값을 돌려준다. 지금 남은 것은 AI 분석 결과뿐이다.
응답 형식은 app/schemas.py를 그대로 따르므로, 실제 기능으로 바꿔도 프론트는 고칠 것이 없다.
"""
from app.schemas import Analysis, Candidate, Evidence


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
