"""분석 흐름(1·2단계)과 검증 규칙. 저장소 읽기와 모델 호출은 바꿔 끼운다."""
import json

import pytest

from app import catalog
from app.ai import analyze
from app.ai.analyze import FAIL_MESSAGE, NO_DOCKERFILE_MESSAGE, run_analysis
from app.ai.repo import RepoError
from app.ai.template_fields import TEMPLATE_FIELDS

URL = "https://github.com/org/app"
SHA = "a" * 40
COMPUTES = ["ecs-fargate", "lambda", "ec2"]
FILES = {"Dockerfile": "FROM node:20", "package.json": "{}", "README.md": "# app", "src/index.js": "app.listen(3000)"}


def cand(compute, state, files=()):
    return {"compute": compute, "state": state, "reason": "이유", "cons": ["단점"], "evidence_files": list(files)}


def output(**over) -> str:
    base = {
        "status": "done",
        "requirements": ["Node.js 20"],
        "evidence": [{"file": "Dockerfile", "finding": "컨테이너로 실행", "certain": True}],
        "candidates": [cand("ecs-fargate", "selected", ["Dockerfile"]), cand("lambda", "alternative"), cand("ec2", "unsuitable")],
        "mascot_message": "Fargate를 추천해요",
        "needs_full_code": False,
    }
    return json.dumps({**base, **over}, ensure_ascii=False)


@pytest.fixture
def repo_files(monkeypatch):
    files = dict(FILES)
    monkeypatch.setattr(analyze, "fetch_repo", lambda url, branch: (SHA, files))
    return files


@pytest.fixture
def model(monkeypatch):
    """model(답1, 답2, ...)로 모델 응답을 정하고, 모델이 받은 프롬프트 목록을 돌려준다."""
    prompts: list[str] = []

    def set_replies(*replies):
        it = iter(replies)
        monkeypatch.setattr(analyze, "_converse", lambda system, user: (prompts.append(user), next(it))[1])
        return prompts

    return set_replies


def test_stage1_only(repo_files, model):
    prompts = model(f"```json\n{output()}\n```")  # 울타리를 붙여도 JSON만 꺼낸다
    result, sha = run_analysis(URL, "main", COMPUTES)
    assert (result.status, sha, len(prompts)) == ("done", SHA, 1)
    assert 'path="Dockerfile"' in prompts[0] and "src/index.js" not in prompts[0]
    assert result.candidates[0].evidence_files == ["Dockerfile"]
    assert "needs_full_code" not in result.model_dump()


def test_stage2_when_model_needs_full_code(repo_files, model):
    evidence = [{"file": "src/index.js", "finding": "3000 포트", "certain": True}]
    stage2 = output(evidence=evidence, candidates=[cand("ecs-fargate", "selected", ["src/index.js"]), cand("lambda", "alternative"), cand("ec2", "unsuitable")])
    prompts = model(output(needs_full_code=True), stage2)
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert len(prompts) == 2 and 'path="src/index.js"' in prompts[1]
    assert result.evidence[0].file == "src/index.js"


def test_stage2_failure_keeps_stage1(repo_files, model):
    model(output(needs_full_code=True), "형식이 틀린 응답")
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert result.status == "done" and result.evidence[0].file == "Dockerfile"


def test_no_dockerfile_skips_model(repo_files, model):
    del repo_files["Dockerfile"]
    prompts = model()
    result, sha = run_analysis(URL, "main", COMPUTES)
    assert (result.status, result.mascot_message, sha, prompts) == ("failed", NO_DOCKERFILE_MESSAGE, SHA, [])


def test_vm_analyzes_without_dockerfile(repo_files, model):
    """온프레미스 vm은 소스를 직접 빌드해서 Dockerfile이 없어도 모델을 부른다. 후보가 vm 하나라도 통과한다."""
    del repo_files["Dockerfile"]
    evidence = [{"file": "package.json", "finding": "Node 앱", "certain": True}]
    prompts = model(output(evidence=evidence, candidates=[cand("vm", "selected", ["package.json"])], template_values={"vm": VM_GOOD}))
    result, _ = run_analysis(URL, "main", ["vm"])
    assert (result.status, len(prompts)) == ("done", 1)
    assert [c.compute for c in result.candidates] == ["vm"] and result.template_values["vm"] == VM_GOOD


