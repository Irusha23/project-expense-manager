import enum
import secrets
import uuid
from datetime import datetime, date, timezone
from decimal import Decimal
from sqlalchemy import (
    String, Integer, Date, DateTime, Boolean, ForeignKey, Text, Numeric,
    Enum, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_callback_token() -> str:
    return secrets.token_hex(6)


class PurchaseStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SYNC_PENDING = "sync_pending"
    SYNCED = "synced"
    SYNC_FAILED = "sync_failed"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    purchases: Mapped[list["Purchase"]] = relationship(back_populates="project")


class Member(Base):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    purchases: Mapped[list["Purchase"]] = relationship(back_populates="purchaser")


class Component(Base):
    __tablename__ = "components"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    canonical_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (
        Index("ix_purchases_project_date", "project_id", "purchase_date"),
        Index("ix_purchases_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    purchaser_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    store_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="LKR", nullable=False)

    # All monetary values are integer minor units: LKR cents.
    subtotal_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    additional_charges_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    receipt_total_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[PurchaseStatus] = mapped_column(
        Enum(PurchaseStatus), default=PurchaseStatus.RECEIVED, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_telegram_update_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    receipt_sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    project: Mapped["Project"] = relationship(back_populates="purchases")
    purchaser: Mapped["Member"] = relationship(back_populates="purchases")
    items: Mapped[list["PurchaseItem"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")
    receipt_files: Mapped[list["ReceiptFile"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    component_id: Mapped[str | None] = mapped_column(ForeignKey("components.id"), nullable=True)
    raw_description: Mapped[str] = mapped_column(String(300))
    standardized_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1.000"))
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit_price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_total_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    purchase: Mapped["Purchase"] = relationship(back_populates="items")


class ReceiptFile(Base):
    __tablename__ = "receipt_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    local_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    drive_web_view_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    purchase: Mapped["Purchase"] = relationship(back_populates="receipt_files")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(60), default="receipt_ocr")
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(36))
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SyncOutbox(Base):
    __tablename__ = "sync_outbox"
    __table_args__ = (Index("ix_outbox_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    destination: Mapped[str] = mapped_column(String(40))  # google_sheets / google_drive
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[OutboxStatus] = mapped_column(Enum(OutboxStatus), default=OutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PurchaseSession(Base):
    """
    Persistent state for an ongoing Telegram purchase submission.

    A member can have one active submission session at a time.
    """

    __tablename__ = "purchase_sessions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=new_id,
    )

    callback_token: Mapped[str] = mapped_column(
        String(12),
        unique=True,
        index=True,
        default=new_callback_token,
        nullable=False,
    )

    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id"),
        index=True,
        nullable=False,
    )

    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        default="awaiting_project",
        index=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
