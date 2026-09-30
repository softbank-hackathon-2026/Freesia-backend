# Freesia Platform Backend

원클릭 배포 플랫폼의 백엔드 (FastAPI). 에이전트용 작업 규칙은 `AGENTS.md`, 지금까지의 논의·결정 정리는 `docs/handoff.md` 참고.

## 빠른 시작 (로컬)

```bash
cp .env.example .env
docker compose up --build
```

- API 문서(Swagger): http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health
- 버전: http://localhost:8000/version.txt

지금 인프라·앱 Space·분석·배포 API는 가짜 데이터(`app/mock_data.py`)로 응답한다. 서버를 재시작하면 만든 데이터는 사라진다.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

테스트는 SQLite 메모리 DB를 사용하므로 Postgres 없이 돌아간다.

## DB 마이그레이션

```bash
alembic revision --autogenerate -m "설명"   # 모델 변경 후 마이그레이션 생성
alembic upgrade head                        # 적용 (컨테이너 시작 시 자동 실행)
```

스키마는 컬럼 추가 위주로만 변경한다. 삭제·이름 변경은 해커톤 기간에 피한다 (ADR-002 롤백 호환).

## 프론트 연동 메모

- 로그인이 없다. 모든 API를 인증 없이 호출한다.
- 흐름: `GET /infra-spaces` → `POST /app-spaces` → `GET /app-spaces/{id}/analysis` → `POST /app-spaces/{id}/deployments` → `GET /deployments/{id}/events`
- 배포 진행 상황은 SSE다. `new EventSource(url)`로 연결하고 `progress` 이벤트를 받는다. 마지막 이벤트의 `status`는 `success` 또는 `failed`.
- 에러 응답 형식: `{"error": "코드", "message": "설명"}`

## CI/CD (ADR-002) 연동 정보

| 항목 | 값 |
|---|---|
| 테스트 명령 | `pytest` (이 폴더에서) |
| Dockerfile | 이 폴더의 `Dockerfile` |
| 커밋 SHA 주입 | `docker build --build-arg GIT_SHA=<sha> .` → `/version.txt`에 표시 |
| 헬스체크 | `GET /health` (앱만), `GET /health/db` (DB 포함) |
| 시작 명령 | `entrypoint.sh` (마이그레이션 적용 후 uvicorn 실행) |
| 설정 | 전부 환경변수. 서버에서는 Parameter Store 값을 주입 |
