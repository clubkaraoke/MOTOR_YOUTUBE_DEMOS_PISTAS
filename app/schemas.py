from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JobUpdate(BaseModel):
    artist: str | None = None
    title: str | None = None
    cut_seconds: float | None = Field(default=None, gt=0)
    privacy_status: str | None = None


class ChannelUpdate(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    max_uploads_24h: int | None = Field(default=None, ge=1, le=50)
    youtube_description: str | None = Field(default=None, max_length=5000)


class AudioProtectionUpdate(BaseModel):
    default_cut_seconds: float = Field(gt=0, le=86400)
    transition_seconds: float = Field(ge=0, le=1)


class BulkPrivacyUpdate(BaseModel):
    privacy_status: str
    ids: list[str] | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename_original: str
    artist: str | None
    title: str | None
    original_duration_seconds: float
    cut_seconds: float
    channel_id: int | None
    privacy_status: str
    status: str
    progress: int
    retry_count: int
    error_code: str | None
    error_message: str | None
    youtube_video_id: str | None
    youtube_url: str | None
    youtube_deleted_at: datetime | None
    youtube_actual_privacy: str | None
    pending_privacy_status: str | None
    privacy_pending_since: datetime | None
    privacy_last_attempt_at: datetime | None
    privacy_last_error: str | None
    privacy_attempt_count: int
    youtube_upload_status: str | None
    youtube_failure_reason: str | None
    youtube_rejection_reason: str | None
    youtube_last_checked_at: datetime | None
    youtube_restriction_status: str | None
    youtube_region_blocked: str | None
    youtube_region_allowed: str | None
    youtube_attention_acknowledged_at: datetime | None
    drive_file_id: str | None
    drive_file_name: str | None
    drive_folder_id: str | None
    drive_deleted_at: datetime | None
    duplicate_drive_file_id: str | None
    duplicate_drive_file_name: str | None
    drive_reused: bool
    cover_url: str | None
    cover_match_score: float | None
    qr_token: str | None
    youtube_title: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
