import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    PREPARED = "PREPARED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    QUEUED = "QUEUED"
    UPLOADING_SOURCE = "UPLOADING_SOURCE"
    UPLOADING_TO_DRIVE = "UPLOADING_TO_DRIVE"
    DRIVE_DUPLICATE_CONFIRMATION = "DRIVE_DUPLICATE_CONFIRMATION"
    WAITING_COVER = "WAITING_COVER"
    DOWNLOADING_AUDIO = "DOWNLOADING_AUDIO"
    RENDERING = "RENDERING"
    VALIDATING = "VALIDATING"
    MP4_READY = "MP4_READY"
    WAITING_SLOT = "WAITING_SLOT"
    UPLOADING_YOUTUBE = "UPLOADING_YOUTUBE"
    VERIFYING = "VERIFYING"
    CLEANUP = "CLEANUP"
    PUBLISHED = "PUBLISHED"
    PAUSED = "PAUSED"
    RENDER_ERROR = "RENDER_ERROR"
    UPLOAD_ERROR = "UPLOAD_ERROR"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename_original: Mapped[str] = mapped_column(String(512))
    artist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_md5: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    rendered_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    drive_file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    drive_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_drive_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duplicate_drive_file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    drive_reused: Mapped[bool] = mapped_column(Boolean, default=False)
    cover_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cover_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    frame_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    youtube_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    original_duration_seconds: Mapped[float] = mapped_column(Float)
    cut_seconds: Mapped[float] = mapped_column(Float)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    privacy_status: Mapped[str] = mapped_column(String(16), default="unlisted")
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PREPARED.value, index=True)
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    youtube_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    youtube_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    youtube_actual_privacy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pending_privacy_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    privacy_pending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    privacy_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    privacy_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    privacy_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    youtube_upload_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    youtube_failure_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    youtube_rejection_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    youtube_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    youtube_restriction_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    youtube_region_blocked: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_region_allowed: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_attention_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleanup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped["Channel | None"] = relationship(back_populates="jobs")


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(100), unique=True)
    youtube_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    oauth_status: Mapped[str] = mapped_column(String(32), default="MOCK")
    token_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    background_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    qr_background_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    youtube_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_uploads_24h: Mapped[int] = mapped_column(Integer, default=7)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    jobs: Mapped[list[Job]] = relationship(back_populates="channel")


class ChannelPublication(Base):
    __tablename__ = "channel_publications"
    __table_args__ = (UniqueConstraint("job_id", name="uq_publication_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    youtube_video_id: Mapped[str] = mapped_column(String(255))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class QrRedirect(Base):
    __tablename__ = "qr_redirects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    artist: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    youtube_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
