"""本地端到端验证（SQLite，无需 MySQL/Redis）：

覆盖用户诉求：
1. 跨来源归并：同一项目多来源 → 一个 Project + 多个 ProjectSource；
2. 字段裁决依据 + 人工确认保留（重采不覆盖 manual）；
3. 更正后旧版本不覆盖（版本链 superseded/active）；
4. 撤回项目从必投名单移除（事件 is_active=False）；
5. 重复采集幂等（项目数/日历事件数不增长）；
6. 失败后重试补齐（第一批缺 G-retry，第二批出现）；
7. 来源冲突与待裁定字段可见（conflict/pending）。

用法：python -I scripts_local/smoke_e2e.py
"""
from __future__ import annotations

import os
import sys
import tempfile

# 用 SQLite 覆盖数据库地址（必须在导入 src.config 前设置）
_tmp = tempfile.mkdtemp(prefix="bidsift_smoke_")
_BACKEND = "/workspace/backend"
sys.path.insert(0, _BACKEND)
os.environ["DB_HOST"] = "localhost"
os.environ["SEED_DEMO"] = "false"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"

# 让 settings.database_url 直接返回 SQLite 地址，绕开 MySQL/pymysql 驱动
_db_path = os.path.join(_tmp, "smoke.db")
_sqlite_url = f"sqlite:///{_db_path}"

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.config.settings import settings  # noqa: E402

type(settings).database_url = property(lambda self: _sqlite_url)  # type: ignore[attr-defined]

import src.config.database as database  # noqa: E402
from src.models import Base  # noqa: E402

_engine = create_engine(_sqlite_url, future=True)
_Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
database.engine = _engine
database.SessionLocal = _Session

