from sqlalchemy import inspect, text


JOB_COLUMNS = {
    "source_md5": "VARCHAR(32)",
    "source_size_bytes": "INTEGER",
    "drive_file_id": "VARCHAR(255)",
    "drive_file_name": "VARCHAR(512)",
    "drive_folder_id": "VARCHAR(255)",
    "drive_deleted_at": "DATETIME",
    "duplicate_drive_file_id": "VARCHAR(255)",
    "duplicate_drive_file_name": "VARCHAR(512)",
    "drive_reused": "BOOLEAN DEFAULT 0",
    "cover_url": "VARCHAR(2048)",
    "cover_match_score": "FLOAT",
    "cover_path": "VARCHAR(1024)",
    "frame_path": "VARCHAR(1024)",
    "qr_token": "VARCHAR(64)",
    "youtube_title": "VARCHAR(100)",
    "youtube_deleted_at": "DATETIME",
    "youtube_actual_privacy": "VARCHAR(16)",
    "pending_privacy_status": "VARCHAR(16)",
    "privacy_pending_since": "DATETIME",
    "privacy_last_attempt_at": "DATETIME",
    "privacy_last_error": "TEXT",
    "privacy_attempt_count": "INTEGER DEFAULT 0",
    "youtube_upload_status": "VARCHAR(32)",
    "youtube_failure_reason": "VARCHAR(80)",
    "youtube_rejection_reason": "VARCHAR(80)",
    "youtube_last_checked_at": "DATETIME",
    "youtube_restriction_status": "VARCHAR(40)",
    "youtube_region_blocked": "TEXT",
    "youtube_region_allowed": "TEXT",
    "youtube_attention_acknowledged_at": "DATETIME",
}

CHANNEL_COLUMNS = {
    "youtube_description": "TEXT",
    "qr_background_image_path": "VARCHAR(1024)",
}


def apply_sqlite_upgrades(engine) -> None:
    """Small install-safe migration for the existing V2 SQLite database."""
    if engine.dialect.name != "sqlite":
        return
    existing = {column["name"] for column in inspect(engine).get_columns("jobs")}
    existing_channels = {column["name"] for column in inspect(engine).get_columns("channels")}
    with engine.begin() as connection:
        for name, sql_type in JOB_COLUMNS.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}"))
        for name, sql_type in CHANNEL_COLUMNS.items():
            if name not in existing_channels:
                connection.execute(text(f"ALTER TABLE channels ADD COLUMN {name} {sql_type}"))
