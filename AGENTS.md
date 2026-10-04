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
- DB: PostgreSQL 16. 로컬(개발·테스트)은 Compose, 서버는 Amazon RDS Multi-AZ (ADR-008)
- 서버 실행: ECS Fargate, Task Definition은 `.aws/task-definition.json` (ADR-002, ADR-013)
- 인증: 없음 (로그인 제외, 아래 7절)
- 테스트: pytest (SQLite 메모리 DB)

## 4. 폴더 구조

```
app/
  main.py          앱 생성, CORS, 공통 에러 형식
  config.py        환경변수 설정 (Settings)
  db.py            DB 연결 (DATABASE_URL 하나로 접속)
  schemas.py       API 요청·응답 형식 (프론트·AI와 맞추는 계약)
  mock_data.py     가짜 데이터. 지금은 AI 분석 결과만 남았다
  ids.py           ID·시각 생성
  deploy.py        배포 진행 기록 (콜백과 가짜 진행이 함께 씀)
  analysis.py      AI 분석 실행·저장 (백그라운드, 멈춤 방지). AI 호출은 ai/
  catalog.py       배포 템플릿 목록 (배포 레포 templates/와 이름·값 범위를 맞춘다, ready 스위치)
  signing.py       워크플로가 부르는 API의 서명 확인 (X-Hub-Signature-256)
  aws.py           Workload·Sandbox 계정 boto3 클라이언트 (WORKLOAD_AWS_*·SANDBOX_AWS_* 키, 읽기만)
  infra_sync.py    인프라 갱신: 출처(AWS Workload·Sandbox, 온프레미스 Proxmox)를 읽어 infra_spaces를 채움 (provider 기록)
  onprem.py        Proxmox API 클라이언트: Cloudflare Access를 거쳐 freesia 태그 VM을 읽음 (읽기만)
  alb_rules.py     공용 ALB에서 앱을 나누는 경로·리스너 규칙 번호 (Multi-AZ 프론트·백엔드 배포, 템플릿 연결 전)
  monitoring.py    배포된 앱의 지표·로그 조회 (앱이 있는 계정의 CloudWatch 읽기만)
  github.py        GitHub 호출: 배포할 커밋 확인(토큰 없이), 배포 레포 워크플로 실행(workflow_dispatch)
  ai/              AI 분석: 저장소 읽기(repo.py), 모델 호출·검증(analyze.py). 담당 강효승
  models/          SQLAlchemy 모델 (repositories, infra_spaces, app_spaces, analyses, plans, deployments)
  routers/         API (health, infra_spaces, repositories, app_spaces, deployments, plans, monitoring)
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
2. **모든 API는 `/api` 아래에 둔다.** 인프라가 `/api`로 시작하는 요청만 백엔드로 보낸다. **`/api/health`, `/api/health/db`, `/api/version.txt`를 유지한다.** CI/CD 배포 성공 판정과 Target Group 헬스체크에 쓰인다 (ADR-002, ADR-013). `/version.txt`는 `APP_VERSION`(빌드 시 `GIT_SHA`)을 그대로 반환한다.
3. **스키마는 Alembic 마이그레이션으로만 변경하고, 컬럼 추가 위주로 한다.** 컬럼 삭제·이름 변경 금지. 롤백 시 DB는 되돌아가지 않기 때문이다 (ADR-002).
4. **DB 접속은 `DATABASE_URL` 하나로만.** 특정 DB(Compose/RDS/SQLite)에 묶이는 코드를 쓰지 않는다. 서버 값은 Parameter Store `/sbh/platform/demo/backend/DATABASE_URL` (ADR-008).
   마이그레이션은 `RUN_MIGRATIONS`로 켜고 끈다. 로컬 기본값 `true`, 서버는 `false`로 두고 배포 단계에서 한 번 실행한다 (ADR-013).
5. **ID는 소문자·숫자·하이픈.** 앱·배포 ID가 AWS 태그(`ApplicationId`, `DeploymentId`)로 쓰일 수 있다 (ADR-005). 현재 UUID4 문자열 사용.
6. **에러 응답 형식 통일:** `{"error": "코드", "message": "설명"}`. `HTTPException(detail={"error": ..., "message": ...})`로 던진다.
7. **GitHub은 읽기만.** 고객 레포는 public만 대상으로 하고, 서버용 GitHub 토큰은 읽기 전용으로 환경변수에 둔다. 응답에 절대 포함하지 않는다.
8. **로그인·회원 기능을 다시 넣지 않는다.** 제외하기로 했다 (아래 7절). 다시 필요하면 ADR부터 갱신한다.
9. **AWS 자격증명을 백엔드에 두지 않는다.** 배포 실행과 Terraform plan/apply는 GitHub Actions에서, 키는 레포 시크릿에 (2일차 회의 32:55~33:23). 백엔드가 AWS SDK로 직접 자원을 만드는 방식은 합의 전 금지.
10. **새 기능에는 테스트를 같이 추가**하고 `pytest` 통과를 확인한다.
11. 트레이드오프가 있는 결정은 Notion Docs & Logs에 ADR로 남긴다 (한 ADR에 질문 하나).

## 7. 결정 상태 (2026-10-04 기준)

| 항목 | 상태 | 근거 |
|---|---|---|
| 주제: C(Space 분리) + E(선택 이유 시각화) | 확정 | ADR-001 |
| AWS 계정 4분리 (Management/Platform/Workload/Sandbox), 플랫폼 DB·인프라 목록은 Platform 계정 | 확정 | ADR-004 |
| 인프라는 사전 구축, 플랫폼은 조회만 (AWS Workload·Sandbox VPC, 온프레미스 Proxmox VM) | 회의 합의 | 2일차 2:27:19 |
| 로그인 없음 (공용 목록 + public 레포 URL 입력) | 초안 | ADR-011 |
| 트리 시각화 유지, 사용자는 구성 요소 수정 불가 | 회의 합의 | 2일차 1:35:34 |
| 고객 앱 배포: 백엔드 → `workflow_dispatch` → 배포 레포(workload-deploy) 워크플로 → 서명 콜백 | 확정 | ADR-009 |
| AI는 템플릿을 고르고 값만 채움 (Terraform 파일을 쓰지 않음), 백엔드가 값 범위 검사 | 확정 | ADR-012 Option B |
| 컴퓨팅: AWS `ecs-fargate`·`lambda`·`ec2`, 온프레미스 `onprem`·`onprem-container` | 확정 | ADR-020, 10/3~10/4 슬랙 |
| 온프레미스: Proxmox VM, Cloudflare Access로 연결, 배포는 Ansible(`deploy-vm.yml`) | 확정 | 10/3 슬랙 (박준서·박소정 님) |
| 인프라를 고르지 않으면 기본 인프라(`DefaultInfra=true` 태그, 지금 Sandbox), 목록에서는 숨김 | 회의 합의 | 10/3 슬랙 |
| AI 모델: Bedrock `global.moonshotai.kimi-k3` (서울) | 확정 | ADR-006 |
| 플랫폼 CI/CD: Actions → ECR → ECS Fargate | 확정 | ADR-002 |
| 백엔드 Task Definition 구성, 마이그레이션은 배포 단계 일회성 Task, Task 2개 | 확정 | ADR-013 |
| AWS 리소스 네이밍·태깅 | 제안 | ADR-005 |
| 플랫폼 DB: Amazon RDS for PostgreSQL (Multi-AZ) | 확정 | ADR-008 |
| API 경로: 모든 API를 `/api` 아래에 둠 | 허들 합의 (정호원 님) | ADR-013 |

## 8. 미정 — 합의 전에는 구현하지 말 것

- **비용 보호**: 로그인이 없어 누구나 배포할 수 있다. 동시 배포 수 제한, 허용 레포 목록, 하루 횟수 상한 중 무엇을 적용할지
- **분석용 GitHub 읽기 토큰**: 없으면 서버 IP당 시간당 약 30번 분석. 누구 계정으로 발급할지
- **Workload 키 권한 축소**: 지금 AdministratorAccess(코드는 읽기만 부름). 읽기 전용으로 줄이는 방식 (정호원 님)
- **온프레미스 VM 여러 대**: 지금 `ONPREM_VM_HOST` 하나라 서비스 VM 1대 전제. VM마다 주소를 받는 규칙

## 9. 다음 작업 (백로그)

API 모양은 `app/schemas.py`, 테이블 설계는 `docs/erd.md`, 진행 상황은 README "진행 상황"에 있다.

1. 배포 30분 시간 초과 (마지막 콜백이 유실되면 지금은 "진행 중"으로 남음)
2. AI 응답이 검사 규칙(V1~V9)에 걸리면 한 번 더 묻기 (강효승 님과)
3. 온프레미스 모니터링 (Proxmox API의 VM CPU·메모리)
4. 인프라·앱에 "지금 떠 있는지" 표시 (`live_app_count`, `is_live`)
5. 프론트 통합 화면이 저장소 API에 연결되면 등록된 저장소만 받기 (`400 repository_not_registered`)

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
- ADR-011 https://app.notion.com/p/3eb8bee9ada481288ddacd97a6fa3e3c
- ADR-013 https://app.notion.com/p/3eb8bee9ada4801ca590cff9c27f1d7f
- 2일차 회의록 https://app.notion.com/p/3ea8bee9ada480739d8bf239a4d49199
- Slack: #term1_team_freesia
