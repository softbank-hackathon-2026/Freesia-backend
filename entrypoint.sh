#!/bin/sh
set -e
# 시작할 때 DB 마이그레이션을 적용한다. 스키마는 컬럼 추가 위주로만 변경 (ADR-002 롤백 호환).
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
