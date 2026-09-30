# 인수인계 노트 — 백엔드 (박태원)

2026-09-30 기준, 지금까지 논의·결정된 내용 정리. 작업 규칙은 루트의 `AGENTS.md`.

---

## 1. 대회와 제약

- SoftBank Hackathon 2026 예선, 팀 Freesia (6명)
- 과제: 로컬에서 만든 웹앱을 AI를 활용해 원터치로 클라우드·온프레미스에 배포하는 시스템
- 제약: 예산 30만 원(AWS 인프라와 LLM 호출이 함께 사용), 시연 약 3분, **녹화 불가·현장 라이브 시연**
- 심사: 완성도 30 / 클라우드 활용 30 / 팀 개발 20 / 독창성 10 / AI 활용 10
  - 완성도에 "로컬과 클라우드 양쪽 배포 즉시 시연", "누구나 접근 가능" 포함
  - 팀 개발에 "설계 문서화, 대안 제시와 논의 과정" 포함 → ADR 중요

## 2. 서비스 한 줄과 흐름

**인프라 관리자가 미리 만든 인프라 중 하나를 개발자가 고르고, AI가 코드를 분석해 배포 방식 후보와 선택 이유를 보여준 뒤 원클릭으로 배포하는 플랫폼**

비유: 인프라 Space = 미리 지어둔 상가 건물, 컴퓨팅(Fargate/Lambda/EC2) = 입점 형태

| 단계 | 사용자 | 주로 일하는 파트 | 백엔드 역할 |
|---|---|---|---|
| 0 | - | 인프라: 샘플 인프라 사전 구축 + `InfraId` 태그 | 인프라 목록 등록 |
| 1 | GitHub 로그인 | 백엔드 | OAuth, 사용자 저장 |
| 2 | 앱 Space 생성 + 레포 선택 | 백엔드 | 레포 목록, Space 생성 |
| 3 | 인프라 선택 | 백엔드 | 인프라 목록 제공 |
| 4 | 대기 | AI (Bedrock) | 코드 전달, 결과 저장 |
| 5 | 선택 이유 트리·캐릭터 설명 확인 | 프론트 | 트리 데이터 제공 |
| 6 | 컴퓨팅 선택 후 배포 클릭 | 백엔드 | ID 발급, CI/CD에 배포 요청 |
| 7 | 진행 상황 확인 | CI/CD (GitHub Actions) | 상태 수신, 실시간 전달 |
| 8 | URL 접속 | CI/CD | 결과 저장·표시 |
| 9 | 모니터링 | - | 메트릭·로그 조회 API |

## 3. 역할 분담 (2일차 회의)

- 백엔드: 박태원 / AI 백업: 박태원
- 프론트엔드·모니터링: 김동윤
- AI: 강효승
- 인프라/CI-CD: 정호원(플랫폼 인프라, 플랫폼 오너, 백·프론트 백업), 박준서(대상 인프라·샘플 앱·샌드박스, 이후 필요한 파트 백업), 박소정(플랫폼 CI/CD)

## 4. 결정 사항

### 확정
- **ADR-001**: 주제 C(Space 분리) + E(선택 이유 시각화)
- **ADR-004**: AWS 계정 4분리
  - Management: 조직·권한
  - Platform: 플랫폼 FE/BE, AI 분석, 배포 관리, **플랫폼 DB, 인프라 목록 메타데이터**
  - Workload: 사전 구축 인프라, 배포된 앱
  - Sandbox: 실험

### 회의 합의 (2일차)
- 인프라 배포 기능은 구현하지 않고 **조회만** (2:27:19)
- 개발자는 인프라 수정 불가 (1:34:32), 맞는 인프라가 없으면 인프라 담당에 요청 (2:14:49)
- **GitHub OAuth** 로그인 (1:55:03), 회원 역할 구분 없이 로그인 하나로 두 Space 사용 (2:13:39)
- 트리 시각화 유지, 구성 요소 수정 불가 (1:35:34), 마스코트가 선택 이유 설명 (2:29:13)
- **AI는 Terraform만 작성, plan/apply는 GitHub Actions, AWS 키는 레포 시크릿** (32:55~33:23, 작년 피드백 반영)
- 모니터링: 인프라 Space는 올라간 앱 목록, 앱 Space는 메트릭·로그 (2:06:51)
- 프론트 React, 백엔드 언어는 담당자 결정 (2:03:44)
- 샘플 앱은 Hello World 수준 (1:53:51)
- 트레이드오프 있는 결정은 각 역할에서 ADR 작성 (1:55:12)
- MVP 먼저, 진행도 보고 확장·축소 (1:43:22)