@pytest.mark.parametrize("has_dockerfile,status", [(False, "done"), (True, "failed")])
def test_mixed_computes_without_dockerfile(repo_files, model, has_dockerfile, status):
    """vm과 컨테이너 방식이 섞인 인프라. Dockerfile이 없으면 vm만 배포할 수 있어 컨테이너 후보는 unsuitable이어도 된다.
    Dockerfile이 있으면 지금처럼 unsuitable을 뺀 후보가 2개 이상이어야 한다."""
    if not has_dockerfile:
        del repo_files["Dockerfile"]
    evidence = [{"file": "package.json", "finding": "Node 앱", "certain": True}]
    model(output(evidence=evidence, candidates=[cand("vm", "selected", ["package.json"]), cand("ecs-fargate", "unsuitable")]))
    result, _ = run_analysis(URL, "main", ["vm", "ecs-fargate"])
    assert result.status == status


def test_repo_error_is_failed(monkeypatch):
    def broken(url, branch):
        raise RepoError("저장소나 브랜치를 찾을 수 없어요.")

    monkeypatch.setattr(analyze, "fetch_repo", broken)
    result, sha = run_analysis(URL, "main", COMPUTES)
    assert (result.status, result.mascot_message, sha) == ("failed", "저장소나 브랜치를 찾을 수 없어요.", None)


def test_model_says_failed(repo_files, model):
    model(output(status="failed", requirements=[], evidence=[], candidates=[], mascot_message="웹 앱이 아니에요"))
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert (result.status, result.mascot_message) == ("failed", "웹 앱이 아니에요")


@pytest.mark.parametrize(
    "over",
    [
        {"candidates": [cand("ecs-fargate", "selected"), cand("lambda", "alternative")]},  # V2·V3 ec2 빠짐
        {"candidates": [cand("lambda", "alternative"), cand("ecs-fargate", "selected"), cand("ec2", "unsuitable")]},  # V4
        {"candidates": [cand("ecs-fargate", "selected"), cand("lambda", "unsuitable"), cand("ec2", "unsuitable")]},  # V5
        {"evidence": [{"file": "secret.txt", "finding": "f", "certain": True}], "candidates": [cand("ecs-fargate", "selected"), cand("lambda", "alternative"), cand("ec2", "unsuitable")]},  # V6
        {"candidates": [cand("ecs-fargate", "selected", ["README.md"]), cand("lambda", "alternative"), cand("ec2", "unsuitable")]},  # V7
        {"status": "failed"},  # V8 failed인데 후보가 있음
        {"evidence": [], "candidates": [cand("ecs-fargate", "selected"), cand("lambda", "alternative"), cand("ec2", "unsuitable")]},  # V9
        {"status": "running"},  # V1
    ],
)
def test_validation_violation_is_failed(repo_files, model, over):
    model(output(**over))
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert (result.status, result.mascot_message, result.candidates) == ("failed", FAIL_MESSAGE, [])


def test_single_compute_passes(repo_files, model):
    model(output(candidates=[cand("ecs-fargate", "selected", ["Dockerfile"])]))
    result, _ = run_analysis(URL, "main", ["ecs-fargate"])
    assert result.status == "done" and [c.compute for c in result.candidates] == ["ecs-fargate"]


def test_bedrock_error_is_failed(repo_files, monkeypatch):
    from botocore.exceptions import ClientError

    def denied(system, user):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Converse")

    monkeypatch.setattr(analyze, "_converse", denied)
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert (result.status, result.mascot_message) == ("failed", FAIL_MESSAGE)


# 템플릿 값 (ADR-012). 틀린 칸만 버리고 분석은 그대로 쓴다. 기본값은 구성안을 만들 때 채운다

