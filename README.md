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
② 앱 만들기  저장소 + 인프라(미리 지어 둔 것 중 선택) → 앱 ID 발급. AWS에는 아직 아무것도 안 생김
③ AI 분석    코드를 보고 컴퓨팅 추천 (running → GET으로 다시 확인)
④ 구성안     고른 컴퓨팅의 템플릿 + 넣을 값 (ADR-012)
⑤ 배포       백엔드가 GitHub에 workload-deploy 워크플로 실행 요청 → 워크플로가 빌드·배포
⑥ 진행 상황  워크플로 콜백 → DB → SSE 6단계(queued → prepare → build → deploy → verify → done), 자원별 트리
```

| 연동 대상 | 백엔드와 주고받는 것 |
|---|---|
| 프론트 (`Freesia-Frontend`) | 아래 API. 로그인 없음 |
| AI (`app/ai`, 강효승 님) | 백엔드 안 함수 `run_analysis(repo_url, branch, computes)`. Bedrock 호출 |
| 배포 워크플로 (`workload-deploy`, 박소정 님) | 백엔드 → GitHub `workflow_dispatch`. 워크플로 → 콜백, 구성안 값 조회 (`X-Hub-Signature-256` 서명) |

## API

🟢 진짜 데이터로 동작 · 🟡 API는 동작하지만 내용이 임시(샘플·기본값·가짜 진행) · 🟠 코드는 있지만 연결 대기

| 분류 | API | 상태 | 비고 |
|---|---|---|---|
| 상태 확인 | `GET /api/health`, `/api/health/db`, `/api/version.txt` | 🟢 | 배포 성공 판정, Target Group 헬스체크 |
| 저장소 | `GET` `POST /api/repositories`, `DELETE /api/repositories/{id}` | 🟢 | public GitHub 주소만, 기본 브랜치 `main` |
| 앱 | `GET` `POST /api/app-spaces`, `GET` `DELETE /api/app-spaces/{id}` | 🟢 | 당분간 등록 안 된 저장소도 받음. 삭제는 목록에서 숨기기, AWS에 떠 있으면 먼저 내려야 함 (`app_still_deployed`) |
| 인프라 | `GET /api/infra-spaces`, `/api/infra-spaces/{id}`, `POST /api/infra-spaces/sync` | 🟢 | 목록을 볼 때마다 Workload 계정에서 `InfraId` 태그가 붙은 VPC·서브넷을 다시 읽어 DB를 채움(약 1초, AWS를 못 읽으면 저장된 목록). `sync`는 같은 갱신을 하고 실패를 `502`로 알림. `deployable_computes`로 배포 가능 여부 표시 |
| AI 분석 | `POST` `GET /api/app-spaces/{id}/analysis` | 🟡 | `AI_MODEL_ID`가 없으면 샘플 결과 |
| 구성안 | `POST /api/app-spaces/{id}/plans`, `GET ...?compute=` | 🟡 | 템플릿 `ecs-fargate/basic`, `lambda/basic`, `ec2/basic`. AI가 채운 값(Fargate만), 없으면 템플릿 기본값 |
| 배포 | `POST /api/app-spaces/{id}/deployments` | 🟡 | `DEPLOY_SIMULATE=true`라 가짜 진행(약 8초). `false`면 `deploy.yml` 실행 |
| | `GET /api/deployments/{id}`, `/events` (SSE) | 🟡 | 가짜 진행 결과 |
| | `GET /api/deployments/{id}/resources` | 🟠 | 배포 시작 때 템플릿 자원을 "대기"로 미리 채움, 워크플로 계획이 오면 그걸로 바뀜 |
| 내리기 | `POST /api/app-spaces/{id}/teardown` | 🟠 | `destroy.yml` 실행. 실제 워크플로로 배포된 적 있어야 함 (`not_deployed`). 결과는 앱의 `teardown_status` |
| 워크플로 전용 | `POST /api/deployments/{id}/callback` | 🟠 | 서버 준비 완료(서명 키 연결). 워크플로 실행 후 동작 |
| | `GET /api/plans/{plan_id}` | 🟠 | 같음 |
| 모니터링 | `GET /api/app-spaces/{id}/metrics`, `/logs` | 🟠 | 떠 있는 실제 배포의 지표와 최근 로그. Fargate: CPU·메모리·응답 시간·요청·5xx / Lambda: 처리 시간·호출·오류 / EC2: CPU·로그(현재 서버만). 응답 칸은 같고 없는 값은 null. Workload 키로 CloudWatch 읽기만 |
| | `POST /api/app-spaces/{id}/teardown/callback` | 🟠 | 내리기 결과 (`success` / `failed` + `reason`). 같은 서명 |

에러는 모두 `{"error": "코드", "message": "설명"}`입니다. 요청·응답 모양은 Swagger와 Notion 명세를 봐 주세요.

## 기존 구성안으로 재배포 (Issue #8)

1. `GET /api/app-spaces/{id}/redeploy-context`로 검토 정보를 읽습니다. DB를 바꾸지 않고 GitHub에서 등록된 브랜치 HEAD를 확인합니다.
2. 응답의 원본·대상 SHA와 구성안을 사용자가 검토한 뒤 `POST /api/app-spaces/{id}/redeployments`에 `source_deployment_id`, `target_commit_sha` 두 필드만 보냅니다. 다른 필드는 422로 거절합니다.
3. 새 배포 ID로 기존 상세·SSE·자원 API를 사용합니다. POST와 `GET /api/deployments/{id}`는 nullable `commit_sha`, `plan_id`, `source_deployment_id`도 반환합니다.

검토 응답은 `{app_space_id, repo_url, branch, source_deployment_id, source_commit_sha, target_commit_sha, compute, plan: {id, template, values}}`입니다. 분석이나 구성안 생성을 호출하지 않습니다. 실행 시 최신 분석 SHA 대신 검토한 SHA를 저장해 dispatch하고 원본 `compute`와 `plan_id`를 재사용합니다.

원본은 해당 앱의 가장 최근 실제 성공 배포(`status=success`, `run_id IS NOT NULL`)입니다. 이후 실패 기록은 원본을 바꾸지 않습니다. 해당 성공 배포의 구성안이 없거나 앱·컴퓨팅이 다르면 오래된 구성안으로 넘어가지 않고 `409 redeploy_unavailable`로 거절합니다. 가짜 진행·실행 ID 없는 과거 기록도 같은 오류입니다. 원본 SHA가 없는 기록은 `source_commit_sha: null`로 표시합니다.

- 원본 성공 배포 또는 브랜치 HEAD가 검토 후 바뀌면 `409 redeploy_source_changed` / `redeploy_target_changed`: 다시 GET하고 사용자 확인을 새로 받습니다.
- 배포·내리기 진행 중이면 기존 `409 deployment_in_progress` / `teardown_in_progress`를 반환합니다. 완료된 내리기 이후 새 실제 성공 배포가 없으면 `409 not_deployed`이며 분석·구성안 선택부터 다시 진행합니다.
- 현재 인프라·컴퓨팅의 준비 상태를 다시 검사합니다. GitHub SHA 조회 실패는 `502 github_error`이며 배포를 만들지 않습니다. dispatch 실패는 기존 배포 API와 같이 `201` 응답의 `failed` 배포로 기록됩니다.
- 서버 PostgreSQL에서는 앱 행 잠금으로 일반 배포·재배포·내리기·삭제 접수를 직렬화합니다. SQLite 테스트는 순차 동작을 확인하며 PostgreSQL의 실제 동시 요청 잠금을 대체 검증하지 않습니다.

구성안의 템플릿 이름·값은 그대로 재사용하지만 전체 인프라 스냅샷이나 템플릿 리비전을 고정하지 않습니다. 워크플로는 실행 시 현재 인프라와 배포 레포 템플릿을 읽습니다. 이미지 교체만 실행됨, URL 유지, 무중단, 자동 롤백을 보장하지 않습니다.

배포 전 `0011` 마이그레이션으로 nullable `deployments.source_deployment_id`를 추가해야 합니다. 이전 데이터는 null이며 구버전 앱도 새 컬럼 없이 INSERT할 수 있습니다. 운영 앱 롤백은 컬럼을 유지합니다. downgrade는 기존 CI와 같은 폐기 가능한 DB의 왕복 검사에만 사용합니다.

## 폴더 구조

```
app/
  main.py            앱 생성, CORS, 공통 에러 형식
  config.py          환경변수 설정
  db.py              DB 연결 (DATABASE_URL 하나)
  schemas.py         API 요청·응답 형식 (프론트·AI·워크플로와의 계약)
  ids.py             ID·시각 생성
  catalog.py         배포 템플릿 목록과 값 범위 (배포 레포 templates/와 맞춤, ready 스위치)
  analysis.py        AI 분석 실행·저장 (백그라운드, running 멈춤 방지)
  deploy.py          배포 진행 기록 (콜백과 가짜 진행이 함께 씀)
  signing.py         워크플로가 부르는 API의 서명 확인
  aws.py             Workload·Sandbox 계정 boto3 클라이언트 (WORKLOAD_AWS_*·SANDBOX_AWS_* 키, 읽기만)
  infra_sync.py      인프라 갱신: 출처(지금은 AWS Workload·Sandbox)마다 InfraId 태그로 인프라를 읽어 infra_spaces를 채움 (provider 기록)
  alb_rules.py       공용 ALB 경로·리스너 규칙 번호 (템플릿 연결 전)
  monitoring.py      배포된 앱의 지표·로그 조회 (Workload 계정 CloudWatch, 읽기만)
  github.py          GitHub 호출: 배포할 커밋 확인, 배포 레포 워크플로 실행 (workflow_dispatch)
  mock_data.py       샘플 분석 결과 (모델 연결 전)
  ai/                AI 분석 모듈 (강효승 님): 저장소 읽기 repo.py, 모델 호출·검증 analyze.py
  models/            SQLAlchemy 모델: repositories, infra_spaces, app_spaces, analyses, plans, deployments
  routers/           API: health, repositories, infra_spaces, app_spaces, deployments, plans