### 검토 중 / 제안 / 초안
- **ADR-002 플랫폼 CI/CD** (박소정, 검토 중): Actions → ECR(SHA 태그) → S3 `releases/<SHA>/` → SSM → EC2 Compose. 성공 기준 `/health`·`/version.txt` SHA 일치. 롤백 시 DB는 안 돌아감 → 스키마는 이전 앱과 호환되게.
- **ADR-003 샘플 인프라 3종** (검토 중): 후보 ECS Fargate / Lambda+API GW / EC2+ALB (+S3·CloudFront, App Runner). 댓글로 "이건 인프라가 아니라 컴퓨팅 후보. 인프라는 Public/Private/HA 같은 네트워크 구조로 나누자"는 의견 → **`infra_id`(건물)와 `compute`(입점 형태) 분리** 방향
- **ADR-005 네이밍·태깅** (제안): 이름 `sbh-<scope>-<env>-<type>-<purpose>`, 필수 태그 Name/Project/Scope/Environment/ManagedBy, 사전 구축 인프라는 `InfraId`, 앱 자원은 `ApplicationId`·`DeploymentId`
- **ADR-006 LLM 모델·호출 방식** (강효승, 초안): Bedrock Converse API, 모델 ID를 설정값으로, 경량 모델부터 escalation
- **ADR-007 플랫폼 DB 구성** (박태원, 초안, Decision 비움): A Compose Postgres / B RDS / C SQLite 비교. 정호원·박소정 의견 받고 결정

## 5. 백엔드 쪽 판단 기록 (ADR 후보)

| 주제 | 현재 방향 | 이유 |
|---|---|---|
| 언어 | Python(FastAPI) 예정 | AI 파트와 언어 통일(백업 용이), Bedrock 라이브러리, Swagger 자동 문서 (강효승 확인 대기) |
| 로그인 | GitHub OAuth App, scope `read:user user:email` | 회원 로그인만 필요, 가장 단순. GitHub App은 레포 권한이 필요할 때 검토 |
| 로그인 유지 | JWT, HttpOnly 쿠키 + Bearer 지원 | 프론트 연동 단순 |
| 레포 접근 | 미정. 시연은 public 레포면 추가 권한 불필요 | private은 `repo` scope(쓰기 권한 딸림) 또는 GitHub App |
| 배포 요청 | 백엔드 → GitHub Actions API + 콜백 추천 | 회의 합의(Actions에서 실행, 키는 시크릿)와 일치, 추가 서버 불필요, 백엔드가 AWS 권한 안 가짐 |
| DB | ADR-007 | - |

추가로 쓸 만한 ADR: 회원 인증 방식, 백엔드 기술 스택, 고객 레포 접근 방식, 배포 요청 연동 방식, 인프라 목록 관리 방식(DB 등록 vs 태그 조회).

## 6. 인터페이스 초안 (합의 전)

### 공통
- 상태 값: `pending` → `building` → `deploying` → `success` / `failed`
- 에러: `{"error": "코드", "message": "설명"}`
- ID는 백엔드가 발급 (소문자·숫자·하이픈)

### 프론트 ↔ 백엔드
| 기능 | Method | 경로 | 상태 |
|---|---|---|---|
| GitHub 로그인 | GET | `/auth/github` | 구현됨 |
| 로그인 콜백 | GET | `/auth/github/callback` | 구현됨 |
| 로그아웃 | POST | `/auth/logout` | 구현됨 |
| 내 정보 | GET | `/me` | 구현됨 |
| 헬스체크·버전 | GET | `/health`, `/health/db`, `/version.txt` | 구현됨 |
| 레포 목록 | GET | `/repos` | 예정 |
| 인프라 Space 목록·상세 | GET | `/infra-spaces`, `/infra-spaces/{id}` | 예정 |
| 앱 Space 생성·목록·상세 | POST/GET | `/app-spaces`, `/app-spaces/{id}` | 예정 |
| AI 분석 시작·결과 | POST/GET | `/app-spaces/{id}/analysis` | 예정 |
| 배포 시작 | POST | `/app-spaces/{id}/deployments` | 예정 |
| 배포 진행 (SSE) | GET | `/deployments/{id}/events` | 예정 |

