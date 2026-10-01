"""배포 워크플로가 부르는 API의 서명 확인 (ADR-009). GitHub Webhook과 같은 X-Hub-Signature-256 방식."""
import hashlib
import hmac

from fastapi import HTTPException

from app.config import get_settings

INVALID_SIGNATURE = {"error": "invalid_signature", "message": "서명이 올바르지 않습니다."}


def verify(message: bytes, signature: str | None) -> None:
    """서명이 `sha256=<message의 HMAC-SHA256 hex>`와 같지 않으면 401. 서버에 키가 없으면 모두 거절한다."""
    secret = get_settings().deploy_callback_secret
    if not secret or not signature:
        raise HTTPException(401, detail=INVALID_SIGNATURE)
    expected = "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, detail=INVALID_SIGNATURE)
