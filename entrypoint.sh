#!/bin/sh
set -e
# RUN_MIGRATIONS=true(기본값, 로컬)이면 시작할 때 DB 마이그레이션을 적용한다.
# 서버는 RUN_MIGRATIONS=false로 두고 배포 단계의 일회성 Task에서 한 번만 실행한다 (ADR-013).
# 스키마는 컬럼 추가 위주로만 변경한다 (롤백 호환).
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
