"""HTTP 端到端：TestClient + SQLite，验证项目归并/裁决/撤回的真实 API 行为。

用法：python -I scripts_local/smoke_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="bidsift_api_")
sys.path.insert(0, "/workspace/backend")
os.environ["SEED_DEMO"] = "false"
_db = os.path.join(_tmp, "api.db")
_sqlite = f"sqlite:///{_db}"

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.config.settings import settings  # noqa: E402

type(settings).database_url = property(lambda self: _sqlite)  # type: ignore[attr-defined]

import src.config.database as database  # noqa: E402
from src.models import Base  # noqa: E402

_engine = create_engine(_sqlite, future=True, connect_args={"check_same_thread": False})
_Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
database.engine = _engine
database.SessionLocal = _Session
Base.metadata.create_all(bind=_engine)

from fastapi.testclient import TestClient  # noqa: E402

from src.config.app import create_app  # noqa: E402
from scripts.seed_demo import (  # noqa: E402
    DEMO_PASSWORD,
    ensure_baseline_run,
    ensure_notices,
    ensure_sites,
    ensure_templates,
    ensure_users,
)
from src.services import crawl_service, deadline_service  # noqa: E402

# 直接种入基线（绕过 lifespan）
from src.config.database import session_scope  # noqa: E402

with session_scope() as db:
    users = ensure_users(db)
    sites = ensure_sites(db)
    ensure_templates(db, sites)
    run0 = ensure_baseline_run(db)
    ensure_notices(db, sites, run0)
    crawl_service.extract_all(db)
    crawl_service._merge_all(db, run0.id)  # noqa: SLF001
    deadline_service.ensure_tracked_events(db)
    deadline_service.sync_project_events(db)
    deadline_service.recompute_events(db)

app = create_app()
# lifespan 会尝试 wait_for_db（MySQL），测试中跳过
client = TestClient(app)
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


def login(role_email: str) -> dict:
    resp = client.post("/api/auth/login", json={"email": role_email, "password": DEMO_PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    analyst = login("analyst@bidsift.io")
    client_h = login("client@bidsift.io")

    # 项目列表（基线 120）
    resp = client.get("/api/projects?page_size=100", headers=analyst)
    check("项目列表 200", resp.status_code == 200, resp.text)
    check("基线项目 120", resp.json()["total"] == 120, str(resp.json()["total"]))

    # 项目统计
    stats = client.get("/api/projects/stats", headers=analyst).json()
    check("项目统计 project_total=120", stats["project_total"] == 120, str(stats))

    # client 只读：可看项目，不能裁定
    check("client 可读项目", client.get("/api/projects", headers=client_h).status_code == 200)
    forbidden = client.post(
        "/api/projects/1/decisions",
        headers=client_h,
        json={"field_key": "project_name", "value": "x"},
    )
    check("client 裁定被 403", forbidden.status_code == 403, str(forbidden.status_code))

    # 跑 4 批采集推进剧情
    for ordinal in range(1, 5):
        r = client.post("/api/crawls", headers=analyst, json={})
        check(f"第{ordinal}批采集成功", r.status_code == 200, r.text)
        check(f"第{ordinal}批序号正确", r.json()["run"]["crawl_ordinal"] == ordinal)

    # 冲突项目筛选
    conflicts = client.get("/api/projects?conflict=conflict&page_size=100", headers=analyst).json()
    check("存在冲突项目", conflicts["total"] >= 1, str(conflicts["total"]))

    # 找一个 conflict 字段做人工裁定
    target = conflicts["items"][0]
    detail = client.get(f"/api/projects/{target['id']}", headers=analyst).json()
    conflict_field = next(d for d in detail["decisions"] if d["status"] == "conflict")
    chosen = conflict_field["candidates"][0]
    adj = client.post(
        f"/api/projects/{target['id']}/decisions",
        headers=analyst,
        json={"field_key": conflict_field["field_key"], "source_notice_id": chosen["notice_id"]},
    )
    check("人工裁定 200", adj.status_code == 200, adj.text)
    check("裁定后状态=manual", adj.json()["decision"]["status"] == "manual", adj.text)

    # 再跑一批，manual 不被覆盖
    client.post("/api/crawls", headers=analyst, json={})
    detail2 = client.get(f"/api/projects/{target['id']}", headers=analyst).json()
    field2 = next(d for d in detail2["decisions"] if d["field_key"] == conflict_field["field_key"])
    check("再采集人工裁定保留", field2["status"] == "manual"
          and field2["value_text"] == chosen["value_text"], f"{field2['status']}")

    # 撤销人工裁定
    reset = client.delete(
        f"/api/projects/{target['id']}/decisions/{conflict_field['field_key']}", headers=analyst
    )
    check("撤销裁定 200", reset.status_code == 200, reset.text)
    check("撤销后非 manual", reset.json()["decision"]["status"] != "manual")

    # 撤回项目：日历默认不含停用事件
    cal = client.get("/api/deadlines", headers=analyst).json()
    check("日历事件均 active", all(e["is_active"] for e in cal["items"]))
    cal_all = client.get("/api/deadlines?risk=skip", headers=analyst).json()
    # 撤回项目详情里事件 is_active=False
    withdrawn_list = client.get("/api/projects?status=withdrawn&page_size=100", headers=analyst).json()
    check("存在撤回项目", withdrawn_list["total"] >= 1, str(withdrawn_list["total"]))
    if withdrawn_list["items"]:
        wd = client.get(f"/api/projects/{withdrawn_list['items'][0]['id']}", headers=analyst).json()
        check("撤回项目事件停用", all((not e["is_active"]) for e in wd["deadlines"]) or len(wd["deadlines"]) == 0)

    # 必投名单不含撤回
    must_bid = deadline_service_week(analyst)
    withdrawn_notice_ids = {
        e["notice_id"]
        for e in client.get("/api/deadlines", headers=analyst).json()["items"]
        if not e["is_active"]
    }
    # 默认日历已过滤 inactive，这里验证必投名单 notice 都处于 active
    active_notice_ids = {e["notice_id"] for e in cal["items"]}
    check("必投名单均为有效事件", all(row["notice_id"] in active_notice_ids for row in must_bid))

    # 重复采集不新增项目/事件
    before = (client.get("/api/projects/stats", headers=analyst).json()["project_total"],
              len(cal_all["items"]) + len(cal["items"]))
    client.post("/api/crawls", headers=analyst, json={})
    after_stats = client.get("/api/projects/stats", headers=analyst).json()
    check("再采集项目数不增", after_stats["project_total"] == before[0],
          f"{before[0]}->{after_stats['project_total']}")

    print(f"\n==== API 结果：通过 {PASS} / 失败 {FAIL} ====")
    return 1 if FAIL else 0


def deadline_service_week(headers: dict) -> list:
    from src.services import deadline_service

    with session_scope() as db:
        return deadline_service.week_must_bid_rows(db)


if __name__ == "__main__":
    sys.exit(main())
