"""公告：看板列表（筛选 + 置信度标记）与详情（正文高亮 + 字段核对）。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.constants.enums import FIELD_ORDER, FieldKey, FieldStatus, NoticeStatus, RiskLevel
from src.constants.fieldspec import FIELD_SPEC
from src.constants.limits import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from src.models.deadline_event import DeadlineEvent
from src.models.extracted_field import ExtractedField
from src.models.notice import Notice
from src.models.notice_version import NoticeVersion
from src.models.project import Project
from src.models.project_field_decision import ProjectFieldDecision
from src.models.project_source import ProjectSource
from src.models.site import Site
from src.services import field_service
from src.utils.errors import AppError
from src.utils.textutil import comma_number
from src.utils.timeutil import iso_local


def _parse_date(value: str | None):
    from src.utils.timeutil import parse_local

    return parse_local(value)


def _project_contexts(db: Session, notice_ids: list[int]) -> dict[int, dict]:
    """批量取每条公告所属项目的归并上下文（来源数/项目状态/冲突/待裁定）。"""
    if not notice_ids:
        return {}
    rows = db.execute(
        select(ProjectSource, Project)
        .join(Project, Project.id == ProjectSource.project_id)
        .where(ProjectSource.notice_id.in_(notice_ids))
    ).all()
    project_ids = {project.id for _, project in rows}
    project_source_totals = dict(
        db.execute(
            select(ProjectSource.project_id, func.count())
            .where(ProjectSource.project_id.in_(project_ids))
            .group_by(ProjectSource.project_id)
        ).all()
    ) if project_ids else {}
    project_conflicts = dict(
        db.execute(
            select(ProjectFieldDecision.project_id, func.count(ProjectFieldDecision.id))
            .where(
                ProjectFieldDecision.project_id.in_(project_ids),
                ProjectFieldDecision.status == "conflict",
            )
            .group_by(ProjectFieldDecision.project_id)
        ).all()
    ) if project_ids else {}
    project_pending = dict(
        db.execute(
            select(ProjectFieldDecision.project_id, func.count(ProjectFieldDecision.id))
            .where(
                ProjectFieldDecision.project_id.in_(project_ids),
                ProjectFieldDecision.status == "pending",
            )
            .group_by(ProjectFieldDecision.project_id)
        ).all()
    ) if project_ids else {}
    result: dict[int, dict] = {}
    for source, project in rows:
        result[source.notice_id] = {
            "project_id": project.id,
            "project_status": project.status,
            "source_count": project_source_totals.get(project.id, 1),
            "conflict_count": project_conflicts.get(project.id, 0),
            "pending_field_count": project_pending.get(project.id, 0),
        }
    return result


def _fields_by_notice(db: Session, notice_ids: list[int]) -> dict[int, list[ExtractedField]]:
    if not notice_ids:
        return {}
    rows = db.scalars(
        select(ExtractedField).where(ExtractedField.notice_id.in_(notice_ids))
    ).all()
    grouped: dict[int, list[ExtractedField]] = {}
    for row in rows:
        grouped.setdefault(row.notice_id, []).append(row)
    return grouped


def _events_by_notice(db: Session, notice_ids: list[int]) -> dict[int, list[DeadlineEvent]]:
    if not notice_ids:
        return {}
    rows = db.scalars(select(DeadlineEvent).where(DeadlineEvent.notice_id.in_(notice_ids))).all()
    grouped: dict[int, list[DeadlineEvent]] = {}
    for row in rows:
        grouped.setdefault(row.notice_id, []).append(row)
    return grouped


def _aggregate(fields: list[ExtractedField], events: list[DeadlineEvent]) -> dict:
    low_count = sum(
        1
        for field in fields
        if field.confidence < settings.low_confidence_threshold
        and field.status == FieldStatus.EXTRACTED.value
    )
    confidences = [field.confidence for field in fields]
    budget = next((field for field in fields if field.field_key == FieldKey.BUDGET_AMOUNT.value), None)
    deadline = next((field for field in fields if field.field_key == FieldKey.DEADLINE.value), None)
    enroll = next((event for event in events if event.kind == "enroll"), None)
    risk = None
    if enroll is not None:
        risk = {
            "risk_level": enroll.risk_level,
            "risk_score": enroll.risk_score,
            "deadline_pressure": enroll.deadline_pressure,
            "work_hours_remaining": enroll.work_hours_remaining,
            "event_at": iso_local(enroll.event_at),
        }
    return {
        "field_count": len(fields),
        "low_confidence_count": low_count,
        "min_confidence": round(min(confidences), 3) if confidences else 0.0,
        "max_confidence": round(max(confidences), 3) if confidences else 0.0,
        "reviewed_count": sum(
            1
            for field in fields
            if field.status in (FieldStatus.VERIFIED.value, FieldStatus.CORRECTED.value)
        ),
        "budget_text": budget.value_text if budget else None,
        "budget_wan": round((budget.amount_yuan or 0) / 10000, 2) if budget and budget.amount_yuan else None,
        "budget_display": (
            f"{comma_number((budget.amount_yuan or 0) / 10000, 2)} 万元"
            if budget and budget.amount_yuan
            else "—"
        ),
        "deadline_text": deadline.value_text if deadline else None,
        "deadline_precision": deadline.precision if deadline else "missing",
        "deadline_is_estimated": bool(deadline.is_estimated) if deadline else False,
        "deadline_missing": bool(deadline is None or deadline.value_text is None),
        "risk": risk,
    }


def serialize_notice_item(
    db: Session,
    notice: Notice,
    site_name: str,
    fields: list[ExtractedField],
    events: list[DeadlineEvent],
    project_ctx: dict | None = None,
) -> dict:
    return {
        "id": notice.id,
        "external_id": notice.external_id,
        "title": notice.title,
        "url": notice.url,
        "publisher": notice.publisher,
        "site_id": notice.site_id,
        "site_name": site_name,
        "region": notice.region,
        "category": notice.category,
        "industry": notice.industry,
        "qualification": notice.qualification,
        "status": notice.status,
        "body_kind": notice.body_kind,
        "published_at": iso_local(notice.published_at),
        "byte_size": notice.byte_size,
        "crawl_run_id": notice.crawl_run_id,
        "current_version_no": notice.current_version_no,
        "withdrawn_at": iso_local(notice.withdrawn_at),
        "project": project_ctx,
        "aggregate": _aggregate(fields, events),
    }


def list_notices(
    db: Session,
    *,
    q: str | None = None,
    site_id: int | None = None,
    region: str | None = None,
    category: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    stmt = select(Notice, Site.name).join(Site, Site.id == Notice.site_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Notice.title.like(like) | Notice.external_id.like(like))
    if site_id:
        stmt = stmt.where(Notice.site_id == site_id)
    if region:
        stmt = stmt.where(Notice.region == region)
    if category:
        stmt = stmt.where(Notice.category == category)
    if status:
        stmt = stmt.where(Notice.status == status)
    stmt = stmt.order_by(Notice.published_at.desc(), Notice.id.desc())
    rows = db.execute(stmt).all()
    notices = [(notice, site_name) for notice, site_name in rows]
    notice_ids = [notice.id for notice, _ in notices]
    fields_map = _fields_by_notice(db, notice_ids)
    events_map = _events_by_notice(db, notice_ids)
    project_ctx_map = _project_contexts(db, notice_ids)
    items: list[dict] = []
    for notice, site_name in notices:
        fields = fields_map.get(notice.id, [])
        events = events_map.get(notice.id, [])
        item = serialize_notice_item(
            db, notice, site_name, fields, events, project_ctx_map.get(notice.id)
        )
        if confidence == "low" and item["aggregate"]["low_confidence_count"] == 0:
            continue
        if confidence == "clean" and item["aggregate"]["low_confidence_count"] > 0:
            continue
        items.append(item)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start: start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "threshold": settings.low_confidence_threshold,
    }


def _body_lines(notice: Notice, fields: list[ExtractedField]) -> list[dict]:
    anchors_by_field: dict[str, list[str]] = {
        key: list(FIELD_SPEC[key]["anchors"]) for key in FIELD_ORDER
    }
    lines = [line for line in notice.clean_text.split("\n") if line.strip()]
    rendered: list[dict] = []
    for line in lines:
        keys: list[str] = []
        for field_key, anchors in anchors_by_field.items():
            if any(anchor in line for anchor in anchors):
                keys.append(field_key)
            else:
                field = next((item for item in fields if item.field_key == field_key), None)
                if field and field.raw_text and field.raw_text in line:
                    keys.append(field_key)
        rendered.append({"text": line, "fields": sorted(set(keys))})
    return rendered


def get_notice_detail(db: Session, notice_id: int) -> dict:
    notice = db.get(Notice, notice_id)
    if notice is None:
        raise AppError("NOT_FOUND", 404, detail=f"notice_id={notice_id}")
    site = db.get(Site, notice.site_id)
    fields = db.scalars(
        select(ExtractedField)
        .where(ExtractedField.notice_id == notice.id)
        .order_by(ExtractedField.id)
    ).all()
    events = db.scalars(
        select(DeadlineEvent)
        .where(DeadlineEvent.notice_id == notice.id)
        .order_by(DeadlineEvent.event_at)
    ).all()
    item = serialize_notice_item(
        db,
        notice,
        site.name if site else "",
        list(fields),
        list(events),
        _project_contexts(db, [notice.id]).get(notice.id),
    )
    item["fields"] = [field_service.serialize_field(db, field, notice) for field in fields]
    item["deadlines"] = [
        {
            "id": event.id,
            "kind": event.kind,
            "event_at": iso_local(event.event_at),
            "precision": event.precision,
            "is_active": event.is_active,
            "inactive_reason": event.inactive_reason,
            "risk_level": event.risk_level,
            "risk_score": event.risk_score,
            "deadline_pressure": event.deadline_pressure,
            "qualify_gap": event.qualify_gap,
            "work_hours_remaining": event.work_hours_remaining,
            "note": event.note,
            "computed_at": iso_local(event.computed_at),
        }
        for event in events
    ]
    versions = db.scalars(
        select(NoticeVersion)
        .where(NoticeVersion.notice_id == notice.id)
        .order_by(NoticeVersion.version_no)
    ).all()
    item["versions"] = [
        {
            "version_no": v.version_no,
            "status": v.status,
            "content_hash": v.content_hash,
            "change_note": v.change_note,
            "byte_size": v.byte_size,
            "seen_at": iso_local(v.seen_at),
            "withdrawn_at": iso_local(v.withdrawn_at),
        }
        for v in versions
    ]
    item["body_lines"] = _body_lines(notice, list(fields))
    item["site"] = {
        "id": site.id if site else None,
        "name": site.name if site else "",
        "domain": site.domain if site else "",
        "region": site.region if site else "",
    }
    item["low_confidence_count"] = item["aggregate"]["low_confidence_count"]
    return item


def status_options() -> list[str]:
    return [status.value for status in NoticeStatus]


def risk_options() -> list[str]:
    return [level.value for level in RiskLevel]


def parse_date(value: str | None):
    return _parse_date(value)
