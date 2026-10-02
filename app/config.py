"""애플리케이션 설정.

모든 설정값은 환경변수에서 읽는다. 서버에서는 Parameter Store 값이 환경변수로
주입되고(ADR-002), 로컬에서는 .env 파일을 사용한다. 코드에 비밀값을 적지 않는다.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 앱
    app_env: str = "local"  # local / dev / demo / prod
    app_version: str = "dev"  # 빌드 시 커밋 SHA 주입 (/version.txt)

    # DB: 서버 운영 방식은 ADR-008에서 결정. 코드는 이 값 하나로만 접속한다.
    database_url: str = "postgresql+psycopg://freesia:freesia@db:5432/freesia"

    # 프론트 주소 (쉼표로 여러 개)
    cors_origins: str = "http://localhost:5173"

    # 배포 워크플로 콜백 서명 키 (ADR-009). 서버는 Parameter Store
    # /sbh/platform/demo/backend/DEPLOY_CALLBACK_SECRET 값이 주입된다. 비어 있으면 콜백을 모두 거절한다.
    deploy_callback_secret: str = ""
    # true면 워크플로를 부르지 않고 가짜 진행을 DB에 기록한다 (배포 레포 연결 전까지, API 명세 9-1)
    deploy_simulate: bool = True
    # 배포 레포 워크플로를 실행할 GitHub 토큰 (workflow_dispatch). 서버는 Parameter Store
    # /sbh/platform/demo/backend/GITHUB_DEPLOY_TOKEN 값이 주입된다. 응답·로그에 남기지 않는다 (규칙 7)
    github_deploy_token: str = ""
    deploy_repo: str = "softbank-hackathon-2026/workload-deploy"
    deploy_ref: str = "main"
    # 콜백 주소 앞부분. 배포 레포 deploy.yml의 CALLBACK_BASE와 같아야 워크플로가 받아들인다
    public_api_base: str = "https://sbh.howon.me/api"

    # AI 분석 (Bedrock, ADR-006·007). 모델·리전은 값만 바꿔 고른다. 자격증명은 두지 않는다 (규칙 9: 서버는 ECS 작업 역할)
    ai_model_id: str = ""
    ai_aws_region: str = "ap-northeast-2"  # 모델마다 열려 있는 리전이 다르다
    ai_timeout_seconds: int = 60  # 모델 응답 대기 상한 (API 명세 7절 3번)
    ai_schema_output: bool = True  # 모델이 JSON 스키마 출력(outputConfig)을 지원하지 않으면 false (예: Nova 2 Lite)

    # Workload 계정 키: 배포된 앱의 지표·로그를 읽기만 한다 (모니터링, API 명세 12절). 서버는 Parameter Store
    # /sbh/platform/demo/backend/WORKLOAD_AWS_* 값이 주입된다. AWS_* 이름을 쓰지 않는다: 그러면 Bedrock 호출(작업 역할)이 이 키를 집어 간다
    workload_aws_access_key_id: str = ""
    workload_aws_secret_access_key: str = ""
    workload_aws_region: str = "ap-northeast-2"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
