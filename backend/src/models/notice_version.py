from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import now_local


class NoticeVersion(Base):
    """同一来源公告的正文版本链。

    - 首次抓取：active；来源发布更正：旧版本 superseded、新版本 active；
    - 来源撤下：当前版本 withdrawn（保留历史，不物理删除）。
    自然键 (notice_id, version_no) 保证重复采集不新增重复版本。
    """

    __tablename__ = "notice_versions"
    __table_args__ = (
        UniqueConstraint("notice_id", "version_no", name="uq_notice_version_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_html: Mapped[str] = mapped_column(Text)
    clean_text: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    change_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    crawl_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_runs.id"), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
