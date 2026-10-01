"""FastAPI 앱 진입점."""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers import app_spaces, deployments, health, infra_spaces, plans, repositories

logger = logging.getLogger(__name__)

API_PREFIX = "/api"


def create_app() -> FastAPI:
    s = get_settings()
    # 모든 API는 /api 아래에 둔다. 인프라(CloudFront·ALB)가 "/api로 시작하면 백엔드" 한 규칙으로 라우팅한다.
    app = FastAPI(
        title="Freesia Platform API",
        version=s.app_version,
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        # 공통 에러 형식: {"error": "코드", "message": "설명"}
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = exc.detail
        else:
            body = {"error": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            # 직접 만든 검증(ValueError)은 errors()에 예외 객체가 들어 있어 JSON으로 바꿔 준다
            content=jsonable_encoder(
                {"error": "validation_error", "message": "요청 값이 올바르지 않습니다.", "details": exc.errors()}
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 내부 정보는 응답에 넣지 않고 로그에만 남긴다
        logger.exception("unhandled error: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "서버 오류가 발생했습니다."},
        )


    for r in (
        health.router,
        infra_spaces.router,
        repositories.router,
        app_spaces.router,
        deployments.router,
        plans.router,
    ):
        app.include_router(r, prefix=API_PREFIX)
    return app


app = create_app()
