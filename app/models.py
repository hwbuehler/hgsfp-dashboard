"""SQLAlchemy models.

Privacy notes that shaped this schema:

* ``Response.id`` is a UUID rather than an autoincrementing integer.  A running
  number leaks the submission order, which combined with knowing who left the
  room when can de-anonymise an answer.
* ``Response.submitted_on`` is a date, not a timestamp — enough to tell which
  run a response belongs to, not enough to correlate with anything else.
* No IP address, user agent or session identifier is stored for participants.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_naive_utc(value: datetime | None) -> datetime | None:
    """Strip tzinfo so stored and freshly created datetimes can be compared.

    SQLite has no datetime type: values written as timezone-aware come back
    without tzinfo, and comparing the two forms raises TypeError.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class SurveyStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    closed = "closed"


class AnalysisStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class User(Base):
    """Dashboard account.  There is no self-signup; use scripts/create_user.py."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SurveyTemplate(Base):
    """Structural blueprint shared by every run of a survey.

    ``blueprint_json`` holds the invariant parts of the SurveyJS definition with
    ``{prefix}`` placeholders — see app/surveyjs.py and scripts/bootstrap_template.py.
    """

    __tablename__ = "survey_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    blueprint_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    surveys: Mapped[list["Survey"]] = relationship(back_populates="template")


class Survey(Base):
    """One concrete run, e.g. "56th Graduate Days"."""

    __tablename__ = "surveys"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("survey_templates.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    public_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[SurveyStatus] = mapped_column(
        Enum(SurveyStatus, native_enum=False), default=SurveyStatus.draft
    )
    intro_text: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    template: Mapped[SurveyTemplate] = relationship(back_populates="surveys")
    slots: Mapped[list["SurveySlot"]] = relationship(
        back_populates="survey",
        cascade="all, delete-orphan",
        order_by="SurveySlot.position",
    )
    responses: Mapped[list["Response"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan"
    )
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="survey",
        cascade="all, delete-orphan",
        order_by="AnalysisRun.id.desc()",
    )

    @property
    def is_open(self) -> bool:
        return self.status is SurveyStatus.published


class SurveySlot(Base):
    """One lecture slot — a page in the questionnaire.

    ``key_prefix`` is what ends up in the result JSON (``ml`` -> ``ml_title``,
    ``ml_interesting``, …), so renaming it changes the stored keys.
    """

    __tablename__ = "survey_slots"
    __table_args__ = (UniqueConstraint("survey_id", "key_prefix", name="uq_slot_prefix"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("surveys.id", ondelete="CASCADE"), index=True
    )
    key_prefix: Mapped[str] = mapped_column(String(16))
    page_title: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer, default=0)
    dna_value: Mapped[str] = mapped_column(String(64), default="DnA")
    dna_text: Mapped[str] = mapped_column(String(120), default="did not attend")

    survey: Mapped[Survey] = relationship(back_populates="slots")
    choices: Mapped[list["SlotChoice"]] = relationship(
        back_populates="slot",
        cascade="all, delete-orphan",
        order_by="SlotChoice.position",
    )

    @property
    def comment_item_name(self) -> str:
        """Name of this slot's field inside the ``sugg_lectures`` question."""
        return f"{self.key_prefix}_comment"


class SlotChoice(Base):
    """One selectable lecture.

    ``value`` is the key stored in the result JSON (e.g. ``"Ollefs"``), ``text``
    is what participants read in the dropdown (the full lecture title).  The
    "did not attend" entry is *not* stored here — it is appended automatically
    when the definition is generated so it cannot be deleted by accident.
    """

    __tablename__ = "slot_choices"

    id: Mapped[int] = mapped_column(primary_key=True)
    slot_id: Mapped[int] = mapped_column(
        ForeignKey("survey_slots.id", ondelete="CASCADE"), index=True
    )
    value: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    lecturer_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    slot: Mapped[SurveySlot] = relationship(back_populates="choices")


class Response(Base):
    """One anonymous submission, stored as the raw SurveyJS result object."""

    __tablename__ = "responses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("surveys.id", ondelete="CASCADE"), index=True
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    submitted_on: Mapped[date] = mapped_column(Date, default=lambda: utcnow().date())

    survey: Mapped[Survey] = relationship(back_populates="responses")


class AnalysisRun(Base):
    """Cached result of one analysis pass.

    Computing statistics is cheap, but clustering loads a sentence-transformer
    model and takes seconds, so it runs as a background job and both the HTML
    preview and the PDF export read from ``result_json``.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("surveys.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AnalysisStatus] = mapped_column(
        Enum(AnalysisStatus, native_enum=False), default=AnalysisStatus.pending
    )
    response_count: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    survey: Mapped[Survey] = relationship(back_populates="analysis_runs")
