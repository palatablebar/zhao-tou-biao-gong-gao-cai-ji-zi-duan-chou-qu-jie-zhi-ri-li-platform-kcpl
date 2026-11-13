"""采集批次：模拟抓取 6 个采源（含跨来源转发/更正/撤回剧情）→ 版本化入库
→ 跨来源归并裁决 → 全量重算抽取（保留人工确认）→ 同步日历风险。

幂等保证（重复执行不新增项目或日历事件）：
- notices 自然键 (site_id, external_id)；notice_versions (notice_id, version_no)；
- projects.merge_key、project_sources(project_id,site_id)、project_field_decisions(project_id,field_key)；
- deadline_events(notice_id,kind)；crawl 序号按成功批次递增，剧情随序号推进。
单条抓取失败只累计 error_count 不影响整批；后续批次重试即可补齐缺失副本。
"""
from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.constants.enums import (
    DecisionStatus,
    FieldStatus,
    NoticeStatus,
    ProjectStatus,
)
from src.models.crawl_run import CrawlRun
from src.models.extracted_field import ExtractedField
from src.models.notice import Notice
from src.models.project import Project
from src.models.project_field_decision import ProjectFieldDecision
from src.models.project_source import ProjectSource
from src.models.site import Site
from src.services import (
    deadline_service,
    extraction_engine,
    field_service,
    merge_service,
    source_catalog,
    template_service,
)
from src.utils.errors import AppError
from src.utils.timeutil import iso_local, now_local


def serialize_run(db: Session, run: CrawlRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "site_count": run.site_count,
        "fetched_count": run.fetched_count,
        "inserted_count": run.inserted_count,
        "duplicate_count": run.duplicate_count,
        "error_count": run.error_count,
        "field_count": run.field_count,
        "low_confidence_count": run.low_confidence_count,
        "merged_count": run.merged_count,
        "conflict_count": run.conflict_count,
        "withdrawn_count": run.withdrawn_count,
        "corrected_count": run.corrected_count,
        "crawl_ordinal": run.crawl_ordinal,
        "phase": run.phase,
        "duration_ms": run.duration_ms,
        "note": run.note,
        "started_at": iso_local(run.started_at),
        "finished_at": iso_local(run.finished_at),
    }


def list_runs(db: Session, limit: int = 20) -> list[dict]:
    runs = db.scalars(select(CrawlRun).order_by(CrawlRun.id.desc()).limit(limit)).all()
    return [serialize_run(db, run) for run in runs]


def _next_ordinal(db: Session) -> int:
    """采集序号 = 历史成功/部分成功批次数 + 1（失败批次不推进剧情，重试仍用同一序号）。"""
    done = db.scalar(
        select(func.max(CrawlRun.crawl_ordinal)).where(
            CrawlRun.crawl_ordinal > 0,
            CrawlRun.status.in_(["success", "partial"]),
        )
    )
    return (done or 0) + 1


def _ensure_notice(db: Session, site: Site, payload: dict, run_id: int) -> str:
    """按自然键 upsert 公告；正文变更时旧版本入版本链，不静默覆盖。"""
    notice = db.scalar(
        select(Notice).where(Notice.site_id == site.id, Notice.external_id == payload["external_id"])
    )
    merge_key = source_catalog.merge_key_for_payload(payload)
    if notice is None:
        notice = Notice(
            site_id=site.id,
            crawl_run_id=run_id,
            external_id=payload["external_id"],
            title=payload["title"],
            url=payload["url"],
            publisher=payload["publisher"],
            region=payload["region"],
            category=payload["category"],
            industry=payload["industry"],
            published_at=payload["published_at"],
            qualification=payload["qualification"],
            body_kind=payload["body_kind"],
            raw_html=payload["raw_html"],
            clean_text=payload["clean_text"],
            content_hash=payload["content_hash"],
            byte_size=payload["byte_size"],
            status=payload["status"],
            merge_key=merge_key,
            current_version_no=1,
            first_crawl_run_id=run_id,
        )
        db.add(notice)
        db.flush()
        merge_service.ensure_version(db, notice, run_id=run_id)
        return "inserted"
    # 已撤下的公告若来源重新发布（新 hash），恢复为有效并起新版本
    if notice.content_hash != payload["content_hash"]:
        previous_status = notice.status
        notice.raw_html = payload["raw_html"]
        notice.clean_text = payload["clean_text"]
        notice.content_hash = payload["content_hash"]
        notice.byte_size = payload["byte_size"]
        notice.crawl_run_id = run_id
        notice.merge_key = merge_key
        if previous_status == NoticeStatus.WITHDRAWN.value:
            notice.status = payload["status"]
            notice.withdrawn_at = None
        merge_service.ensure_version(
            db, notice, run_id=run_id, change_note=payload.get("note")
        )
        db.flush()
        return "corrected" if previous_status != NoticeStatus.WITHDRAWN.value else "reactivated"
    return "duplicate"


