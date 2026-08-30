import json
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httplib2
from cryptography.fernet import Fernet
from google.auth.exceptions import TransportError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Channel, ChannelPublication, Job, JobStatus, QrRedirect
from app.services.oauth_client import youtube_oauth_client
from app.services.title_builder import build_youtube_title


class YouTubeError(RuntimeError):
    pass


def is_quota_exceeded(message: str) -> bool:
    lowered = message.lower()
    return (
        "quotaexceeded" in lowered
        or "exceeded your quota" in lowered
        or "dailylimitexceeded" in lowered
    )


YOUTUBE_HTTP_TIMEOUT_SECONDS = 90
UploadProgressCallback = Callable[[int], None]


def _execute(request):
    """Execute a Google API request with transient retries.

    The TypeError fallback keeps compatibility with the lightweight request
    doubles used by the test suite, while real googleapiclient requests accept
    num_retries.
    """
    try:
        return request.execute(num_retries=3)
    except TypeError as exc:
        if "num_retries" not in str(exc):
            raise
        return request.execute()


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.token_encryption_key
    if not key:
        path = settings.token_encryption_key_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(Fernet.generate_key())
            key = path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise YouTubeError(f"No se pudo preparar la clave cifrada en {path}") from exc
    return Fernet(key.encode())


