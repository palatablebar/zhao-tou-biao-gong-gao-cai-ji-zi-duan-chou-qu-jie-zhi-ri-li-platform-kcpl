"""项目（跨来源归并主体）查询与人工裁定。

- 列表/详情：聚合各来源、字段裁决依据、版本链、冲突与待裁定字段；
- 人工裁定：把裁决置为 manual，采集批次只更新候选、不再覆盖；
- 撤回归档：项目级撤回（联动日历事件停用）。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    FIELD_ORDER,
    DecisionBasis,
    DecisionStatus,
    FieldKey,
    ProjectStatus,
    SourceStatus,
)
from src.constants.fieldspec import FIELD_SPEC
from src.models.deadline_event import DeadlineEvent
from src.models.notice import Notice
from src.models.notice_version import NoticeVersion
from src.models.project import Project
from src.models.project_field_decision import ProjectFieldDecision
from src.models.project_source import ProjectSource
from src.models.site import Site
from src.services import extraction_engine
from src.utils.errors import AppError
from src.utils.timeutil import iso_local, now_local, parse_local

DECISION_STATUS_LABEL = {
    DecisionStatus.AUTO.value: "系统裁决",
    DecisionStatus.MANUAL.value: "人工确认",
    DecisionStatus.CONFLICT.value: "来源冲突",
    DecisionStatus.PENDING.value: "待补全",
}

BASIS_LABEL = {
    DecisionBasis.SINGLE_SOURCE.value: "唯一来源",
    DecisionBasis.HIGHEST_CONFIDENCE.value: "置信度最高",
    DecisionBasis.LATEST_PUBLISHED.value: "发布时间最新",
    DecisionBasis.MANUAL_CONFIRM.value: "人工确认",
    DecisionBasis.MANUAL_OVERRIDE.value: "人工修正",
    DecisionBasis.NONE.value: "无可用值",
}


def _site_map(db: Session, site_ids) -> dict[int, Site]:
    if not site_ids:
        return {}
    return {s.id: s for s in db.scalars(select(Site).where(Site.id.in_(site_ids))).all()}


def _notice_map(db: Session, notice_ids) -> dict[int, Notice]:
    if not notice_ids:
        return {}
    return {n.id: n for n in db.scalars(select(Notice).where(Notice.id.in_(notice_ids))).all()}


def serialize_decision(decision: ProjectFieldDecision) -> dict:
    return {
        "id": decision.id,
        "project_id": decision.project_id,
        "field_key": decision.field_key,
        "label": FIELD_SPEC.get(decision.field_key, {}).get("label", decision.field_key),
        "status": decision.status,
        "status_label": DECISION_STATUS_LABEL.get(decision.status, decision.status),
        "basis": decision.basis,
        "basis_label": BASIS_LABEL.get(decision.basis, decision.basis),
        "value_text": decision.value_text,
        "unit": decision.unit,
        "amount_yuan": decision.amount_yuan,
        "amount_wan": round((decision.amount_yuan or 0) / 10000, 2) if decision.amount_yuan else None,
        "event_at": iso_local(decision.event_at),
        "precision": decision.precision,
        "is_estimated": decision.is_estimated,
        "confidence": decision.confidence,
        "source_notice_id": decision.source_notice_id,
        "source_site_id": decision.source_site_id,
        "reason": decision.reason,
        "reviewer": decision.reviewer,
        "reviewed_at": iso_local(decision.reviewed_at),
        "candidates": decision.candidates or [],
        "conflict": decision.status == DecisionStatus.CONFLICT.value,
        "pending": decision.status == DecisionStatus.PENDING.value,
    }


def _serialize_source(db: Session, source: ProjectSource, notices: dict[int, Notice], sites: dict[int, Site]) -> dict:
    notice = notices.get(source.notice_id)
    site = sites.get(source.site_id)
    versions = db.scalars(
        select(NoticeVersion)
        .where(NoticeVersion.notice_id == source.notice_id)
        .order_by(NoticeVersion.version_no)
    ).all()
    return {
        "id": source.id,
        "site_id": source.site_id,
        "site_name": site.name if site else "",
        "site_domain": site.domain if site else "",
        "region": site.region if site else "",
        "notice_id": source.notice_id,
        "external_id": source.external_id,
        "status": source.status,
        "current_version_no": source.current_version_no,
        "content_hash": source.content_hash,
        "first_seen_at": iso_local(source.first_seen_at),
        "last_seen_at": iso_local(source.last_seen_at),
        "withdrawn_at": iso_local(source.withdrawn_at),
        "notice_status": notice.status if notice else None,
        "notice_title": notice.title if notice else "",
        "url": notice.url if notice else "",
        "versions": [
            {
                "version_no": v.version_no,
                "status": v.status,
                "content_hash": v.content_hash,
                "change_note": v.change_note,
                "seen_at": iso_local(v.seen_at),
                "withdrawn_at": iso_local(v.withdrawn_at),
            }
            for v in versions
        ],
    }


def _project_summary(db: Session, project: Project) -> dict:
    decisions = db.scalars(
        select(ProjectFieldDecision)
        .where(ProjectFieldDecision.project_id == project.id)
        .order_by(ProjectFieldDecision.id)
    ).all()
    source_count = db.scalar(
        select(func.count())
        .select_from(ProjectSource)
        .where(
            ProjectSource.project_id == project.id,
            ProjectSource.status == SourceStatus.ACTIVE.value,
        )
    ) or 0
    enroll = db.scalar(
        select(DeadlineEvent)
        .where(DeadlineEvent.project_id == project.id, DeadlineEvent.kind == "enroll")
        .order_by(DeadlineEvent.id)
    )
    return {
        "id": project.id,
        "merge_key": project.merge_key,
        "display_name": project.display_name,
        "publisher": project.publisher,
        "region": project.region,
        "category": project.category,
        "industry": project.industry,
        "qualification": project.qualification,
        "status": project.status,
        "status_label": "已撤回" if project.status == ProjectStatus.WITHDRAWN.value else "跟踪中",
        "primary_notice_id": project.primary_notice_id,
        "active_source_count": source_count,
        "conflict_count": project.conflict_count,
        "pending_field_count": project.pending_field_count,
        "withdrawn_at": iso_local(project.withdrawn_at),
        "updated_at": iso_local(project.updated_at),
        "last_seen_at": iso_local(project.last_seen_at),
        "risk_level": enroll.risk_level if enroll else None,
        "risk_score": enroll.risk_score if enroll else None,
        "deadline_event_active": enroll.is_active if enroll else False,
        "decision_states": {d.field_key: d.status for d in decisions},
    }


def list_projects(
    db: Session,
    *,
    q: str | None = None,
    region: str | None = None,
    category: str | None = None,
    status: str | None = None,
    conflict: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    from src.constants.limits import MAX_PAGE_SIZE

    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    stmt = select(Project)
    if q:
        stmt = stmt.where(Project.display_name.like(f"%{q.strip()}%"))
    if region:
        stmt = stmt.where(Project.region == region)
    if category:
        stmt = stmt.where(Project.category == category)
    if status:
        stmt = stmt.where(Project.status == status)
    projects = db.scalars(stmt.order_by(Project.updated_at.desc(), Project.id.desc())).all()
    if conflict == "conflict":
        projects = [p for p in projects if p.conflict_count > 0]
    elif conflict == "pending":
        projects = [p for p in projects if p.pending_field_count > 0]
    total = len(projects)
    start = (page - 1) * page_size
    page_items = projects[start: start + page_size]
    return {
        "items": [_project_summary(db, project) for project in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def project_stats(db: Session) -> dict:
    total = db.scalar(select(func.count()).select_from(Project)) or 0
    active = db.scalar(
        select(func.count()).select_from(Project).where(Project.status == ProjectStatus.ACTIVE.value)
    ) or 0
    withdrawn = db.scalar(
        select(func.count()).select_from(Project).where(Project.status == ProjectStatus.WITHDRAWN.value)
    ) or 0
    conflict_projects = db.scalar(select(func.count(func.distinct(ProjectFieldDecision.project_id))).where(
        ProjectFieldDecision.status == DecisionStatus.CONFLICT.value
    )) or 0
    pending_fields = db.scalar(
        select(func.count()).select_from(ProjectFieldDecision).where(
            ProjectFieldDecision.status == DecisionStatus.PENDING.value
        )
    ) or 0
    conflict_fields = db.scalar(
        select(func.count()).select_from(ProjectFieldDecision).where(
            ProjectFieldDecision.status == DecisionStatus.CONFLICT.value
        )
    ) or 0
    manual_fields = db.scalar(
        select(func.count()).select_from(ProjectFieldDecision).where(
            ProjectFieldDecision.status == DecisionStatus.MANUAL.value
        )
    ) or 0
    multi_source = db.scalar(
        select(func.count())
        .select_from(ProjectSource)
        .where(ProjectSource.status == SourceStatus.ACTIVE.value)
    ) or 0
    return {
        "project_total": total,
        "active_total": active,
        "withdrawn_total": withdrawn,
        "conflict_project_total": conflict_projects,
        "conflict_field_total": conflict_fields,
        "pending_field_total": pending_fields,
        "manual_field_total": manual_fields,
        "active_source_total": multi_source,
    }


def get_project(db: Session, project_id: int) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise AppError("NOT_FOUND", 404, detail=f"project_id={project_id}")
    sources = db.scalars(
        select(ProjectSource).where(ProjectSource.project_id == project.id).order_by(ProjectSource.id)
    ).all()
    notice_ids = [s.notice_id for s in sources]
    site_ids = [s.site_id for s in sources]
    notices = _notice_map(db, notice_ids)
    sites = _site_map(db, site_ids)
    decisions = db.scalars(
        select(ProjectFieldDecision)
        .where(ProjectFieldDecision.project_id == project.id)
        .order_by(ProjectFieldDecision.id)
    ).all()
    events = db.scalars(
        select(DeadlineEvent).where(DeadlineEvent.project_id == project.id).order_by(DeadlineEvent.event_at)
    ).all()
    payload = _project_summary(db, project)
    payload["sources"] = [_serialize_source(db, s, notices, sites) for s in sources]
    payload["decisions"] = [serialize_decision(d) for d in decisions]
    payload["deadlines"] = [
        {
            "id": e.id,
            "kind": e.kind,
            "event_at": iso_local(e.event_at),
            "risk_level": e.risk_level,
            "risk_score": e.risk_score,
            "is_active": e.is_active,
            "inactive_reason": e.inactive_reason,
            "note": e.note,
        }
        for e in events
    ]
    return payload


def _get_decision(db: Session, project_id: int, field_key: str) -> ProjectFieldDecision:
    if field_key not in FIELD_ORDER:
        raise AppError("VALIDATION", 422, detail=f"未知字段：{field_key}")
    decision = db.scalar(
        select(ProjectFieldDecision).where(
            ProjectFieldDecision.project_id == project_id,
            ProjectFieldDecision.field_key == field_key,
        )
    )
    if decision is None:
        raise AppError("NOT_FOUND", 404, detail=f"project {project_id} field {field_key} 尚未裁决")
    return decision


def adjudicate(db: Session, project_id: int, field_key: str, payload: dict, reviewer: str) -> dict:
    """人工裁定：可指定采用某来源候选（source_notice_id）或直接给值。"""
    project = db.get(Project, project_id)
    if project is None:
        raise AppError("NOT_FOUND", 404, detail=f"project_id={project_id}")
    decision = _get_decision(db, project_id, field_key)
    raw_value = (payload.get("value") or "").strip()
    source_notice_id = payload.get("source_notice_id")

    previous = decision.value_text
    basis = DecisionBasis.MANUAL_OVERRIDE.value
    if source_notice_id:
        candidate = next((c for c in (decision.candidates or []) if c["notice_id"] == source_notice_id), None)
        if candidate is None:
            raise AppError("VALIDATION", 422, detail="source_notice_id 不在该字段候选来源中")
        decision.value_text = candidate["value_text"]
        decision.unit = candidate.get("unit")
        decision.amount_yuan = candidate.get("amount_yuan")
        decision.event_at = parse_local(candidate.get("event_at"))
        decision.precision = candidate.get("precision") or "missing"
        decision.is_estimated = bool(candidate.get("is_estimated"))
        decision.confidence = candidate["confidence"]
        decision.source_notice_id = candidate["notice_id"]
        decision.source_site_id = candidate["site_id"]
        basis = DecisionBasis.MANUAL_CONFIRM.value
        raw_value = candidate["value_text"] or ""
    elif raw_value:
        _apply_manual_value(decision, field_key, raw_value, payload.get("unit"), project)
    else:
        raise AppError("VALIDATION", 422, detail="需提供 value 或 source_notice_id")

    decision.status = DecisionStatus.MANUAL.value
    decision.basis = basis
    decision.reviewer = reviewer
    decision.reviewed_at = now_local()
    decision.reason = (
        f"人工{('确认' if basis == DecisionBasis.MANUAL_CONFIRM.value else '修正')}"
        f"（{reviewer}）：{previous or '空'} → {decision.value_text}；后续采集不再覆盖"
    )[:400]
    db.flush()
    _refresh_project_flags(db, project)
    return {"decision": serialize_decision(decision), "previous_value": previous}


def _apply_manual_value(decision: ProjectFieldDecision, field_key: str, raw_value: str, unit: str | None, project: Project) -> None:
    if len(raw_value) > 255:
        raise AppError("VALIDATION", 422, detail="value 超过 255 字符")
    if field_key == FieldKey.BUDGET_AMOUNT.value:
        amount_yuan, resolved_unit, normalized = extraction_engine.parse_amount_text(raw_value, unit)
        if amount_yuan is None:
            raise AppError("VALIDATION", 422, detail="无法解析金额（需带万元/元等单位）")
        decision.value_text = normalized if unit else raw_value
        decision.unit = resolved_unit
        decision.amount_yuan = amount_yuan
        decision.event_at = None
        decision.precision = "absolute"
        decision.is_estimated = False
    elif field_key == FieldKey.DEADLINE.value:
        event_at, precision, estimated = extraction_engine.parse_deadline_text(raw_value, None)
        if event_at is None:
            raise AppError("VALIDATION", 422, detail="无法解析日期")
        decision.value_text = raw_value
        decision.event_at = event_at
        decision.precision = precision
        decision.is_estimated = estimated
        decision.amount_yuan = None
        decision.unit = None
    else:
        decision.value_text = raw_value
    decision.source_notice_id = None
    decision.source_site_id = None
    decision.confidence = 1.0


def reset_decision(db: Session, project_id: int, field_key: str, reviewer: str) -> dict:
    """撤销人工裁定，恢复为系统自动裁决（下一次采集/重算生效）。"""
    decision = _get_decision(db, project_id, field_key)
    if decision.status != DecisionStatus.MANUAL.value:
        raise AppError("CONFLICT", 409, detail="该字段不是人工裁定，无需撤销")
    decision.status = DecisionStatus.AUTO.value
    decision.basis = DecisionBasis.NONE.value
    decision.reviewer = None
    decision.reviewed_at = None
    decision.reason = f"人工裁定已由 {reviewer} 撤销，恢复系统自动裁决"
    db.flush()
    project = db.get(Project, project_id)
    # 立即用现有候选重算一次
    from src.services.merge_service import arbitrate_project, MergeStats

    arbitrate_project(db, project, MergeStats())
    _refresh_project_flags(db, project)
    return {"decision": serialize_decision(decision)}


def _refresh_project_flags(db: Session, project: Project) -> None:
    project.conflict_count = db.scalar(
        select(func.count())
        .select_from(ProjectFieldDecision)
        .where(
            ProjectFieldDecision.project_id == project.id,
            ProjectFieldDecision.status == DecisionStatus.CONFLICT.value,
        )
    ) or 0
    project.pending_field_count = db.scalar(
        select(func.count())
        .select_from(ProjectFieldDecision)
        .where(
            ProjectFieldDecision.project_id == project.id,
            ProjectFieldDecision.status == DecisionStatus.PENDING.value,
        )
    ) or 0
    db.flush()


def filter_options(db: Session) -> dict:
    regions = [r for (r,) in db.execute(select(Project.region).distinct()).all() if r]
    categories = [c for (c,) in db.execute(select(Project.category).distinct()).all() if c]
    return {
        "regions": regions,
        "categories": categories,
        "statuses": [ProjectStatus.ACTIVE.value, ProjectStatus.WITHDRAWN.value],
        "decision_status": DECISION_STATUS_LABEL,
        "basis": BASIS_LABEL,
    }