def _fetch_site_payloads(site: Site, ordinal: int) -> tuple[list[dict], int]:
    """组装某站点本批次应抓到的目录：基线公告 + 已到批次的跨来源副本。

    返回 (payloads, transient_errors)；瞬时失败的副本计入 transient_errors 并跳过。
    """
    payloads = source_catalog.notices_for_site(site.domain)
    by_external = {p["external_id"]: p for p in payloads}
    failing = source_catalog.failing_mirror_keys(ordinal)
    transient_errors = 0
    for spec in source_catalog.MIRROR_PLAN:
        target = next((s for s in source_catalog.SITE_SPECS if s.key == spec.target_site_key), None)
        if target is None or target.domain != site.domain:
            continue
        if spec.appear_phase > ordinal:
            continue
        if spec.key in failing:
            # 模拟瞬时抓取失败：本批不返回该副本（计入失败），下批恢复后补齐
            transient_errors += 1
            continue
        mirror = source_catalog.build_mirror_notice(spec)
        # 更正公告与基线同登记号：以更正正文覆盖，确保走版本链而非新登记
        by_external[mirror["external_id"]] = mirror
    return list(by_external.values()), transient_errors


def _apply_withdrawals(db: Session, ordinal: int, run_id: int, stats: merge_service.MergeStats) -> int:
    """处理主站撤下（项目级，级联全部来源）与转发副本撤下，联动项目/日历。"""
    withdrawn = 0
    # 1) 项目整体撤下：主站基线公告被撤 → 级联该项目全部来源
    for index in source_catalog.project_withdrawn_indexes(ordinal):
        base_payload = source_catalog.build_notice(index)
        merge_key = source_catalog.merge_key_for(index)
        project = db.scalar(select(Project).where(Project.merge_key == merge_key))
        if project is None or project.status == ProjectStatus.WITHDRAWN.value:
            continue
        sources = db.scalars(
            select(ProjectSource).where(ProjectSource.project_id == project.id)
        ).all()
        notice_ids = {s.notice_id for s in sources}
        # 兜底：即使来源未登记，也按自然键找到基线公告
        base_site = next(
            (s for s in source_catalog.SITE_SPECS if s.key == base_payload["site_key"]), None
        )
        if base_site is not None:
            site = db.scalar(select(Site).where(Site.domain == base_site.domain))
            if site is not None:
                base_notice = db.scalar(
                    select(Notice).where(
                        Notice.site_id == site.id,
                        Notice.external_id == base_payload["external_id"],
                    )
                )
                if base_notice is not None:
                    notice_ids.add(base_notice.id)
        for notice_id in notice_ids:
            notice = db.get(Notice, notice_id)
            if notice is not None and notice.status != NoticeStatus.WITHDRAWN.value:
                merge_service.mark_notice_withdrawn(
                    db, notice, run_id=run_id, reason=f"项目整体撤下（主站撤下 index={index}）"
                )
                withdrawn += 1
        for source in sources:
            merge_service.mark_source_withdrawn(db, source, reason=f"项目级撤回 index={index}")
        merge_service.refresh_project_status(db, project, stats)
        merge_service.arbitrate_project(db, project, stats)

    # 2) 单个转发副本撤下（项目本身可能仍有其他有效来源）
    for spec in source_catalog.MIRROR_PLAN:
        if spec.withdrawn_phase is None or spec.withdrawn_phase > ordinal:
            continue
        target = next(
            (s for s in source_catalog.SITE_SPECS if s.key == spec.target_site_key), None
        )
        if target is None:
            continue
        mirror = source_catalog.build_mirror_notice(spec)
        site = db.scalar(select(Site).where(Site.domain == target.domain))
        if site is None:
            continue
        notice = db.scalar(
            select(Notice).where(
                Notice.site_id == site.id, Notice.external_id == mirror["external_id"]
            )
        )
        if notice is None or notice.status == NoticeStatus.WITHDRAWN.value:
            continue
        merge_service.mark_notice_withdrawn(
            db, notice, run_id=run_id, reason=f"来源撤下转发副本（{spec.note}）"
        )
        withdrawn += 1
        _withdraw_notice_sources(db, notice, stats)
    return withdrawn


