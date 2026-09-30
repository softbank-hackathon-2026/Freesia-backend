# AGENTS.md — Freesia 플랫폼 백엔드

이 문서는 이 레포에서 작업하는 AI 에이전트(Claude Code 등)와 팀원을 위한 작업 규칙이다.
배경과 논의 과정 전체는 `docs/handoff.md`에 있다. 규칙과 handoff가 충돌하면 이 문서를 따른다.

## 1. 프로젝트 한 줄

인프라 관리자가 미리 만든 인프라(인프라 Space) 중 하나를 개발자가 고르고, AI가 코드를 분석해 배포 방식 후보와 선택 이유(트리 + 캐릭터 설명)를 보여준 뒤 원클릭으로 배포하는 플랫폼. (SoftBank Hackathon 2026, 팀 Freesia, ADR-001: Option C + E)

## 2. 백엔드의 역할

백엔드는 파트를 잇는 허브다. 직접 분석하거나 배포하지 않는다.

- Space·분석 결과·배포 이력 저장 (**회원·로그인 없음**, 모든 방문자가 같은 목록을 본다)
- 프론트에 API 제공
- AI 파트에 분석 요청 → 결과 저장
- CI/CD 파이프라인에 배포 요청 → 결과(상태·URL) 수신
- 인프라 Space는 **조회만** 제공 (생성·수정 기능 만들지 않음)

## 3. 기술 스택

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, pydantic-settings
- DB: PostgreSQL 16 (로컬은 Compose). 서버 운영 방식은 ADR-008에서 결정 전
- 인증: 없음 (로그인 제외, 아래 7절)
- 테스트: pytest (SQLite 메모리 DB)

## 4. 폴더 구조

```
app/
  main.py          앱 생성, CORS, 공통 에러 형식
  config.py        환경변수 설정 (Settings)
  db.py            DB 연결 (DATABASE_URL 하나로 접속)
  schemas.py       API 요청·응답 형식 (프론트·AI와 맞추는 계약)
  mock_data.py     가짜 데이터. 실제 기능이 붙으면 하나씩 대체한다
  models/          SQLAlchemy 모델 (아직 없음)
  routers/         API (health, infra_spaces, app_spaces, deployments)
alembic/           마이그레이션
tests/             pytest
docs/handoff.md    지금까지의 논의·결정 정리
```

## 5. 명령어

```bash
docker compose up --build      # 로컬 실행 (백엔드 + Postgres)
pytest                         # 테스트 (PR 전 반드시 통과)
alembic revision --autogenerate -m "설명"
alembic upgrade head
```

## 6. 반드시 지킬 규칙

1. **설정·비밀값은 전부 환경변수.** 코드·compose·문서에 비밀번호, 키, 토큰을 쓰지 않는다. `.env`는 커밋하지 않고 `.env.example`만 갱신한다. 서버 값은 Parameter Store에서 주입된다 (ADR-002).
2. **`/health`와 `/version.txt`를 유지한다.** CI/CD 배포 성공 판정에 쓰인다 (ADR-002). `/version.txt`는 `APP_VERSION`(빌드 시 `GIT_SHA`)을 그대로 반환한다.
3. **스키마는 Alembic 마이그레이션으로만 변경하고, 컬럼 추가 위주로 한다.** 컬럼 삭제·이름 변경 금지. 롤백 시 DB는 되돌아가지 않기 때문이다 (ADR-002).
4. **DB 접속은 `DATABASE_URL` 하나로만.** 특정 DB(Compose/RDS/SQLite)에 묶이는 코드를 쓰지 않는다 (ADR-008 결정 전).
5. **ID는 소문자·숫자·하이픈.** 앱·배포 ID가 AWS 태그(`ApplicationId`, `DeploymentId`)로 쓰일 수 있다 (ADR-005). 현재 UUID4 문자열 사용.
6. **에러 응답 형식 통일:** `{"error": "코드", "message": "설명"}`. `HTTPException(detail={"error": ..., "message": ...})`로 던진다.
7. **GitHub은 읽기만.** 고객 레포는 public만 대상으로 하고, 서버용 GitHub 토큰은 읽기 전용으로 환경변수에 둔다. 응답에 절대 포함하지 않는다.
8. **로그인·회원 기능을 다시 넣지 않는다.** 제외하기로 했다 (아래 7절). 다시 필요하면 ADR부터 갱신한다.
9. **AWS 자격증명을 백엔드에 두지 않는다.** 배포 실행과 Terraform plan/apply는 GitHub Actions에서, 키는 레포 시크릿에 (2일차 회의 32:55~33:23). 백엔드가 AWS SDK로 직접 자원을 만드는 방식은 합의 전 금지.
10. **새 기능에는 테스트를 같이 추가**하고 `pytest` 통과를 확인한다.
11. 트레이드오프가 있는 결정은 Notion Docs & Logs에 ADR로 남긴다 (한 ADR에 질문 하나).

