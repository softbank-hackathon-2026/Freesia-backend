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

    # DB: 서버 운영 방식은 ADR-007에서 결정. 코드는 이 값 하나로만 접속한다.
    database_url: str = "postgresql+psycopg://freesia:freesia@db:5432/freesia"

    # GitHub OAuth App
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8000/auth/github/callback"
    github_oauth_scope: str = "read:user user:email"

    # 로그인 후 돌아갈 프론트 주소
    frontend_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    # 인증 토큰(JWT)
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 12
    auth_cookie_name: str = "access_token"
    cookie_secure: bool = False  # HTTPS 환경(서버)에서는 true

    # GitHub 액세스 토큰 암호화 키 (Fernet, base64 32바이트)
    token_encryption_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
