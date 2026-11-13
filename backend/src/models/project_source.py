from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import now_local


class ProjectSource(Base):
    """项目在单个采源上的登记记录（项目 × 来源 唯一）。

    保存该来源当前有效公告快照指针；同一来源的历史正文在 notice_versions 留痕。
    状态：active（有效）/ superseded（被本来源更正替代）/ withdrawn（本来源撤下）。
    """

    __tablename__ = "project_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "site_id", name="uq_project_source_site"),
        UniqueConstraint("notice_id", name="uq_project_source_notice"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    current_version_no: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    first_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_runs.id"), nullable=True)
    last_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_runs.id"), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
