"""FastAPI 앱 진입점."""
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers import auth, health, me


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Freesia Platform API", version=s.app_version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=True,  # 로그인 쿠키 전달
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

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    return app


app = create_app()
