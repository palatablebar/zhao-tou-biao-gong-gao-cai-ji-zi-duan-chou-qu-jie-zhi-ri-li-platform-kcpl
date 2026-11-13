"""验证 seed 幂等：连续执行两次，所有表行数不变。"""
from __future__ import annotations

import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="bidsift_idem_")
sys.path.insert(0, "/workspace/backend")
os.environ["SEED_DEMO"] = "false"
_db = os.path.join(_tmp, "idem.db")
_sqlite = f"sqlite:///{_db}"

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.config.settings import settings  # noqa: E402

type(settings).database_url = property(lambda self: _sqlite)  # type: ignore[attr-defined]

import src.config.database as database  # noqa: E402
from src.models import Base  # noqa: E402

_engine = create_engine(_sqlite, future=True)
_Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
database.engine = _engine
database.SessionLocal = _Session
Base.metadata.create_all(bind=_engine)

from scripts.seed_demo import seed_all  # noqa: E402
from src.models.deadline_event import DeadlineEvent  # noqa: E402
from src.models.notice import Notice  # noqa: E402
from src.models.notice_version import NoticeVersion  # noqa: E402
from src.models.project import Project  # noqa: E402
from src.models.project_field_decision import ProjectFieldDecision  # noqa: E402
from src.models.project_source import ProjectSource  # noqa: E402


def counts() -> dict:
    with _Session() as db:
        return {
            "notices": db.scalar(select(func.count()).select_from(Notice)),
            "projects": db.scalar(select(func.count()).select_from(Project)),
            "events": db.scalar(select(func.count()).select_from(DeadlineEvent)),
            "versions": db.scalar(select(func.count()).select_from(NoticeVersion)),
            "sources": db.scalar(select(func.count()).select_from(ProjectSource)),
            "decisions": db.scalar(select(func.count()).select_from(ProjectFieldDecision)),
        }


print(seed_all())
first = counts()
print("first:", first)
print(seed_all())
second = counts()
print("second:", second)
ok = first == second
print("IDEMPOTENT:", ok)
sys.exit(0 if ok else 1)