def _withdraw_notice_sources(db: Session, notice: Notice, stats: merge_service.MergeStats) -> None:
    sources = db.scalars(
        select(ProjectSource).where(ProjectSource.notice_id == notice.id)
    ).all()
    project_ids = {s.project_id for s in sources}
    for source in sources:
        merge_service.mark_source_withdrawn(db, source, reason=notice.external_id)
    for project_id in project_ids:
        project = db.get(Project, project_id)
        if project is not None:
            merge_service.refresh_project_status(db, project, stats)
            merge_service.arbitrate_project(db, project, stats)


def extract_all(db: Session, site_ids: list[int] | None = None) -> dict:
    stmt = select(Notice)
    if site_ids:
        stmt = stmt.where(Notice.site_id.in_(site_ids))
    notices = db.scalars(stmt.order_by(Notice.id)).all()
    created = updated = preserved = total = low = 0
    for notice in notices:
        anchors = template_service.anchors_for_site(db, notice.site_id)
        results = extraction_engine.extract_notice(notice.clean_text, notice.published_at, anchors)
        existing = {
            field.field_key: field
            for field in db.scalars(
                select(ExtractedField).where(ExtractedField.notice_id == notice.id)
            ).all()
        }
        for field_key, result in results.items():
            total += 1
            field = existing.get(field_key)
            if field is None:
                field = ExtractedField(notice_id=notice.id, field_key=field_key)
                db.add(field)
                field_service.apply_result(field, result)
                created += 1
            elif field.status in field_service.PRESERVED_STATUSES:
                preserved += 1
            else:
                field_service.apply_result(field, result)
                updated += 1
            if (
                field.confidence < settings.low_confidence_threshold
                and field.status == FieldStatus.EXTRACTED.value
            ):
                low += 1
    db.flush()
    return {
        "field_count": total,
        "created": created,
        "updated": updated,
        "preserved_manual": preserved,
        "low_confidence_count": low,
        "notice_count": len(notices),
    }


def _merge_all(db: Session, run_id: int) -> merge_service.MergeStats:
    """对全库有效/撤回公告重跑归并（幂等），保证重试可补齐、撤回可联动。"""
    stats = merge_service.MergeStats()
    notices = db.scalars(select(Notice).order_by(Notice.id)).all()
    sites = {s.id: s for s in db.scalars(select(Site)).all()}
    for notice in notices:
        site = sites.get(notice.site_id)
        if site is None:
            continue
        merge_key = merge_service.merge_key_for_notice(notice)
        payload = {
            "title": notice.title,
            "external_id": notice.external_id,
            "publisher": notice.publisher,
            "region": notice.region,
            "category": notice.category,
            "industry": notice.industry,
            "qualification": notice.qualification,
        }
        if notice.status == NoticeStatus.WITHDRAWN.value:
            _withdraw_notice_sources(db, notice, stats)
            continue
        merge_service.merge_notice(
            db, notice, site, merge_key, payload, run_id=run_id, stats=stats
        )
    return stats


