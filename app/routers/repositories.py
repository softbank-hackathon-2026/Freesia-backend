"""저장소 등록 (통합 메뉴). public GitHub 저장소 주소를 목록에 적어 두고, 앱을 만들 때 고른다."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.ids import new_id, now
from app.schemas import Repository, RepositoryCreate, parse_github_url

router = APIRouter(prefix="/repositories", tags=["repositories"])

ALREADY_EXISTS = {"error": "repository_exists", "message": "이미 등록된 저장소입니다."}


@router.get("", response_model=list[Repository], summary="등록된 저장소 목록")
def list_repositories(db: Session = Depends(get_db)) -> list[models.Repository]:
    stmt = select(models.Repository).order_by(models.Repository.created_at.desc())
    return list(db.scalars(stmt))


@router.post("", response_model=Repository, status_code=status.HTTP_201_CREATED, summary="저장소 등록")
def create_repository(body: RepositoryCreate, db: Session = Depends(get_db)) -> models.Repository:
    exists = db.scalar(
        select(models.Repository).where(
            models.Repository.repo_url == body.repo_url, models.Repository.branch == body.branch
        )
    )
    if exists:
        raise HTTPException(409, detail=ALREADY_EXISTS)
    owner, repo = parse_github_url(body.repo_url)
    item = models.Repository(
        id=new_id("repo"), owner=owner, repo=repo, repo_url=body.repo_url, branch=body.branch, created_at=now()
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # 동시에 같은 저장소를 등록한 경우
        db.rollback()
        raise HTTPException(409, detail=ALREADY_EXISTS)
    return item


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT, summary="저장소 등록 해제")
def delete_repository(repository_id: str, db: Session = Depends(get_db)) -> Response:
    item = db.get(models.Repository, repository_id)
    if item is None:
        raise HTTPException(404, detail={"error": "repository_not_found", "message": "저장소를 찾을 수 없습니다."})
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