alembic/versions/    마이그레이션 0001~0010
tests/               pytest (SQLite 메모리 DB)
.github/workflows/   ci.yml (PR·main 검사), deploy.yml (main 머지 시 배포)
.aws/                task-definition.json (서버 환경변수·비밀값 연결)
docs/                erd.md, handoff.md
```

## 환경변수

`.env.example`을 복사해 씁니다. 서버는 `.aws/task-definition.json`의 `environment`와 `secrets`(Parameter Store)로 넣습니다. 비밀값은 코드·`.env`에 커밋하지 않습니다.

| 이름 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | 로컬 Compose Postgres | 서버는 Parameter Store `/sbh/platform/demo/backend/DATABASE_URL` |
| `RUN_MIGRATIONS` | `true` | 시작할 때 마이그레이션. 서버는 `false` |
| `CORS_ORIGINS` | `http://localhost:5173` | 쉼표로 여러 개 |
| `APP_VERSION` | `dev` | 빌드 때 커밋 SHA (`/api/version.txt`) |
| `DEPLOY_SIMULATE` | `true` | `true`면 워크플로 대신 가짜 진행 |
| `DEPLOY_CALLBACK_SECRET` | 빈 값 | 콜백·값 조회 서명 키. 비어 있으면 모두 401 |
| `GITHUB_DEPLOY_TOKEN` | 빈 값 | 배포 레포 워크플로 실행 토큰. 비어 있으면 진짜 배포·내리기가 실패로 기록됨 |
| `DEPLOY_REPO`, `DEPLOY_REF`, `PUBLIC_API_BASE` | `softbank-hackathon-2026/workload-deploy`, `main`, `https://sbh.howon.me/api` | 실행할 배포 레포와 콜백 주소 |
| `WORKLOAD_AWS_ACCESS_KEY_ID`, `WORKLOAD_AWS_SECRET_ACCESS_KEY` | 빈 값 | 모니터링용 Workload 계정 키 (CloudWatch 읽기만). 서버는 Parameter Store. `AWS_*` 이름을 쓰지 않음 |
| `SANDBOX_AWS_ACCESS_KEY_ID`, `SANDBOX_AWS_SECRET_ACCESS_KEY` | 빈 값 | Sandbox 계정 읽기 키 (인프라 목록·모니터링). 비어 있으면 Sandbox는 읽지 않음. 서버는 Parameter Store |
| `AI_MODEL_ID` | 빈 값 | 비어 있으면 분석은 샘플 결과 |
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
- [x] 콜백·자원별 상태·워크플로 값 조회 API (서명 확인)
- [x] AI 분석 모듈 연결 (모델 없으면 샘플)
- [x] 프론트 연동 확인 (`?source=api`, 저장소 등록 → 배포 완료까지)
- [x] 서명 키·GitHub 토큰 Task Definition 연결
- [x] 워크플로 실행 코드(`deploy.yml`, `destroy.yml`). `DEPLOY_SIMULATE=false`로 켬
- [ ] 진짜 워크플로로 한 번 배포해 보고 가짜 진행 끄기
- [ ] AI 모델 연결, AI가 구성안 값 채우기 (강효승 님)
- [ ] 나머지 인프라 2종 실제 값 (박준서 님)
- [ ] 가짜 진행이 서버 교체로 멈추지 않게, 30분 시간 초과
- [ ] 모니터링(지표·로그) API, 내리기 완료 콜백(배포 레포와 협의)

### Redeployment review follow-up
A teardown attempt can partially remove resources even when it fails. The simple redeployment source must be newer than the latest teardown request, regardless of its final status; otherwise the API returns `409 not_deployed` and requires normal analysis/configuration. Missing timestamps in teardown history return `409 redeploy_unavailable`. This also prevents a later failed teardown from hiding an earlier successful destroy. Fresh actual successful deployment after the request restores eligibility.
