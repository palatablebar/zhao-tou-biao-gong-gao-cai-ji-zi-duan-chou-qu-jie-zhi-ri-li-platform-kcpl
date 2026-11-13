"""项目（跨来源归并）控制器。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, project_service


def list_projects(db: Session, filters: dict) -> dict:
    return project_service.list_projects(db, **filters)


def project_stats(db: Session) -> dict:
    return project_service.project_stats(db)


def filter_options(db: Session) -> dict:
    return project_service.filter_options(db)


def get_project(db: Session, project_id: int) -> dict:
    return project_service.get_project(db, project_id)


def adjudicate(
    db: Session, project_id: int, payload: dict, actor_email: str, actor_role: str, request: Request
) -> dict:
    result = project_service.adjudicate(
        db, project_id, payload["field_key"], payload, actor_email
    )
    audit_service.record(
        db,
        actor_email=actor_email,
        actor_role=actor_role,
        action="project.adjudicate",
        entity="project",
        entity_id=project_id,
        detail={
            "field_key": payload["field_key"],
            "previous": result["previous_value"],
            "value": result["decision"]["value_text"],
            "basis": result["decision"]["basis"],
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return result


def reset(
    db: Session, project_id: int, field_key: str, actor_email: str, actor_role: str, request: Request
) -> dict:
    result = project_service.reset_decision(db, project_id, field_key, actor_email)
    audit_service.record(
        db,
        actor_email=actor_email,
        actor_role=actor_role,
        action="project.reset_decision",
        entity="project",
        entity_id=project_id,
        detail={"field_key": field_key},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return result