def run_crawl(
    db: Session,
    actor_email: str,
    site_ids: list[int] | None = None,
    as_of: datetime | None = None,
) -> dict:
    running = db.scalar(select(CrawlRun).where(CrawlRun.status == "running"))
    if running is not None:
        raise AppError("CRAWL_BUSY", 409, detail=f"run_id={running.id}")
    started = time.perf_counter()
    ordinal = _next_ordinal(db)
    run = CrawlRun(status="running", triggered_by=actor_email, crawl_ordinal=ordinal, phase=f"phase-{ordinal}")
    db.add(run)
    db.flush()
    fetched = inserted = duplicated = corrected = errors = 0
    try:
        stmt = select(Site).order_by(Site.priority)
        if site_ids:
            stmt = stmt.where(Site.id.in_(site_ids))
        sites = db.scalars(stmt).all()
        for site in sites:
            try:
                payloads, transient_errors = _fetch_site_payloads(site, ordinal)
                errors += transient_errors
            except Exception:  # noqa: BLE001 - 整站失败不阻断其他站点
                errors += 1
                continue
            for payload in payloads:
                fetched += 1
                try:
                    outcome = _ensure_notice(db, site, payload, run.id)
                except Exception:  # noqa: BLE001 - 单条失败不影响整批
                    errors += 1
                    continue
                if outcome == "inserted":
                    inserted += 1
                elif outcome in ("corrected", "reactivated"):
                    corrected += 1
                else:
                    duplicated += 1
        # 撤回联动（主站撤下 / 转发源撤下）
        withdrawal_stats = merge_service.MergeStats()
        withdrawn_notice_count = _apply_withdrawals(db, ordinal, run.id, withdrawal_stats)
        # 全量抽取（人工复核字段保留）
        extraction = extract_all(db, [site.id for site in sites])
        # 跨来源归并 + 字段裁决
        merge_stats = _merge_all(db, run.id)
        # 日历：建/更新事件，撤回项目移出必投名单
        deadline_service.ensure_tracked_events(db)
        deadline_service.sync_project_events(db)
        risk = deadline_service.recompute_events(db, as_of=as_of)

        run.site_count = len(sites)
        run.fetched_count = fetched
        run.inserted_count = inserted
        run.duplicate_count = duplicated
        run.corrected_count = corrected
        run.error_count = errors
        run.field_count = extraction["field_count"]
        run.low_confidence_count = extraction["low_confidence_count"]
        run.merged_count = db.scalar(select(func.count()).select_from(Project)) or 0
        run.conflict_count = db.scalar(
            select(func.count())
            .select_from(ProjectFieldDecision)
            .where(ProjectFieldDecision.status == DecisionStatus.CONFLICT.value)
        ) or 0
        run.withdrawn_count = db.scalar(
            select(func.count()).select_from(Project).where(
                Project.status == ProjectStatus.WITHDRAWN.value
            )
        ) or 0
        run.status = "success"
        run.note = (
            f"第 {ordinal} 批：抓取 {fetched}（新登 {inserted}/正文更正 {corrected}/重复 {duplicated}/失败 {errors}）；"
            f"本批撤下来源公告 {withdrawn_notice_count}，归并项目 {run.merged_count}，"
            f"冲突待裁 {run.conflict_count}，在库撤回项目 {run.withdrawn_count}；"
            f"抽取 {extraction['field_count']} 字段，低置信 {extraction['low_confidence_count']}"
        )[:255]
        run.finished_at = now_local()
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        db.flush()
        return {
            "run": serialize_run(db, run),
            "extraction": extraction,
            "merge": _stats_dict(merge_stats, withdrawal_stats),
            "risk": risk,
        }
    except Exception as exc:
        run.status = "failed"
        run.note = f"采集失败：{exc}"[:255]
        run.finished_at = now_local()
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        db.flush()
        raise


def _stats_dict(*stats_objs: merge_service.MergeStats) -> dict:
    merged = merge_service.MergeStats()
    for stats in stats_objs:
        for name in stats.__dataclass_fields__:
            setattr(merged, name, getattr(merged, name) + getattr(stats, name))
    return {
        "projects_created": merged.projects_created,
        "sources_created": merged.sources_created,
        "sources_updated": merged.sources_updated,
        "decisions_auto": merged.decisions_auto,
        "decisions_conflict": merged.decisions_conflict,
        "decisions_pending": merged.decisions_pending,
        "manual_preserved": merged.manual_preserved,
        "withdrawn": merged.withdrawn,
        "corrected": merged.corrected,
    }


def summary(db: Session) -> dict:
    notices = db.scalar(select(func.count()).select_from(Notice)) or 0
    fields = db.scalar(select(func.count()).select_from(ExtractedField)) or 0
    runs = db.scalar(select(func.count()).select_from(CrawlRun)) or 0
    projects = db.scalar(select(func.count()).select_from(Project)) or 0
    conflicts = db.scalar(
        select(func.count())
        .select_from(ProjectFieldDecision)
        .where(ProjectFieldDecision.status == DecisionStatus.CONFLICT.value)
    ) or 0
    latest = db.scalar(select(CrawlRun).order_by(CrawlRun.id.desc()))
    return {
        "notice_count": notices,
        "field_count": fields,
        "run_count": runs,
        "project_count": projects,
        "conflict_count": conflicts,
        "latest_run": serialize_run(db, latest) if latest else None,
    }
