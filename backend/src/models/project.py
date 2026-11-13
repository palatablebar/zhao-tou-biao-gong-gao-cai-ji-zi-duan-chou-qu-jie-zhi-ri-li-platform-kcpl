from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import now_local


class Project(Base):
    """跨来源归并后的项目（同一招标项目在多个交易中心转发只对应一条 Project）。

    merge_key 为归并自然键（确定性生成：归一化项目名称 + 招标人），
    重复采集命中同一 merge_key 不新增项目。
    """

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("merge_key", name="uq_project_merge_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    merge_key: Mapped[str] = mapped_column(String(160), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    publisher: Mapped[str] = mapped_column(String(160), default="")
    region: Mapped[str] = mapped_column(String(20), default="", index=True)
    category: Mapped[str] = mapped_column(String(30), default="", index=True)
    industry: Mapped[str] = mapped_column(String(20), default="", index=True)
    qualification: Mapped[str] = mapped_column(String(20), default="none")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    primary_notice_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_field_count: Mapped[int] = mapped_column(Integer, default=0)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
