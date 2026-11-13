"""跨来源项目归并与字段裁决。

职责：
1. 把同一项目在多个交易中心的公告（notice）归并到一个 Project；
2. 维护 ProjectSource（各来源当前快照）与 NoticeVersion（来源内版本链）；
3. 对 4 个结构化字段做来源裁决，生成 ProjectFieldDecision：
   - 人工确认（manual）永远优先，采集只更新候选、不覆盖裁决值；
   - 自动裁决：唯一来源直接取；多来源取置信度最高，相同取发布时间最新；
   - 取值不一致且无人工裁定时标记 conflict；全部缺失标记 pending；
4. 来源撤下 / 更正时联动项目状态与日历事件（撤回项目移出必投名单）。

所有写操作基于自然键 upsert，重复执行不新增项目、来源、裁决或日历事件。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    FIELD_ORDER,
    DecisionBasis,
    DecisionStatus,
    NoticeStatus,
    ProjectStatus,
    SourceStatus,
    VersionStatus,
)
from src.models.extracted_field import ExtractedField
from src.models.notice import Notice
from src.models.notice_version import NoticeVersion
from src.models.project import Project
from src.models.project_field_decision import ProjectFieldDecision
from src.models.project_source import ProjectSource
from src.models.site import Site
from src.services import source_catalog
from src.utils.timeutil import now_local


# ---------------------------------------------------------------------------
# 归并键
# ---------------------------------------------------------------------------

def merge_key_for_notice(notice: Notice) -> str:
    """归并键：优先用入库时持久化的 merge_key；缺失时回退到名称+招标人归一化。"""
    if notice.merge_key:
        return notice.merge_key
    if notice.seed_key and notice.seed_key.startswith("notice:"):
        try:
            index = int(notice.seed_key.split(":", 1)[1])
            return source_catalog.merge_key_for(index, notice.publisher)
        except (ValueError, IndexError):
            pass
    # mirror 或无 seed_key：用项目名 + 招标人归一化
    return _merge_key_from_text(notice.title, notice.publisher)


def _merge_key_from_text(title: str, publisher: str) -> str:
    import hashlib

    name = source_catalog.normalize_project_name(title.replace("招标公告", ""))
    purchaser = source_catalog.normalize_project_name(publisher)
    return hashlib.sha256(f"{name}|{purchaser}".encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 版本链
# ---------------------------------------------------------------------------

def ensure_version(
    db: Session,
    notice: Notice,
    *,
    run_id: int | None,
    change_note: str | None = None,
) -> str:
    """确保 notice 当前正文存在对应 active 版本；返回 inserted/existed/superseded。"""
    active = db.scalar(
        select(NoticeVersion).where(
            NoticeVersion.notice_id == notice.id,
            NoticeVersion.status == VersionStatus.ACTIVE.value,
        )
    )
    if active is not None and active.content_hash == notice.content_hash:
        return "existed"
    if active is not None:
        active.status = VersionStatus.SUPERSEDED.value
    existing_versions = db.scalars(
        select(NoticeVersion).where(NoticeVersion.notice_id == notice.id)
    ).all()
    next_no = max((row.version_no for row in existing_versions), default=0) + 1
    db.add(
        NoticeVersion(
            notice_id=notice.id,
            version_no=next_no,
            status=VersionStatus.ACTIVE.value,
            content_hash=notice.content_hash,
            raw_html=notice.raw_html,
            clean_text=notice.clean_text,
            byte_size=notice.byte_size,
            change_note=change_note,
            crawl_run_id=run_id,
        )
    )
    notice.current_version_no = next_no
    db.flush()
    return "superseded" if active is not None else "inserted"


def mark_notice_withdrawn(db: Session, notice: Notice, *, run_id: int | None, reason: str) -> None:
    """来源撤下：notice 与当前版本标记 withdrawn，保留历史。"""
    if notice.status == NoticeStatus.WITHDRAWN.value:
        return
    notice.status = NoticeStatus.WITHDRAWN.value
    notice.withdrawn_at = now_local()
    active = db.scalar(
        select(NoticeVersion).where(
            NoticeVersion.notice_id == notice.id,
            NoticeVersion.status == VersionStatus.ACTIVE.value,
        )
    )
    if active is not None:
        active.status = VersionStatus.WITHDRAWN.value
        active.withdrawn_at = now_local()
        if reason and not active.change_note:
            active.change_note = reason[:255]
    db.flush()


# ---------------------------------------------------------------------------
# 项目与来源 upsert
# ---------------------------------------------------------------------------

@dataclass
class MergeStats:
    projects_created: int = 0
    sources_created: int = 0
    sources_updated: int = 0
    decisions_auto: int = 0
    decisions_conflict: int = 0
    decisions_pending: int = 0
    manual_preserved: int = 0
    withdrawn: int = 0
    corrected: int = 0


def get_or_create_project(db: Session, merge_key: str, payload: dict) -> tuple[Project, bool]:
    project = db.scalar(select(Project).where(Project.merge_key == merge_key))
    if project is not None:
        return project, False
    project = Project(
        merge_key=merge_key,
        display_name=_project_display_name(payload),
        publisher=payload.get("publisher", ""),
        region=payload.get("region", ""),
        category=payload.get("category", ""),
        industry=payload.get("industry", ""),
        qualification=payload.get("qualification", "none"),
        status=ProjectStatus.ACTIVE.value,
    )
    db.add(project)
    db.flush()
    return project, True


def _project_display_name(payload: dict) -> str:
    title = payload.get("title", "")
    return title.replace("招标公告", "").strip() or payload.get("external_id", "")


def upsert_project_source(
    db: Session,
    project: Project,
    notice: Notice,
    site: Site,
    *,
    run_id: int,
) -> tuple[ProjectSource, str]:
    source = db.scalar(
        select(ProjectSource).where(
            ProjectSource.project_id == project.id,
            ProjectSource.site_id == site.id,
        )
    )
    now = now_local()
    if source is None:
        source = ProjectSource(
            project_id=project.id,
            site_id=site.id,
            notice_id=notice.id,
            external_id=notice.external_id,
            status=SourceStatus.ACTIVE.value,
            current_version_no=notice.current_version_no,
            content_hash=notice.content_hash,
            first_seen_run_id=run_id,
            last_seen_run_id=run_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(source)
        db.flush()
        return source, "inserted"
    outcome = "existed"
    if source.notice_id != notice.id or source.content_hash != notice.content_hash:
        outcome = "updated"
    source.notice_id = notice.id
    source.external_id = notice.external_id
    source.content_hash = notice.content_hash
    source.current_version_no = notice.current_version_no
    source.status = SourceStatus.ACTIVE.value
    source.withdrawn_at = None
    source.last_seen_run_id = run_id
    source.last_seen_at = now
    db.flush()
    return source, outcome


def mark_source_withdrawn(db: Session, source: ProjectSource, reason: str) -> None:
    if source.status == SourceStatus.WITHDRAWN.value:
        return
    source.status = SourceStatus.WITHDRAWN.value
    source.withdrawn_at = now_local()
    db.flush()


# ---------------------------------------------------------------------------
# 字段裁决
# ---------------------------------------------------------------------------

def _candidate_from_field(field: ExtractedField, notice: Notice, site: Site) -> dict:
    return {
        "notice_id": notice.id,
        "site_id": site.id,
        "site_name": site.name,
        "external_id": notice.external_id,
        "value_text": field.value_text,
        "amount_yuan": field.amount_yuan,
        "event_at": field.event_at.isoformat(timespec="seconds") if field.event_at else None,
        "unit": field.unit,
        "precision": field.precision,
        "is_estimated": field.is_estimated,
        "confidence": round(field.confidence, 3),
        "field_status": field.status,
        "published_at": notice.published_at.isoformat(timespec="seconds"),
        "notice_status": notice.status,
    }


def _values_equal(field_key: str, a: dict, b: dict) -> bool:
    if field_key == "budget_amount":
        if a.get("amount_yuan") is not None and b.get("amount_yuan") is not None:
            return abs(float(a["amount_yuan"]) - float(b["amount_yuan"])) < 0.01
        return (a.get("value_text") or "") == (b.get("value_text") or "")
    if field_key == "deadline":
        if a.get("event_at") and b.get("event_at"):
            return a["event_at"] == b["event_at"]
        return (a.get("value_text") or "") == (b.get("value_text") or "")
    return (a.get("value_text") or "") == (b.get("value_text") or "")


def _pick_winner(field_key: str, candidates: list[dict]) -> tuple[dict, str]:
    """自动裁决：置信度最高，相同置信度取发布时间最新。"""
    ordered = sorted(
        candidates,
        key=lambda item: (item["confidence"], item["published_at"]),
        reverse=True,
    )
    winner = ordered[0]
    tied = [item for item in ordered if abs(item["confidence"] - winner["confidence"]) < 1e-9]
    basis = (
        DecisionBasis.LATEST_PUBLISHED.value
        if len(tied) > 1
        else DecisionBasis.HIGHEST_CONFIDENCE.value
    )
    if len(candidates) == 1:
        basis = DecisionBasis.SINGLE_SOURCE.value
    return winner, basis


def _has_conflict(field_key: str, candidates: list[dict]) -> bool:
    valued = [item for item in candidates if item.get("value_text")]
    if len(valued) < 2:
        return False
    first = valued[0]
    return any(not _values_equal(field_key, first, other) for other in valued[1:])


def arbitrate_project(db: Session, project: Project, stats: MergeStats) -> None:
    """基于项目各有效来源的抽取字段，重算 4 个字段裁决（人工裁决不覆盖）。"""
    sources = db.scalars(
        select(ProjectSource).where(ProjectSource.project_id == project.id)
    ).all()
    active_sources = [s for s in sources if s.status == SourceStatus.ACTIVE.value]
    notice_ids = [s.notice_id for s in active_sources]
    notices = {
        n.id: n
        for n in db.scalars(select(Notice).where(Notice.id.in_(notice_ids))).all()
    } if notice_ids else {}
    site_ids = {n.site_id for n in notices.values()}
    sites = {s.id: s for s in db.scalars(select(Site).where(Site.id.in_(site_ids))).all()} if site_ids else {}
    fields = db.scalars(
        select(ExtractedField).where(ExtractedField.notice_id.in_(notice_ids))
    ).all() if notice_ids else []
    fields_by_notice: dict[int, dict[str, ExtractedField]] = {}
    for field in fields:
        fields_by_notice.setdefault(field.notice_id, {})[field.field_key] = field

    for field_key in FIELD_ORDER:
        decision = db.scalar(
            select(ProjectFieldDecision).where(
                ProjectFieldDecision.project_id == project.id,
                ProjectFieldDecision.field_key == field_key,
            )
        )
        candidates: list[dict] = []
        for source in active_sources:
            notice = notices.get(source.notice_id)
            if notice is None or notice.status == NoticeStatus.WITHDRAWN.value:
                continue
            field = fields_by_notice.get(source.notice_id, {}).get(field_key)
            if field is None:
                continue
            candidates.append(_candidate_from_field(field, notice, sites.get(notice.site_id)))

        if decision is None:
            decision = ProjectFieldDecision(project_id=project.id, field_key=field_key)
            db.add(decision)
        decision.candidates = candidates

        # 人工确认/修正：只刷新候选，绝不覆盖裁决值
        if decision.status == DecisionStatus.MANUAL.value:
            stats.manual_preserved += 1
            db.flush()
            continue

        valued = [item for item in candidates if item.get("value_text")]
        if not valued:
            decision.status = DecisionStatus.PENDING.value
            decision.basis = DecisionBasis.NONE.value
            decision.value_text = None
            decision.amount_yuan = None
            decision.event_at = None
            decision.unit = None
            decision.precision = "missing"
            decision.confidence = 0.0
            decision.source_notice_id = None
            decision.source_site_id = None
            decision.reason = "各有效来源均未提供该字段（占位话术或漏抽），待人工补全"
            stats.decisions_pending += 1
            db.flush()
            continue

        conflict = _has_conflict(field_key, candidates)
        winner, basis = _pick_winner(field_key, valued)
        decision.basis = basis
        decision.value_text = winner["value_text"]
        decision.unit = winner.get("unit")
        decision.amount_yuan = winner.get("amount_yuan")
        decision.event_at = _parse_event_at(winner.get("event_at"))
        decision.precision = winner.get("precision") or "missing"
        decision.is_estimated = bool(winner.get("is_estimated"))
        decision.confidence = winner["confidence"]
        decision.source_notice_id = winner["notice_id"]
        decision.source_site_id = winner["site_id"]
        decision.reviewer = None
        decision.reviewed_at = None
        if conflict:
            decision.status = DecisionStatus.CONFLICT.value
            distinct = _distinct_count(field_key, valued)
            decision.reason = (
                f"{len(valued)} 个来源给出 {distinct} 种不同取值，已暂存置信度最高者，待人工裁定"
            )
            stats.decisions_conflict += 1
        else:
            decision.status = DecisionStatus.AUTO.value
            reason = (
                "仅一个来源提供该值"
                if basis == DecisionBasis.SINGLE_SOURCE.value
                else f"多来源取值一致，取置信度最高来源（{winner['site_name']}，{winner['confidence']}）"
            )
            decision.reason = reason
            stats.decisions_auto += 1
        db.flush()

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


def _parse_event_at(value):
    from src.utils.timeutil import parse_local

    return parse_local(value)


def _distinct_count(field_key: str, valued: list[dict]) -> int:
    keys: set[str] = set()
    for item in valued:
        if field_key == "budget_amount":
            keys.add(str(item.get("amount_yuan") or item.get("value_text")))
        elif field_key == "deadline":
            keys.add(str(item.get("event_at") or item.get("value_text")))
        else:
            keys.add(item.get("value_text") or "")
    return len(keys)


# ---------------------------------------------------------------------------
# 项目状态联动（撤回）
# ---------------------------------------------------------------------------

def refresh_project_status(db: Session, project: Project, stats: MergeStats | None = None) -> None:
    """项目所有来源都撤回/失效 → 项目 withdrawn；否则 active。"""
    sources = db.scalars(
        select(ProjectSource).where(ProjectSource.project_id == project.id)
    ).all()
    active = [s for s in sources if s.status == SourceStatus.ACTIVE.value]
    now = now_local()
    if not active:
        if project.status != ProjectStatus.WITHDRAWN.value:
            project.status = ProjectStatus.WITHDRAWN.value
            project.withdrawn_at = now
            if stats is not None:
                stats.withdrawn += 1
    else:
        if project.status == ProjectStatus.WITHDRAWN.value:
            project.status = ProjectStatus.ACTIVE.value
            project.withdrawn_at = None
    # 主公告指针：取有效来源中发布时间最新者
    if active:
        notices = db.scalars(select(Notice).where(Notice.id.in_([s.notice_id for s in active]))).all()
        primary = sorted(notices, key=lambda n: (n.published_at, n.id), reverse=True)[0]
        project.primary_notice_id = primary.id
        project.qualification = primary.qualification
        project.region = primary.region
        project.category = primary.category
        project.industry = primary.industry
        project.display_name = primary.title.replace("招标公告", "").strip() or project.display_name
    project.last_seen_at = now
    db.flush()


def merge_notice(
    db: Session,
    notice: Notice,
    site: Site,
    merge_key: str,
    payload: dict,
    *,
    run_id: int,
    stats: MergeStats,
) -> Project:
    """采集入库后，把单条 notice 归并进项目（含来源 upsert + 裁决）。"""
    project, created = get_or_create_project(db, merge_key, payload)
    if created:
        stats.projects_created += 1
    _, outcome = upsert_project_source(db, project, notice, site, run_id=run_id)
    if outcome == "inserted":
        stats.sources_created += 1
    elif outcome == "updated":
        stats.sources_updated += 1
    refresh_project_status(db, project, stats)
    arbitrate_project(db, project, stats)
    return project
