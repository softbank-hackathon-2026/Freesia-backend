# Freesia Platform Backend

원클릭 배포 플랫폼의 백엔드 (FastAPI). 에이전트용 작업 규칙은 `AGENTS.md`, 지금까지의 논의·결정 정리는 `docs/handoff.md` 참고.

## 빠른 시작 (로컬)

```bash
cp .env.example .env
# .env에서 GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET / JWT_SECRET / TOKEN_ENCRYPTION_KEY 채우기
docker compose up --build
```

- API 문서(Swagger): http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health
- 버전: http://localhost:8000/version.txt
- 로그인: http://localhost:8000/auth/github → 로그인 후 `FRONTEND_URL`로 이동 → http://localhost:8000/me

## GitHub OAuth App 등록

1. GitHub 조직 설정 → Developer settings → OAuth Apps → New OAuth App
2. Homepage URL: `http://localhost:5173`
3. Authorization callback URL: `http://localhost:8000/auth/github/callback`
4. 발급된 Client ID / Client Secret을 `.env`에 입력
5. 서버 배포 시에는 도메인 기준 콜백 주소로 별도 OAuth App을 등록 (OAuth App은 콜백 1개)

권한은 최소 권한 `read:user user:email`만 요청한다. 레포 접근 범위(public만 / private까지)는 미정.

## 키 생성

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"                               # JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TOKEN_ENCRYPTION_KEY
```

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

테스트는 SQLite 메모리 DB와 가짜 GitHub 응답을 사용하므로 Postgres·GitHub 없이 돌아간다.

## DB 마이그레이션

```bash
alembic revision --autogenerate -m "설명"   # 모델 변경 후 마이그레이션 생성
alembic upgrade head                        # 적용 (컨테이너 시작 시 자동 실행)
```

스키마는 컬럼 추가 위주로만 변경한다. 삭제·이름 변경은 해커톤 기간에 피한다 (ADR-002 롤백 호환).

## 프론트 연동 메모

- 로그인 후 인증 토큰은 HttpOnly 쿠키(`access_token`)로 발급된다.
- 프론트에서 API 호출 시 `fetch(url, { credentials: "include" })` 필요.
- `Authorization: Bearer <token>` 헤더도 지원한다.
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