# 让 session_scope / get_db 都使用测试 engine
import src.services.crawl_service as crawl_service  # noqa: E402
from src.constants.enums import (  # noqa: E402
    DecisionStatus,
    NoticeStatus,
    ProjectStatus,
    SourceStatus,
    VersionStatus,
)
from src.models.crawl_run import CrawlRun  # noqa: E402
from src.models.deadline_event import DeadlineEvent  # noqa: E402
from src.models.notice import Notice  # noqa: E402
from src.models.notice_version import NoticeVersion  # noqa: E402
from src.models.project import Project  # noqa: E402
from src.models.project_field_decision import ProjectFieldDecision  # noqa: E402
from src.models.project_source import ProjectSource  # noqa: E402
from src.services import deadline_service, project_service, source_catalog  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def main() -> int:
    Base.metadata.create_all(bind=_engine)
    db = _Session()

    # ---- 基线 seed（手动执行 seed 的实体步骤，避免 session_scope 重绑定）----
    from scripts.seed_demo import (
        ensure_baseline_run,
        ensure_notices,
        ensure_sites,
        ensure_templates,
        ensure_users,
    )

    users = ensure_users(db)
    sites = ensure_sites(db)
    ensure_templates(db, sites)
    run0 = ensure_baseline_run(db)
    created = ensure_notices(db, sites, run0)
    db.commit()
    print(f"[seed] notices inserted={created}")

    extraction = crawl_service.extract_all(db)
    db.commit()
    crawl_service._merge_all(db, run0.id)
    deadline_service.ensure_tracked_events(db)
    deadline_service.sync_project_events(db)
    deadline_service.recompute_events(db)
    db.commit()

    base_notices = db.scalar(select(func.count()).select_from(Notice))
    base_projects = db.scalar(select(func.count()).select_from(Project))
    base_events = db.scalar(select(func.count()).select_from(DeadlineEvent))
    base_versions = db.scalar(select(func.count()).select_from(NoticeVersion))
    print(f"[seed] notices={base_notices} projects={base_projects} events={base_events} versions={base_versions}")
    check("基线 120 条公告", base_notices == 120, f"got {base_notices}")
    check("基线 120 个项目", base_projects == 120, f"got {base_projects}")
    check("基线 18 个日历事件", base_events == 18, f"got {base_events}")
    check("基线 120 个 active 版本", base_versions == 120, f"got {base_versions}")
    check("基线字段 480", extraction["field_count"] == 480, f"got {extraction['field_count']}")

    # 预取剧情 base 索引对应的项目
    def project_for_index(index: int) -> Project:
        payload = source_catalog.build_notice(index)
        notice = db.scalar(select(Notice).where(Notice.external_id == payload["external_id"]))
        source = db.scalar(select(ProjectSource).where(ProjectSource.notice_id == notice.id))
        return db.get(Project, source.project_id)

    proj_a = project_for_index(2)
    proj_b = project_for_index(6)
    proj_c = project_for_index(10)
    proj_d = project_for_index(14)
    proj_e = project_for_index(18)
    proj_f = project_for_index(22)
    proj_g = project_for_index(26)
    proj_h = project_for_index(30)

    # ---- 第 1 批采集 ----
    print("\n[crawl 1] 首批（含转发/冲突副本；G-retry 模拟失败）")
    r1 = crawl_service.run_crawl(db, actor_email="worker@bidsift.io")
    db.commit()
    run1 = r1["run"]
    check("第 1 批 ordinal=1", run1["crawl_ordinal"] == 1, str(run1["crawl_ordinal"]))
    check("第 1 批有失败计数（G-retry 瞬时失败）", run1["error_count"] >= 1, str(run1["error_count"]))

    # 组 A：index 2 现在应至少有 2 个来源（基线 + gdggzy 转发）
    sources_a = db.scalars(select(ProjectSource).where(ProjectSource.project_id == proj_a.id)).all()
    check("组A 跨来源归并到同一项目", len(sources_a) == 2, f"got {len(sources_a)}")
    check("组A 项目数未增长", db.scalar(select(func.count()).select_from(Project)) == 120)

    # 组 D：预算冲突
    dec_d_budget = db.scalar(select(ProjectFieldDecision).where(
        ProjectFieldDecision.project_id == proj_d.id,
        ProjectFieldDecision.field_key == "budget_amount",
    ))
    check("组D 预算字段标记冲突", dec_d_budget.status == DecisionStatus.CONFLICT.value, dec_d_budget.status)
    check("组D 冲突有≥2候选", len(dec_d_budget.candidates) >= 2, str(len(dec_d_budget.candidates)))

    # 组 G：第一批还没有 sdggzy 副本（抓取失败）
    sources_g = db.scalars(select(ProjectSource).where(ProjectSource.project_id == proj_g.id)).all()
    check("组G 首批失败未建来源", all(s.site_id is not None for s in sources_g) and len(sources_g) == 1,
          f"got {len(sources_g)}")

    # ---- 幂等：立刻再跑一次同 ordinal 内容（ordinal 仍递增，但目录相同）----
    print("\n[crawl 2a] 重复执行（第二批目录，验证不新增项目/事件）")
    projects_before = db.scalar(select(func.count()).select_from(Project))
    events_before = db.scalar(select(func.count()).select_from(DeadlineEvent))
    r2 = crawl_service.run_crawl(db, actor_email="worker@bidsift.io")
    db.commit()
    projects_after = db.scalar(select(func.count()).select_from(Project))
    events_after = db.scalar(select(func.count()).select_from(DeadlineEvent))
    check("重复采集不新增项目", projects_after == projects_before, f"{projects_before}->{projects_after}")
    check("重复采集不新增日历事件", events_after == events_before, f"{events_before}->{events_after}")

    # 组 G：第二批重试补齐
    sources_g2 = db.scalars(select(ProjectSource).where(ProjectSource.project_id == proj_g.id)).all()
    check("组G 失败重试补齐来源", len(sources_g2) == 2, f"got {len(sources_g2)}")

    # 组 A：第二批江苏转发到达 → 3 来源
    sources_a2 = db.scalars(select(ProjectSource).where(ProjectSource.project_id == proj_a.id)).all()
    check("组A 第二批达 3 来源", len(sources_a2) == 3, f"got {len(sources_a2)}")

    # 组 B：主站更正预算 → 版本链出现 superseded + active
    notice_b = db.get(Notice, proj_b.primary_notice_id)
    versions_b = db.scalars(
        select(NoticeVersion).where(NoticeVersion.notice_id == notice_b.id).order_by(NoticeVersion.version_no)
    ).all()
    check("组B 更正产生新版本链", len(versions_b) >= 2, f"got {len(versions_b)}")
    check("组B 旧版本 superseded", any(v.status == VersionStatus.SUPERSEDED.value for v in versions_b[:-1]))
    check("组B 当前版本 active", versions_b[-1].status == VersionStatus.ACTIVE.value)

    # 组 E：截止时间冲突
    dec_e_deadline = db.scalar(select(ProjectFieldDecision).where(
        ProjectFieldDecision.project_id == proj_e.id,
        ProjectFieldDecision.field_key == "deadline",
    ))
    check("组E 截止时间冲突", dec_e_deadline.status == DecisionStatus.CONFLICT.value, dec_e_deadline.status)

    # 组 F：广东来源第二批撤回，项目仍 active（江苏仍有效）
    f_sources = db.scalars(select(ProjectSource).where(ProjectSource.project_id == proj_f.id)).all()
    withdrawn_f = [s for s in f_sources if s.status == SourceStatus.WITHDRAWN.value]
    check("组F 单来源撤回标记", len(withdrawn_f) == 1, f"got {len(withdrawn_f)}")
    check("组F 项目仍有效", db.get(Project, proj_f.id).status == ProjectStatus.ACTIVE.value)

    # ---- 人工确认组 D 预算，然后再采集，验证不被覆盖 ----
    print("\n[manual] 人工裁定组D预算，再跑采集验证保留")
    chosen = dec_d_budget.candidates[0]
    adj = project_service.adjudicate(
        db, proj_d.id, "budget_amount",
        {"field_key": "budget_amount", "source_notice_id": chosen["notice_id"]},
        "analyst@bidsift.io",
    )
    db.commit()
    manual_value = adj["decision"]["value_text"]
    dec_d_after = db.scalar(select(ProjectFieldDecision).where(
        ProjectFieldDecision.project_id == proj_d.id,
        ProjectFieldDecision.field_key == "budget_amount",
    ))
    check("人工裁定状态=manual", dec_d_after.status == DecisionStatus.MANUAL.value)

    # ---- 第 3 批：组 C 项目整体撤回（主站撤 index 10）----
    print("\n[crawl 3] 第三批（组C 主站撤下）")
    r3 = crawl_service.run_crawl(db, actor_email="worker@bidsift.io")
    db.commit()
    check("第3批 ordinal=3", r3["run"]["crawl_ordinal"] == 3)
    proj_c_refreshed = db.get(Project, proj_c.id)
    check("组C 项目标记撤回", proj_c_refreshed.status == ProjectStatus.WITHDRAWN.value,
          proj_c_refreshed.status)
    c_events = db.scalars(select(DeadlineEvent).where(DeadlineEvent.project_id == proj_c.id)).all()
    check("组C 日历事件停用", c_events and all(not e.is_active for e in c_events),
          str([(e.id, e.is_active) for e in c_events]))
    must_bid = deadline_service.week_must_bid_rows(db)
    check("组C 移出本周必投", all(row["notice_id"] not in {e.notice_id for e in c_events} for row in must_bid))

    # 人工裁定在再采集后保留
    dec_d_post = db.scalar(select(ProjectFieldDecision).where(
        ProjectFieldDecision.project_id == proj_d.id,
        ProjectFieldDecision.field_key == "budget_amount",
    ))
    check("人工裁定采集后不被覆盖", dec_d_post.status == DecisionStatus.MANUAL.value
          and dec_d_post.value_text == manual_value,
          f"{dec_d_post.status}/{dec_d_post.value_text}")

    # ---- 第 4 批：组 H 先更正后撤（index 30 整体撤回），组 C 转发源也撤 ----
    print("\n[crawl 4] 第四批（组H 撤回）")
    r4 = crawl_service.run_crawl(db, actor_email="worker@bidsift.io")
    db.commit()
    proj_h_refreshed = db.get(Project, proj_h.id)
    check("组H 更正后整体撤回", proj_h_refreshed.status == ProjectStatus.WITHDRAWN.value)
    h_versions = db.scalars(
        select(NoticeVersion).join(Notice, Notice.id == NoticeVersion.notice_id)
        .join(ProjectSource, ProjectSource.notice_id == Notice.id)
        .where(ProjectSource.project_id == proj_h.id)
    ).all()
    check("组H 历史版本仍保留", len(h_versions) >= 2, f"got {len(h_versions)}")

    # ---- 待裁定字段（pending）可见性：找一个全来源缺失的字段 ----
    pending = db.scalars(select(ProjectFieldDecision).where(
        ProjectFieldDecision.status == DecisionStatus.PENDING.value
    )).all()
    print(f"[pending] 待补全裁决字段数={len(pending)}")

    # ---- project_service 列表/详情/统计 ----
    listing = project_service.list_projects(db, conflict="conflict")
    check("冲突筛选返回项目", listing["total"] >= 1, str(listing["total"]))
    detail = project_service.get_project(db, proj_a.id)
    check("项目详情含来源与裁决", len(detail["sources"]) >= 3 and len(detail["decisions"]) == 4)
    stats = project_service.project_stats(db)
    check("项目统计含撤回数", stats["withdrawn_total"] >= 2, str(stats["withdrawn_total"]))
    check("项目统计含人工裁定数", stats["manual_field_total"] >= 1, str(stats["manual_field_total"]))

    # ---- 撤销人工裁定恢复自动 ----
    reset = project_service.reset_decision(db, proj_d.id, "budget_amount", "admin@bidsift.io")
    db.commit()
    check("撤销人工裁定后回到冲突/自动", reset["decision"]["status"] in
          (DecisionStatus.CONFLICT.value, DecisionStatus.AUTO.value), reset["decision"]["status"])

    # ---- 日历列表默认不返回停用事件 ----
    active_events = deadline_service.list_events(db)
    all_events = deadline_service.list_events(db, include_inactive=True)
    check("日历默认隐藏停用事件", len(active_events) < len(all_events) == 18,
          f"active={len(active_events)} all={len(all_events)}")

    # ---- 最终幂等总检：项目数不超过 120 ----
    final_projects = db.scalar(select(func.count()).select_from(Project))
    final_events = db.scalar(select(func.count()).select_from(DeadlineEvent))
    check("最终项目数仍为 120（全为归并）", final_projects == 120, str(final_projects))
    check("最终日历事件仍为 18", final_events == 18, str(final_events))

    db.close()
    print(f"\n==== 结果：通过 {PASS} / 失败 {FAIL} ====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