def encrypt_token(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_token(value: str) -> dict:
    return json.loads(_fernet().decrypt(value.encode()).decode())


def _service(channel: Channel):
    settings = get_settings()
    client = youtube_oauth_client()
    if not channel.token_reference:
        raise YouTubeError("Canal sin autorización OAuth")
    token = decrypt_token(channel.token_reference)
    credentials = Credentials(
        token=token.get("token"), refresh_token=token.get("refresh_token"),
        token_uri=client.get("token_uri", "https://oauth2.googleapis.com/token"), client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube"],
    )
    transport = httplib2.Http(timeout=YOUTUBE_HTTP_TIMEOUT_SECONDS)
    # Google resumable uploads use HTTP 308 "Resume Incomplete". httplib2
    # otherwise treats 308 as a redirect and raises RedirectMissingLocation
    # because that upload response intentionally has no Location header.
    if hasattr(transport, "redirect_codes"):
        transport.redirect_codes = transport.redirect_codes - {308}
    http = AuthorizedHttp(credentials, http=transport)
    return build("youtube", "v3", http=http, cache_discovery=False)


def upload_video(
    job: Job,
    channel: Channel,
    progress_callback: UploadProgressCallback | None = None,
    check_existing: bool = False,
) -> tuple[str, str]:
    settings = get_settings()
    job.youtube_title = job.youtube_title or build_youtube_title(job.filename_original, job.artist, job.title)
    if job.youtube_video_id:
        if progress_callback:
            progress_callback(100)
        return job.youtube_video_id, job.youtube_url or ""
    if settings.youtube_mode == "mock":
        video_id = f"mock_{job.id.replace('-', '')[:16]}"
        if progress_callback:
            progress_callback(100)
        return video_id, f"{settings.public_base_url.rstrip('/')}/api/jobs/{job.id}/video"
    service = _service(channel)
    # Normal uploads go straight to videos.insert. Since June 2026 YouTube
    # separates videos.insert into its own quota bucket; calling channels.list
    # and playlistItems.list before every upload can exhaust the general quota
    # and block uploads even when upload quota is still available.
    #
    # We only perform the duplicate-recovery scan when explicitly requested,
    # e.g. after a prior upload attempt may have reached YouTube but the local
    # worker did not persist the returned video id.
    if check_existing:
        channels = _execute(service.channels().list(part="contentDetails", mine=True)).get("items", [])
        uploads_playlist = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"] if channels else None
        candidate_ids: list[str] = []
        if uploads_playlist:
            recent = _execute(service.playlistItems().list(
                part="contentDetails", playlistId=uploads_playlist, maxResults=50
            ))
            candidate_ids = [
                item["contentDetails"]["videoId"] for item in recent.get("items", [])
                if item.get("contentDetails", {}).get("videoId")
            ]
        if candidate_ids:
            found = _execute(service.videos().list(part="snippet,status", id=",".join(candidate_ids)))
            for item in found.get("items", []):
                if job.id in item.get("snippet", {}).get("tags", []):
                    video_id = item["id"]
                    return video_id, f"https://www.youtube.com/watch?v={video_id}"
    body = {
        "snippet": {"title": job.youtube_title, "description": channel.youtube_description or settings.youtube_default_description,
                    "tags": [job.id, "DJGABO Engine"]},
        "status": {"privacyStatus": job.privacy_status},
    }
    media = MediaFileUpload(job.rendered_path, chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    attempts = 0
    last_percent = 0
    if progress_callback:
        progress_callback(0)
    while response is None:
        try:
            status, response = request.next_chunk(num_retries=2)
            attempts = 0
            if status is not None and response is None:
                last_percent = max(last_percent, min(99, int(round(status.progress() * 100))))
                if progress_callback:
                    progress_callback(last_percent)
        except HttpError as exc:
            attempts += 1
            if exc.resp.status not in {429, 500, 502, 503, 504} or attempts > 6:
                raise YouTubeError(str(exc)) from exc
            if progress_callback:
                progress_callback(last_percent)
            time.sleep(min(2 ** attempts, 60))
        except (httplib2.HttpLib2Error, TransportError, OSError, TimeoutError) as exc:
            attempts += 1
            if attempts > 6:
                raise YouTubeError(
                    f"La conexión con YouTube no respondió después de {attempts} intentos: {exc}"
                ) from exc
            if progress_callback:
                progress_callback(last_percent)
            time.sleep(min(2 ** attempts, 60))
    if progress_callback:
        progress_callback(100)
    video_id = response["id"]
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def finalize_publication(db: Session, job: Job, channel: Channel, video_id: str, url: str, now: datetime | None = None) -> None:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    existing = db.scalar(select(ChannelPublication).where(ChannelPublication.job_id == job.id))
    job.youtube_video_id = video_id
    job.youtube_url = url
    job.published_at = job.published_at or now
    job.cleanup_at = None if settings.youtube_mode == "mock" else (
        job.cleanup_at or now + timedelta(minutes=settings.success_retention_minutes)
    )
    job.status = JobStatus.PUBLISHED.value
    job.progress = 100
    redirect = db.scalar(select(QrRedirect).where(QrRedirect.job_id == job.id))
    if redirect:
        redirect.youtube_video_id = video_id
        redirect.youtube_url = url
    if existing:
        existing.channel_id = channel.id
        existing.youtube_video_id = video_id
        existing.published_at = job.published_at
    else:
        db.add(ChannelPublication(channel_id=channel.id, job_id=job.id, youtube_video_id=video_id, published_at=job.published_at))
    db.commit()


def change_privacy(job: Job, channel: Channel, privacy: str) -> str:
    if privacy not in {"public", "unlisted", "private"}:
        raise YouTubeError("Privacidad inválida")
    actual_privacy = privacy
    if get_settings().youtube_mode == "real":
        try:
            response = _execute(
                _service(channel).videos().update(
                    part="status",
                    body={"id": job.youtube_video_id, "status": {"privacyStatus": privacy}},
                )
            )
        except HttpError as exc:
            raise YouTubeError(str(exc)) from exc
        actual_privacy = response.get("status", {}).get("privacyStatus", privacy)
    job.privacy_status = actual_privacy
    job.youtube_actual_privacy = actual_privacy
    job.pending_privacy_status = None
    job.privacy_pending_since = None
    job.privacy_last_attempt_at = datetime.now(timezone.utc)
    job.privacy_last_error = None
    job.privacy_attempt_count = 0
    job.youtube_last_checked_at = datetime.now(timezone.utc)
    return actual_privacy


def _apply_video_status(job: Job, item: dict | None, now: datetime) -> dict:
    job.youtube_last_checked_at = now
    if not item:
        job.youtube_deleted_at = job.youtube_deleted_at or now
        job.youtube_upload_status = "deleted_or_unavailable"
        if job.youtube_restriction_status != "UNAVAILABLE":
            job.youtube_attention_acknowledged_at = None
        job.youtube_restriction_status = "UNAVAILABLE"
        return {"available": False, "upload_status": job.youtube_upload_status}

    status = item.get("status", {})
    region = item.get("contentDetails", {}).get("regionRestriction", {})
    blocked = region.get("blocked") or []
    allowed = region.get("allowed")
    previous_restriction = job.youtube_restriction_status
    if len(blocked) >= 200 or allowed == []:
        restriction_status = "WORLDWIDE_BLOCKED"
    elif "PE" in blocked or (allowed is not None and "PE" not in allowed):
        restriction_status = "BLOCKED_IN_PE"
    elif blocked or allowed is not None:
        restriction_status = "REGION_RESTRICTED"
    else:
        restriction_status = None

    job.youtube_deleted_at = None
    job.youtube_actual_privacy = status.get("privacyStatus")
    job.youtube_upload_status = status.get("uploadStatus")
    job.youtube_failure_reason = status.get("failureReason")
    job.youtube_rejection_reason = status.get("rejectionReason")
    job.youtube_restriction_status = restriction_status
    job.youtube_region_blocked = json.dumps(blocked) if blocked else None
    job.youtube_region_allowed = json.dumps(allowed) if allowed is not None else None
    if previous_restriction != restriction_status:
        job.youtube_attention_acknowledged_at = None
    if job.youtube_actual_privacy:
        job.privacy_status = job.youtube_actual_privacy
    return {
        "available": True,
        "privacy_status": job.youtube_actual_privacy,
        "upload_status": job.youtube_upload_status,
        "failure_reason": job.youtube_failure_reason,
        "rejection_reason": job.youtube_rejection_reason,
        "restriction_status": job.youtube_restriction_status,
    }


def sync_video_statuses(jobs: list[Job], channel: Channel) -> dict[str, dict]:
    """Refresh up to 50 videos with one videos.list quota unit."""
    now = datetime.now(timezone.utc)
    results: dict[str, dict] = {}
    real_jobs: list[Job] = []
    for job in jobs:
        if get_settings().youtube_mode != "real" or not job.youtube_video_id or job.youtube_video_id.startswith("mock_"):
            job.youtube_actual_privacy = job.privacy_status
            job.youtube_upload_status = "processed"
            job.youtube_last_checked_at = now
            results[job.id] = {
                "available": True,
                "privacy_status": job.youtube_actual_privacy,
                "upload_status": job.youtube_upload_status,
            }
        else:
            real_jobs.append(job)

    if not real_jobs:
        return results
    if len(real_jobs) > 50:
        raise YouTubeError("sync_video_statuses admite como máximo 50 videos por llamada")

    response = _execute(
        _service(channel).videos().list(
            part="status,contentDetails",
            id=",".join(job.youtube_video_id for job in real_jobs if job.youtube_video_id),
        )
    )
    items_by_id = {item.get("id"): item for item in response.get("items", []) if item.get("id")}
    for job in real_jobs:
        results[job.id] = _apply_video_status(job, items_by_id.get(job.youtube_video_id), now)
    return results


def sync_video_status(job: Job, channel: Channel) -> dict:
    """Refresh one video status; batch callers should use sync_video_statuses."""
    return sync_video_statuses([job], channel)[job.id]


def delete_video(job: Job, channel: Channel) -> None:
    if get_settings().youtube_mode == "real" and job.youtube_video_id:
        _service(channel).videos().delete(id=job.youtube_video_id).execute()