GOOD = {"container_port": 3000, "health_check_path": "/health", "cpu": 256, "memory": 512}
LAMBDA_GOOD = {"container_port": 3000, "health_check_path": "/health", "memory": 512, "timeout": 30}
EC2_GOOD = {"container_port": 3000, "health_check_path": "/health", "instance_type": "t3.micro"}
VM_GOOD = {"runtime": "node", "app_port": 3000, "health_check_path": "/health", "build_command": "npm ci",
           "start_command": "npm start", "runtime_version": "21", "java_server": "none", "war_file": "target/*.war"}


def without(values, *names):
    return {k: v for k, v in values.items() if k not in names}


def test_template_values_saved(repo_files, model):
    values = {"ecs-fargate": GOOD, "lambda": LAMBDA_GOOD, "ec2": EC2_GOOD, "vm": VM_GOOD}
    model(output(template_values=values))
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert result.template_values == values


@pytest.mark.parametrize(
    "compute,given,expected",
    [
        ("ecs-fargate", {**GOOD, "memory": 4096}, without(GOOD, "memory")),  # cpu 256에 없는 memory만 버리고 포트는 지킨다
        ("ecs-fargate", {**GOOD, "cpu": 1024, "memory": 512}, {**without(GOOD, "memory"), "cpu": 1024}),  # cpu 1024에 없는 memory
        ("ecs-fargate", {**GOOD, "cpu": 2048}, without(GOOD, "cpu")),  # 템플릿 밖 cpu
        ("ecs-fargate", {**GOOD, "container_port": "3000"}, without(GOOD, "container_port")),
        ("ecs-fargate", {**GOOD, "health_check_path": "health"}, without(GOOD, "health_check_path")),
        ("ecs-fargate", {"port": 3000}, {}),  # 모르는 이름은 무시된다
        ("ecs-fargate", "모양이 틀림", {}),
        ("lambda", {**LAMBDA_GOOD, "container_port": 80}, without(LAMBDA_GOOD, "container_port")),  # Lambda는 1024 미만 포트를 못 연다
        ("ec2", {**EC2_GOOD, "instance_type": "t2.micro"}, without(EC2_GOOD, "instance_type")),  # 템플릿 밖 서버 크기
        # vm은 배포 레포 vm_plan.py check_values 규칙
        ("vm", {**VM_GOOD, "app_port": 80}, without(VM_GOOD, "app_port")),  # 일반 사용자라 1024 미만 포트를 못 연다
        ("vm", {**VM_GOOD, "runtime": "ruby"}, without(VM_GOOD, "runtime")),
        ("vm", {**VM_GOOD, "runtime_version": "11"}, without(VM_GOOD, "runtime_version")),
        ("vm", {**VM_GOOD, "start_command": "npm start\nrm -rf /"}, without(VM_GOOD, "start_command")),  # 한 줄만
        ("vm", {**VM_GOOD, "build_command": None}, without(VM_GOOD, "build_command")),
        ("vm", {**VM_GOOD, "health_check_path": "health"}, without(VM_GOOD, "health_check_path")),
    ],
)
def test_wrong_template_values_fall_back_per_field(repo_files, model, compute, given, expected):
    model(output(template_values={compute: given}))
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert result.status == "done" and result.template_values[compute] == expected


@pytest.mark.parametrize("over", [{}, {"template_values": ["모양이 틀림"]}])
def test_missing_template_values_are_empty(repo_files, model, over):
    model(output(**over))
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert result.status == "done" and result.template_values == {compute: {} for compute in TEMPLATE_FIELDS}


def test_failed_has_no_template_values(repo_files, model):
    model(output(status="failed", requirements=[], evidence=[], candidates=[], mascot_message="웹 앱이 아니에요",
                 template_values={"ecs-fargate": GOOD}))
    result, _ = run_analysis(URL, "main", COMPUTES)
    assert result.template_values == {}


def test_template_fields_match_catalog():
    """양식의 이름이 catalog(= variables.tf)와 다르면 AI 값이 오류 없이 버려진다. 템플릿을 추가할 때 여기서 잡는다."""
    for compute, fields in TEMPLATE_FIELDS.items():
        assert catalog.is_ready(compute), compute
        assert set(catalog.fill_values(compute)) == set(fields), compute
    assert set(analyze.RESULT_SCHEMA["properties"]["template_values"]["required"]) == set(TEMPLATE_FIELDS)