## 7. 결정 상태 (2026-09-30 기준)

| 항목 | 상태 | 근거 |
|---|---|---|
| 주제: C(Space 분리) + E(선택 이유 시각화) | 확정 | ADR-001 |
| AWS 계정 4분리 (Management/Platform/Workload/Sandbox), 플랫폼 DB·인프라 목록은 Platform 계정 | 확정 | ADR-004 |
| 인프라는 사전 구축, 플랫폼은 조회만 | 회의 합의 | 2일차 2:27:19 |
| 로그인 없음 (공용 목록 + public 레포 URL 입력). 2일차 합의(GitHub OAuth)를 보류 | 초안 | ADR-011 |
| 트리 시각화 유지, 사용자는 구성 요소 수정 불가 | 회의 합의 | 2일차 1:35:34 |
| AI는 Terraform만 작성, 실행은 GitHub Actions, 키는 레포 시크릿 | 회의 합의 | 2일차 32:55 |
| 플랫폼 CI/CD: Actions → ECR → S3 → SSM → EC2 Compose | 검토 중 | ADR-002 |
| 샘플 인프라 / 컴퓨팅 후보 (Fargate·Lambda·EC2 vs Public·Private·HA) | 검토 중 | ADR-003 |
| AWS 리소스 네이밍·태깅 | 제안 | ADR-005 |
| LLM 모델·호출 방식 (Bedrock) | 초안 | ADR-006 |
| 플랫폼 DB 서버 운영 방식 | 초안 (Decision 비움) | ADR-008 |

## 8. 미정 — 합의 전에는 구현하지 말 것

- **서버용 GitHub 토큰**: 누구 계정으로 발급할지, Parameter Store 키 이름 (플랫폼 인프라 담당과 협의)
- **비용 보호**: 동시 배포 1개 제한, 허용 레포 목록, 하루 횟수 상한 중 무엇을 적용할지
- **고객 앱 배포 파이프라인**: 담당자·방식 미정 (Work Board "CI/CD 파이프라인 - 대상 서비스"). 후보: 백엔드 → GitHub Actions API(`workflow_dispatch`) + 콜백 (백엔드 추천안) / Jenkins / 백엔드 AWS SDK 직접
- **AI 연동 방식**: AI 파트(강효승)와 요청·응답 JSON 형식 합의 필요
- **컴퓨팅 후보 목록**: ADR-003 결론 후 `compute` 값 확정
- **`/health`에 DB 확인 포함 여부**: 박소정과 합의 (현재 `/health`=앱만, `/health/db`=DB 포함)

## 9. 다음 작업 (백로그)

API 모양은 `app/schemas.py`에 있고, 지금은 `app/mock_data.py`의 가짜 데이터로 응답한다. 아래 순서로 실제 기능으로 바꾼다. 응답 모양은 유지한다.

1. 앱 Space DB 저장 (생성·조회·목록, 레포 URL, 선택 인프라)
2. 인프라 Space 조회 (Platform DB의 인프라 목록, `InfraId` 기준)
3. 레포 URL로 주요 파일 읽기 (public 레포, 서버 토큰)
4. AI 분석 요청·결과(추천안, 판단 근거, 트리) 저장·조회
5. 배포 요청 전달 + 콜백 수신 + SSE 진행 상황 전달
6. 모니터링 조회 API (인프라 Space: 올라간 앱 목록 / 앱 Space: 메트릭·로그)

인터페이스 초안은 `docs/handoff.md`의 "인터페이스 초안" 참고.

## 10. 참고 링크

- 팀 Notion: https://app.notion.com/p/3e68bee9ada4815793bfff81e764c01e
- ADR-001 https://app.notion.com/p/7658bee9ada48234952901e78652231a
- ADR-002 https://app.notion.com/p/3ea8bee9ada4803ab658fe7ea8966982
- ADR-003 https://app.notion.com/p/3ea8bee9ada4802da4d3f8f8821632f6
- ADR-004 https://app.notion.com/p/3ea8bee9ada4803a9a35e5240dfc3e4a
- ADR-005 https://app.notion.com/p/3eb8bee9ada4804983b7c222d1202e00
- ADR-006 https://app.notion.com/p/3eb8bee9ada481a0b534d72e302dafcb
- ADR-008 https://app.notion.com/p/3eb8bee9ada48001ad32ff312b10494a
- 2일차 회의록 https://app.notion.com/p/3ea8bee9ada480739d8bf239a4d49199
- Slack: #term1_team_freesia