분석 결과 예시:
```json
{
  "status": "done",
  "requirements": ["Node.js 실행", "Postgres DB 필요"],
  "infra_id": "sbh-workload-demo-vpc-sample01",
  "candidates": [
    { "compute": "ecs-fargate", "state": "selected", "reason": "Dockerfile이 있고 상시 웹 서비스" },
    { "compute": "lambda", "state": "alternative", "reason": "가능하지만 상시 연결에 불리" },
    { "compute": "ec2", "state": "unsuitable", "reason": "이 규모엔 관리 부담 과다" }
  ]
}
```

### 백엔드 ↔ AI (강효승)
- 요청: 레포 정보, 주요 파일 내용(package.json, Dockerfile, requirements.txt 등), 선택한 인프라, 컴퓨팅 후보 목록
- 응답: requirements, **evidence(어떤 파일 보고 판단했는지 + certain 여부)**, candidates(state, reason, cons)
- 합의 필요: 별도 서비스 vs 백엔드 내 모듈, 넘길 파일 범위, JSON 스키마

### 백엔드 ↔ CI/CD (고객 앱 배포, 담당 미정)
요청:
```json
{
  "deployment_id": "dep-...",
  "application_id": "app-...",
  "repo": "org/sample-app",
  "branch": "main",
  "infra_id": "sbh-workload-demo-vpc-sample01",
  "compute": "ecs-fargate",
  "callback_url": "https://<platform>/deployments/<id>/status"
}
```
콜백 (여러 번): `{"status": "building"}` … `{"status": "success", "url": "..."}` / `{"status": "failed", "reason": "..."}`, 헤더에 비밀 토큰.

## 7. 이 레포에 이미 된 것

- FastAPI 뼈대, 환경변수 설정, 공통 에러 형식, CORS
- `/health`, `/health/db`, `/version.txt` (`GIT_SHA` 빌드 인자 → `APP_VERSION`)
- SQLAlchemy + Alembic, `users` 테이블(마이그레이션 0001)
- GitHub OAuth 로그인(state 검증), JWT 쿠키, GitHub 토큰 Fernet 암호화 저장, `/me`, 로그아웃
- Dockerfile(비루트 사용자, 시작 시 마이그레이션), docker-compose(백엔드 + Postgres 16, 볼륨, DB 포트 비공개)
- pytest 10개 통과

## 8. 남은 할 일

### 소통
- [ ] ADR-002 스레드에 "구현 전 합의" 백엔드 답변 (pytest / Dockerfile 백엔드 작성 / `/health`·`/version.txt` 추가 / DB는 ADR-007)
- [ ] 강효승: AI 쪽 Python 여부 확인
- [ ] 정호원·박소정: ADR-007 의견 (RDS 추가 가능 여부, 배포 파일 구성, Parameter Store 키, `/health` DB 확인 여부)
- [ ] "CI/CD 파이프라인 - 대상 서비스" 담당·방식 확인, 백엔드 추천안(Actions API + 콜백) 공유
- [ ] 박준서·강효승: 시연 레포 public 여부 → 레포 접근 범위 결정
- [ ] 도메인 정해지면 OAuth 콜백 재등록

### 개발
- [ ] 로컬에서 OAuth App 등록 후 실제 로그인 확인
- [ ] 앱 Space·인프라 Space 모델과 API
- [ ] 레포 읽기 (접근 범위 확정 후)
- [ ] AI 연동 (형식 합의 후)
- [ ] 배포 요청·콜백·SSE (파이프라인 담당 확정 후)
- [ ] 모니터링 조회 API
- [ ] Swagger 주소 김동윤에게 공유

## 9. 주의점

- 시연은 라이브. 배포 파이프라인이 길어 실패 지점이 많음 → 인프라·클러스터는 사전 구축, 발표 직전 배포 중지 (ADR-002)
- 로컬 시연(심사 필수 항목)의 "같은 앱"이 고객 앱인지 우리 시스템인지 해석이 갈림 → 주최 측 확인 권장
- 로컬 실행은 AI가 만든 `docker-compose.yml`로 고객 노트북에서 `docker compose up` 하는 방식이 자연스러움 (AI가 SQLite→Postgres로 바꾸면 고객 로컬 개발도 깨지므로 필요)
- ADR 리뷰 위치 규칙은 없음 (슬랙 스레드·노션 댓글 혼용). 문장 지적은 노션 댓글, 방향 논의는 슬랙 스레드 제안
