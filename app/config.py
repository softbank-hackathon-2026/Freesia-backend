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

    # AI 분석 (Bedrock, ADR-006·007). 모델·리전은 값만 바꿔 고른다. 자격증명은 두지 않는다 (규칙 9: 서버는 ECS 작업 역할)
    ai_model_id: str = ""
    ai_aws_region: str = "ap-northeast-2"  # 모델마다 열려 있는 리전이 다르다
    ai_timeout_seconds: int = 60  # 모델 응답 대기 상한 (API 명세 7절 3번)
    ai_schema_output: bool = True  # 모델이 JSON 스키마 출력(outputConfig)을 지원하지 않으면 false (예: Nova 2 Lite)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
