# Freesia Platform Backend

원클릭 배포 플랫폼의 백엔드(FastAPI)입니다. 직접 분석하거나 배포하지 않고, 프론트·AI·배포 워크플로를 잇는 허브입니다.
API 계약은 [백엔드 API 명세 (Notion)](https://app.notion.com/p/3ec8bee9ada48145a459e616b692fbb1), 작업 규칙은 `AGENTS.md`, DB 설계는 `docs/erd.md`에 있습니다.

## 서버

| 항목 | 값 |
|---|---|
| API | `https://sbh.howon.me/api` (Swagger `/api/docs`) |
| 프론트에서 백엔드 쓰기 | `https://sbh.howon.me/?source=api` (`?source=api`가 없으면 프론트 데모 모드) |
| 배포 | `main`에 머지하면 자동 배포 (`.github/workflows/deploy.yml`, 문서만 바뀌면 제외). ECS Fargate Task 2개 |
| DB | RDS PostgreSQL 17, 마이그레이션은 배포 단계에서 한 번 실행 (ADR-013) |

## 흐름

```
① 통합       저장소 주소 등록
② 앱 만들기  저장소 + 인프라 → 앱 ID 발급. 인프라를 고르지 않으면 기본 인프라(DefaultInfra 태그, 지금 Sandbox)
             아직 아무 자원도 안 생김
③ AI 분석    코드를 보고 인프라가 허용하는 컴퓨팅 중에서 추천하고 템플릿 값을 채움 (running → GET으로 다시 확인)
④ 구성안     고른 컴퓨팅의 템플릿 + 넣을 값 (ADR-012)
⑤ 배포       백엔드가 GitHub에 workload-deploy 워크플로 실행 요청
             AWS(Fargate·Lambda·EC2) → deploy.yml (Terraform) / 온프레미스(onprem·onprem-container) → deploy-vm.yml (Ansible)
⑥ 진행 상황  워크플로 콜백 → DB → SSE 6단계(queued → prepare → build → deploy → verify → done), 자원별 트리
⑦ 운영       모니터링(지표·로그), 재배포(설정 유지·코드만 최신), 내리기(destroy.yml / destroy-vm.yml)
```

| 인프라 출처 | 어디서 읽나 | 컴퓨팅 |
|---|---|---|
| AWS Workload 계정 (필수) | `InfraId` 태그 VPC (읽기 키 `WORKLOAD_AWS_*`) | `ecs-fargate`, `lambda`, `ec2` (공용 ALB가 있으면 Fargate는 `shared-alb`) |
| AWS Sandbox 계정 (키 있을 때) | 같음 (`SANDBOX_AWS_*`) | 같음 |
| 온프레미스 Proxmox (설정 있을 때) | Cloudflare Access → Proxmox API, `freesia` 태그 VM (`ONPREM_*`) | `onprem`(코드를 VM에서 빌드), `onprem-container`(Dockerfile로 Docker) |

| 연동 대상 | 백엔드와 주고받는 것 |
|---|---|
| 프론트 (`Freesia-Frontend`) | 아래 API. 로그인 없음 |
| AI (`app/ai`, 강효승 님) | 백엔드 안 함수 `run_analysis(repo_url, branch, computes)`. Bedrock 호출 (작업 역할 권한) |
| 배포 워크플로 (`workload-deploy`, 박소정 님) | 백엔드 → GitHub `workflow_dispatch`. 워크플로 → 콜백, 구성안 값 조회 (`X-Hub-Signature-256` 서명) |
| 온프레미스 (Proxmox, 정호원 님·박준서 님) | 백엔드 → Proxmox API 읽기만. VM 접속은 배포 워크플로가 함 |

## API

🟢 진짜 데이터로 동작 · 🟡 API는 동작하지만 내용이 임시 · ⚪ 지원 안 함

| 분류 | API | 상태 | 비고 |
|---|---|---|---|
| 상태 확인 | `GET /api/health`, `/api/health/db`, `/api/version.txt` | 🟢 | 배포 성공 판정, Target Group 헬스체크 |
| 저장소 | `GET` `POST /api/repositories`, `DELETE /api/repositories/{id}` | 🟢 | public GitHub 주소만, 기본 브랜치 `main` |
| 인프라 | `GET /api/infra-spaces`, `/api/infra-spaces/{id}`, `POST /api/infra-spaces/sync` | 🟢 | 목록을 볼 때마다 출처(AWS Workload·Sandbox, 온프레미스)를 다시 읽음. `provider`(aws/onprem), `deployable_computes`. 기본 인프라는 목록에서 빠지고 상세는 됨. `sync`는 실패를 `502`로 알림 |
| 앱 | `GET` `POST /api/app-spaces`, `GET` `DELETE /api/app-spaces/{id}` | 🟢 | `infra_id`를 비우면 기본 인프라(없으면 `400 no_default_infra`). 삭제는 목록에서 숨기기, 떠 있으면 먼저 내려야 함 (`app_still_deployed`) |
| AI 분석 | `POST` `GET /api/app-spaces/{id}/analysis` | 🟢 | 실제 모델(`AI_MODEL_ID`). 비어 있으면 샘플. 응답은 검사 규칙 V1~V9를 통과해야 저장 |
| 구성안 | `POST /api/app-spaces/{id}/plans`, `GET ...?compute=` | 🟢 | 템플릿: `ecs-fargate/basic`·`shared-alb`, `lambda/basic`, `ec2/basic`, `onprem`, `onprem-container`. AI 값, 없으면 기본값 |
| 배포 | `POST /api/app-spaces/{id}/deployments` | 🟢 | AWS는 `deploy.yml`, 온프레미스는 `deploy-vm.yml` (`compute` 그대로 전달). `DEPLOY_SIMULATE=true`면 가짜 진행 |
| | `GET /api/deployments/{id}`, `/events` (SSE) | 🟢 | |
| | `GET /api/deployments/{id}/resources` | 🟢 | AWS는 배포 시작 때 템플릿 자원을 "대기"로 미리 채움. 온프레미스는 Ansible 작업이 자원 하나 (`ansible_task`) |
| 재배포 | `GET /api/app-spaces/{id}/redeploy-context`, `POST .../redeployments` | 🟢 | 마지막 실제 성공 배포의 설정으로 브랜치 최신 커밋 배포 (아래 절) |
| 내리기 | `POST /api/app-spaces/{id}/teardown` | 🟢 | provider로 `destroy.yml` / `destroy-vm.yml`. 실제로 배포된 적 있어야 함 (`not_deployed`). 결과는 앱의 `teardown_status` |
| 모니터링 | `GET /api/app-spaces/{id}/metrics`, `/logs` | 🟢 | 앱이 있는 계정의 CloudWatch를 읽기만. Fargate: CPU·메모리·응답 시간·요청·5xx / Lambda: 처리 시간·호출·오류 / EC2: CPU·로그(현재 서버만). 온프레미스는 ⚪ `unsupported` |
| 워크플로 전용 | `POST /api/deployments/{id}/callback`, `POST /api/app-spaces/{id}/teardown/callback` | 🟢 | 서명 확인. 프론트는 부르지 않음 |
| | `GET /api/plans/{plan_id}` | 🟢 | 워크플로가 받아 가는 구성안 값 (AWS: VPC·서브넷·공용 ALB, 온프레미스: `vm_host`) |

에러는 모두 `{"error": "코드", "message": "설명"}`입니다. 요청·응답 모양은 Swagger와 Notion 명세를 봐 주세요.

## 기존 구성안으로 재배포 (Issue #8)

1. `GET /api/app-spaces/{id}/redeploy-context`로 검토 정보를 읽습니다. DB를 바꾸지 않고 GitHub에서 등록된 브랜치 HEAD를 확인합니다.
2. 응답의 원본·대상 SHA와 구성안을 사용자가 검토한 뒤 `POST /api/app-spaces/{id}/redeployments`에 `source_deployment_id`, `target_commit_sha` 두 필드만 보냅니다. 다른 필드는 422로 거절합니다.
3. 새 배포 ID로 기존 상세·SSE·자원 API를 사용합니다. POST와 `GET /api/deployments/{id}`는 nullable `commit_sha`, `plan_id`, `source_deployment_id`도 반환합니다.

검토 응답은 `{app_space_id, repo_url, branch, source_deployment_id, source_commit_sha, target_commit_sha, compute, plan: {id, template, values}}`입니다. 분석이나 구성안 생성을 호출하지 않습니다. 실행 시 최신 분석 SHA 대신 검토한 SHA를 저장해 dispatch하고 원본 `compute`와 `plan_id`를 재사용합니다.

원본은 해당 앱의 가장 최근 실제 성공 배포(`status=success`, `run_id IS NOT NULL`)입니다. 이후 실패 기록은 원본을 바꾸지 않습니다. 해당 성공 배포의 구성안이 없거나 앱·컴퓨팅이 다르면 오래된 구성안으로 넘어가지 않고 `409 redeploy_unavailable`로 거절합니다. 가짜 진행·실행 ID 없는 과거 기록도 같은 오류입니다. 원본 SHA가 없는 기록은 `source_commit_sha: null`로 표시합니다.

- 원본 성공 배포 또는 브랜치 HEAD가 검토 후 바뀌면 `409 redeploy_source_changed` / `redeploy_target_changed`: 다시 GET하고 사용자 확인을 새로 받습니다.
- 내리기는 실패해도 자원을 일부 지웠을 수 있습니다. 그래서 원본 성공 배포는 결과와 상관없이 가장 최근 내리기 요청보다 뒤여야 합니다. 아니면 `409 not_deployed`로 분석·구성안부터 다시 하게 합니다. 내리기 기록에 시각이 없으면 `409 redeploy_unavailable`입니다. 요청 뒤에 새 실제 성공 배포가 생기면 다시 재배포할 수 있습니다.
- 배포·내리기 진행 중이면 기존 `409 deployment_in_progress` / `teardown_in_progress`를 반환합니다. 완료된 내리기 이후 새 실제 성공 배포가 없으면 `409 not_deployed`이며 분석·구성안 선택부터 다시 진행합니다.
- 현재 인프라·컴퓨팅의 준비 상태를 다시 검사합니다. GitHub SHA 조회 실패는 `502 github_error`이며 배포를 만들지 않습니다. dispatch 실패는 기존 배포 API와 같이 `201` 응답의 `failed` 배포로 기록됩니다.
- 서버 PostgreSQL에서는 앱 행 잠금으로 일반 배포·재배포·내리기·삭제 접수를 직렬화합니다. SQLite 테스트는 순차 동작을 확인하며 PostgreSQL의 실제 동시 요청 잠금을 대체 검증하지 않습니다.

구성안의 템플릿 이름·값은 그대로 재사용하지만 전체 인프라 스냅샷이나 템플릿 리비전을 고정하지 않습니다. 워크플로는 실행 시 현재 인프라와 배포 레포 템플릿을 읽습니다. 이미지 교체만 실행됨, URL 유지, 무중단, 자동 롤백을 보장하지 않습니다.

배포 전 `0011` 마이그레이션으로 nullable `deployments.source_deployment_id`를 추가해야 합니다. 이전 데이터는 null이며 구버전 앱도 새 컬럼 없이 INSERT할 수 있습니다. 운영 앱 롤백은 컬럼을 유지합니다. downgrade는 기존 CI와 같은 폐기 가능한 DB의 왕복 검사에만 사용합니다.

## 폴더 구조

```
Freesia-backend/
├── app/                          FastAPI 앱 (모든 API는 /api 아래)
│   ├── main.py                   앱 생성, CORS, 라우터 등록, 공통 에러 형식 {"error", "message"}
│   ├── config.py                 환경변수 설정 (Settings). 서버 값은 Parameter Store에서 주입, 코드에 비밀값 없음
│   ├── db.py                     DB 연결. DATABASE_URL 하나로 접속 (로컬 Compose / 서버 RDS / 테스트 SQLite)
│   ├── schemas.py                API 요청·응답 형식. 프론트·AI·배포 워크플로와 맞추는 계약 (Compute 값 목록도 여기)
│   ├── ids.py                    ID(소문자·숫자·하이픈)와 UTC 시각 생성
│   │
│   │   ── 인프라 (조회만) ──
│   ├── infra_sync.py             인프라 갱신. 출처(Source) 목록을 돌며 읽어 infra_spaces를 채움
│   │                               · 출처: AWS Workload(필수) · AWS Sandbox(키 있을 때) · 온프레미스 Proxmox(설정 있을 때)
│   │                               · 출처마다 provider(aws/onprem) 기록, 한 출처가 실패해도 나머지는 갱신
│   │                               · VPC 태그: InfraId(필수) DisplayName·Description·Network·Computes·DefaultInfra
│   │                               · 공용 ALB(Multi-AZ)와 앱 서브넷도 찾음. 목록을 볼 때마다 갱신 (5초 쿨다운)
│   ├── aws.py                    고객 앱 계정 boto3 클라이언트. 계정별 키(WORKLOAD_AWS_*·SANDBOX_AWS_*), 읽기 API만 부름
│   ├── onprem.py                 Proxmox API 클라이언트. Cloudflare Access를 거쳐 VM 목록을 읽고 freesia 태그 VM만 인프라로
│   │
│   │   ── AI 분석 ──
│   ├── analysis.py               분석 실행·저장. 백그라운드로 돌고, 오래 running이면 failed로 정리 (멈춤 방지)
│   ├── ai/                       AI 모듈 (강효승 님)
│   │   ├── repo.py               public 레포를 커밋 SHA로 고정하고 tarball 하나로 받아 메모리에서 읽음 (크기 상한)
│   │   ├── analyze.py            Bedrock 모델 호출·응답 검증. 1단계 핵심 파일 → 필요하면 2단계 전체 코드
│   │   └── template_fields.py    컴퓨팅별로 AI가 채울 템플릿 값 양식 (배포 레포 variables.tf·vm_plan.py와 맞춤)
│   ├── mock_data.py              샘플 분석 결과 (AI_MODEL_ID가 비어 있을 때)
│   │
│   │   ── 구성안·배포 ──
│   ├── catalog.py                배포 템플릿 목록과 값 범위 검사, ready 스위치
│   │                               · AWS: ecs-fargate/basic · ecs-fargate/shared-alb · lambda/basic · ec2/basic
│   │                               · 온프레미스: onprem · onprem-container (ONPREM_COMPUTES)
│   ├── alb_rules.py              공용 ALB에서 앱을 나누는 경로(/, /api)와 리스너 규칙 번호. 겹치면 409
│   ├── github.py                 GitHub 호출: 배포할 커밋 확인, 배포 레포 워크플로 실행(workflow_dispatch), 템플릿 자원 목록 읽기
│   ├── deploy.py                 배포 진행 기록: 단계 이벤트(SSE용), 자원별 상태(트리), 트리 미리 채우기, 가짜 진행
│   ├── signing.py                워크플로가 부르는 API의 서명 확인 (X-Hub-Signature-256, HMAC-SHA256)
│   ├── monitoring.py             배포된 앱의 지표·로그. 앱이 있는 계정의 CloudWatch를 읽기만 (온프레미스는 unsupported)
│   │
│   ├── models/                   SQLAlchemy 모델 (테이블 설계는 docs/erd.md)
│   │   ├── repository.py         repositories: 등록된 저장소
│   │   ├── infra_space.py        infra_spaces: 인프라 (provider, VPC·서브넷, 공용 ALB, vm_host, is_default)
│   │   ├── app_space.py          app_spaces: 앱 (레포, 인프라, 공용 ALB 경로, 내리기 상태, 삭제 시각)
│   │   ├── analysis.py           analyses: AI 분석 결과 (분석한 커밋 SHA 포함)
│   │   ├── plan.py               plans: 구성안 = 템플릿 + 값
│   │   └── deployment.py         deployments · deployment_events(SSE) · deployment_resources(트리)
│   │
│   └── routers/                  API 엔드포인트 (prefix /api)
│       ├── health.py             GET /health · /health/db · /version.txt (ALB 헬스체크, 배포 성공 판정)
│       ├── repositories.py       GET·POST /repositories, DELETE /repositories/{id}
│       ├── infra_spaces.py       GET /infra-spaces(볼 때마다 갱신, 기본 인프라 숨김) · POST /infra-spaces/sync · GET /infra-spaces/{id}
│       ├── app_spaces.py         앱 만들기·목록·상세·삭제 / 분석 / 구성안 / 배포 시작 / 재배포 / 내리기(+콜백)
│       │                           · 배포: AWS는 deploy.yml, 온프레미스(onprem·onprem-container)는 deploy-vm.yml
│       │                           · 내리기: provider로 destroy.yml / destroy-vm.yml
│       ├── deployments.py        GET /deployments/{id} · /resources(트리) · /events(SSE) · POST /callback(워크플로, 서명)
│       ├── plans.py              GET /plans/{plan_id}: 워크플로가 받아 가는 구성안 값 (서명, 프론트는 안 부름)
│       └── monitoring.py         GET /app-spaces/{id}/metrics · /logs
│
├── alembic/                      DB 마이그레이션 (컬럼 추가만, 삭제·이름 변경 금지)
│   ├── env.py                    DATABASE_URL로 접속
│   └── versions/                 0001~0014
├── tests/                        pytest (SQLite 메모리 DB). GitHub·AWS·Proxmox·AI는 가짜로 바꿔 끼움 (conftest.py)
│
├── .github/workflows/
│   ├── ci.yml                    PR·main: 테스트, 마이그레이션 검사, 도커 빌드
│   └── deploy.yml                main 머지 시: ECR에 이미지 → 마이그레이션 일회성 Task → ECS Fargate(Task 2개) 교체
├── .aws/task-definition.json     서버 Task Definition. 환경변수와 Parameter Store 비밀값 연결
├── Dockerfile · entrypoint.sh    서버 이미지. RUN_MIGRATIONS=true면 시작할 때 마이그레이션 (로컬 기본값)
├── docker-compose.yml            로컬 실행 (백엔드 + Postgres)
├── .env.example                  환경변수 목록 (값은 비움, .env는 커밋하지 않음)
├── requirements.txt · requirements-dev.txt
├── AGENTS.md                     작업 규칙 (AI 에이전트·팀원)
└── docs/
    ├── erd.md                    테이블 설계
    └── handoff.md                논의·결정 정리
```

## 환경변수

`.env.example`을 복사해 씁니다. 서버는 `.aws/task-definition.json`의 `environment`와 `secrets`(Parameter Store)로 넣습니다. 비밀값은 코드·`.env`에 커밋하지 않습니다.

| 이름 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | 로컬 Compose Postgres | 서버는 Parameter Store `/sbh/platform/demo/backend/DATABASE_URL` |
| `RUN_MIGRATIONS` | `true` | 시작할 때 마이그레이션. 서버는 `false` |
| `CORS_ORIGINS` | `http://localhost:5173` | 쉼표로 여러 개 |
| `APP_VERSION` | `dev` | 빌드 때 커밋 SHA (`/api/version.txt`) |
| `DEPLOY_SIMULATE` | `true` | `true`면 워크플로 대신 가짜 진행. **서버는 `false`** (실제 배포) |
| `DEPLOY_CALLBACK_SECRET` | 빈 값 | 콜백·값 조회 서명 키. 비어 있으면 모두 401 |
| `GITHUB_DEPLOY_TOKEN` | 빈 값 | 배포 레포 워크플로 실행 토큰. 비어 있으면 진짜 배포·내리기가 실패로 기록됨 |
| `DEPLOY_REPO`, `DEPLOY_REF`, `PUBLIC_API_BASE` | `softbank-hackathon-2026/workload-deploy`, `main`, `https://sbh.howon.me/api` | 실행할 배포 레포와 콜백 주소 |
| `WORKLOAD_AWS_ACCESS_KEY_ID`, `WORKLOAD_AWS_SECRET_ACCESS_KEY` | 빈 값 | Workload 계정 키 (인프라 목록·모니터링, 읽기 API만 부름). 서버는 Parameter Store. `AWS_*` 이름을 쓰지 않음 (Bedrock은 작업 역할로 부름) |
| `SANDBOX_AWS_ACCESS_KEY_ID`, `SANDBOX_AWS_SECRET_ACCESS_KEY` | 빈 값 | Sandbox 계정 읽기 키 (인프라 목록·모니터링). 비어 있으면 Sandbox는 읽지 않음. 서버는 Parameter Store |
| `ONPREM_API_URL`, `ONPREM_CF_CLIENT_ID`, `ONPREM_CF_CLIENT_SECRET`, `ONPREM_PVE_TOKEN_ID`, `ONPREM_PVE_TOKEN_SECRET` | 빈 값 | 온프레미스 Proxmox 읽기 (Cloudflare Access 서비스 토큰 + Proxmox API 토큰). 5개가 다 있어야 읽음. 서버는 Parameter Store |
| `ONPREM_VM_HOST` | 빈 값 | 배포 워크플로가 cloudflared로 들어갈 서비스 VM 호스트 (지금 `vpn.howon.me`). 비어 있으면 온프레미스 인프라는 보이지만 배포는 막힘 |
| `AI_MODEL_ID` | 빈 값 | 비어 있으면 분석은 샘플 결과. 서버는 `global.moonshotai.kimi-k3` |
| `AI_AWS_REGION`, `AI_TIMEOUT_SECONDS`, `AI_SCHEMA_OUTPUT` | `ap-northeast-2`, `60`, `true` | AI 호출 설정 |

## 로컬 실행·테스트

```bash
cp .env.example .env
docker compose up --build        # 백엔드 + Postgres, http://localhost:8000/api/docs

pip install -r requirements-dev.txt
pytest                           # SQLite 메모리 DB, Postgres 없이 실행
```

CI는 PR마다 `pytest`, PostgreSQL 17 마이그레이션 올리기·내리기, Docker 빌드를 확인합니다.

## DB 마이그레이션

```bash
alembic revision --autogenerate -m "설명"
alembic upgrade head
```

- 컬럼은 추가만 합니다. 삭제·이름 변경은 하지 않습니다 (롤백해도 DB는 되돌아가지 않음).
- 서버에 적용된 마이그레이션 파일은 고치지 않습니다. 데이터를 바꿀 때도 새 파일을 만듭니다.

## 머지할 때 주의

- **머지하면 바로 서버에 배포됩니다.** 시연 직전에는 머지하지 않습니다.
- Task Definition에 새 `secrets`를 넣는 PR은 **실행 역할 권한이 먼저 추가된 뒤에** 머지합니다. 순서가 바뀌면 새 버전이 뜨지 않습니다 (자동 롤백으로 이전 버전은 유지).

## 진행 상황

- [x] 저장소·앱·인프라·분석·구성안·배포 DB 저장, 서버 Task 2개
- [x] 실제 AI 모델로 분석하고 템플릿 값 채우기 (강효승 님)
- [x] 실제 배포·내리기·콜백·SSE·자원 트리 (배포 시작부터 트리 표시)
- [x] AWS 컴퓨팅 3종 + 고가용성 공용 ALB(`demo.howon.me`, 경로로 앱 나누기)
- [x] 인프라 자동 갱신: AWS Workload·Sandbox, 온프레미스 Proxmox. 기본 인프라(`DefaultInfra`)
- [x] 온프레미스 `onprem`·`onprem-container` 배포·내리기 (실제 VM에서 확인)
- [x] 모니터링(AWS), 재배포
- [ ] 배포 30분 시간 초과 (콜백이 끊기면 지금은 "진행 중"으로 남음)
- [ ] AI 응답이 검사 규칙에 걸리면 한 번 더 묻기 ("분석에 실패했어요"가 간혹 나옴)
- [ ] 온프레미스 모니터링 (Proxmox VM CPU·메모리)
- [ ] 인프라·앱에 "지금 떠 있는지" 표시 (`live_app_count`, `is_live`)
- [ ] Workload 키를 읽기 전용 권한으로 줄이기 (지금 AdministratorAccess, 정호원 님)
- [ ] 비용 보호 (로그인이 없어 배포 횟수 제한 없음)
