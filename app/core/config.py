from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8088
    database_url: str = "sqlite:///./data/db/djgabo_engine.db"
    redis_url: str = "redis://localhost:6379/0"
    local_sync_fallback: bool = True
    google_mode: str = "real"
    google_credentials_file: str = ""
    drive_audio_folder_id: str = "14GwUYaJRPw7nV5UlyS_XV9EbAWQqpIok"
    cover_spreadsheet_id: str = "14ytnhSOmcsh18hIQWX1YK0n7jGyQtvbr6_yuMCRlv14"
    cover_sheet_name: str = "01_CEREBRO2"
    cover_cache_seconds: int = 300
    cover_match_threshold: float = 78.0
    public_base_url: str = "http://127.0.0.1:8088"
    whatsapp_number: str = "51921675846"
    render_concurrency: int = 1
    font_bold_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
    youtube_mode: str = "mock"
    youtube_default_privacy: str = "unlisted"
    youtube_default_description: str = "Demo musical generado por DJGABO Engine"
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_oauth_client_file: str = ""
    oauth_redirect_base_url: str = "http://localhost:8088"
    token_encryption_key: str = ""
    token_encryption_key_file: str = ""
    default_cut_seconds: float = 80.0
    audio_crossfade_seconds: float = Field(default=0.25, ge=0, le=1)
    max_uploads_per_channel_24h: int = 7
    ready_buffer: int = 4
    success_retention_minutes: int = 0
    failed_retention_days: int = 7
    max_upload_file_mb: int = 500
    max_storage_gb: int = 50
    data_root: Path = Path("./data")

    @property
    def incoming_dir(self) -> Path:
        return self.data_root / "incoming"

    @property
    def processing_dir(self) -> Path:
        return self.data_root / "processing"

    @property
    def ready_dir(self) -> Path:
        return self.data_root / "ready"

    @property
    def assets_dir(self) -> Path:
        return self.data_root / "assets"

    @property
    def failed_dir(self) -> Path:
        return self.data_root / "failed"

    @property
    def mock_drive_dir(self) -> Path:
        return self.data_root / "mock_drive"

    @property
    def youtube_oauth_client_path(self) -> Path:
        return Path(self.youtube_oauth_client_file) if self.youtube_oauth_client_file else (
            self.data_root / "google-auth" / "youtube-oauth-client.json"
        )

    @property
    def token_encryption_key_path(self) -> Path:
        return Path(self.token_encryption_key_file) if self.token_encryption_key_file else (
            self.data_root / "google-auth" / "youtube-token.key"
        )

    def ensure_directories(self) -> None:
        for path in (self.incoming_dir, self.processing_dir, self.ready_dir,
                     self.assets_dir, self.failed_dir, self.mock_drive_dir, self.data_root / "db",
                     self.data_root / "google-auth"):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
