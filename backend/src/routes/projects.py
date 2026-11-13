from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import ALL_ROLES, PROJECT_ADJUDICATE_ROLES
from src.controllers import project_controller
from src.middlewares.rbac import Principal, require_roles
from src.schemas import ProjectAdjudicateIn

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects(
    q: str | None = None,
    region: str | None = None,
    category: str | None = None,
    status: str | None = None,
    conflict: str | None = None,
    page: int = 1,
    page_size: int = 25,
    principal: Principal = Depends(require_roles(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return project_controller.list_projects(
        db,
        {
            "q": q,
            "region": region,
            "category": category,
            "status": status,
            "conflict": conflict,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/stats")
def project_stats(
    principal: Principal = Depends(require_roles(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return project_controller.project_stats(db)


@router.get("/filter-options")
def filter_options(
    principal: Principal = Depends(require_roles(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return project_controller.filter_options(db)


@router.get("/{project_id}")
def get_project(
    project_id: int,
    principal: Principal = Depends(require_roles(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return project_controller.get_project(db, project_id)


@router.post("/{project_id}/decisions")
def adjudicate(
    project_id: int,
    payload: ProjectAdjudicateIn,
    request: Request,
    principal: Principal = Depends(require_roles(*PROJECT_ADJUDICATE_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return project_controller.adjudicate(
        db, project_id, payload.model_dump(exclude_none=True), principal.email, principal.role, request
    )


@router.delete("/{project_id}/decisions/{field_key}")
def reset_decision(
    project_id: int,
    field_key: str,
    request: Request,
    principal: Principal = Depends(require_roles(*PROJECT_ADJUDICATE_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return project_controller.reset(
        db, project_id, field_key, principal.email, principal.role, request
    )
