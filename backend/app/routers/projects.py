from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Project
from app.schemas import ProjectCreate, ProjectRead

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
def list_projects(active_only: bool = True, db: Session = Depends(get_db)):
    query = select(Project).order_by(Project.name.asc())
    if active_only:
        query = query.where(Project.is_active.is_(True))
    return db.scalars(query).all()


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(
        name=payload.name.strip(),
        code=payload.code.strip() if payload.code else None,
        description=payload.description,
    )
    db.add(project)
    try:
        db.commit()
        db.refresh(project)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A project with this name or code already exists.")
    return project
