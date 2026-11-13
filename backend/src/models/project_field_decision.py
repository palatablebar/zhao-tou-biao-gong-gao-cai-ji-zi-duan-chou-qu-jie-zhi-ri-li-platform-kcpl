from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from src.models import Base
from src.utils.timeutil import now_local


class ProjectFieldDecision(Base):
    """项目级字段裁决：一行 = 一个项目的一个字段的最终裁决。

    - 保留裁决值、依据来源（source_notice_id / site_id）、置信度与裁决理由；
    - candidates 记录各来源候选值，用于前端展示来源冲突；
    - status=manual 时为人工确认/修正，后续采集不得覆盖；
    - 自然键 (project_id, field_key) 保证重复采集不新增裁决行。
    """

    __tablename__ = "project_field_decisions"
    __table_args__ = (
        UniqueConstraint("project_id", "field_key", name="uq_project_field"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    field_key: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    basis: Mapped[str] = mapped_column(String(30), default="none")
    value_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount_yuan: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    precision: Mapped[str] = mapped_column(String(20), default="missing")
    is_estimated: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id"), nullable=True)
    source_site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(400), default="")
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    reviewer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)
