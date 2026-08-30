import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock
from google.auth.exceptions import RefreshError
from redis import Redis
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import Channel, Job, JobStatus, QrRedirect, Setting
from app.services.cleanup import cleanup_due
from app.services.cover_provider import cover_provider
from app.services.frame_builder import create_frame
from app.services.google_drive import DriveAudioStorage, GoogleStorageError
from app.services.media import MediaError, create_demo_video, validate_demo_video
from app.services.qr import whatsapp_url
from app.services.scheduler import choose_channel
from app.services.title_builder import build_youtube_title
from app.services.youtube import YouTubeError, finalize_publication, upload_video

log = logging.getLogger("djgabo.worker")

# One production transition per YouTube channel during the one-week visual test.
CHANNEL_IMAGE_TRANSITIONS = {
    1: "diagtr",
    2: "smoothleft",
    3: "circleopen",
    4: "dissolve",
}


def _setting(db, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


def _ensure_qr(db, job: Job) -> QrRedirect:
    redirect = db.scalar(select(QrRedirect).where(QrRedirect.job_id == job.id))
    if redirect:
        return redirect
    token = secrets.token_urlsafe(24)
    redirect = QrRedirect(token=token, job_id=job.id, artist=job.artist or "", title=job.title or "")
    job.qr_token = token
    db.add(redirect)
    db.commit()
    return redirect


def _resolve_cover(db, job: Job) -> bool:
    if job.cover_url:
        return True
    try:
        match = cover_provider.find(job.artist, job.title, job.filename_original)
    except GoogleStorageError as exc:
        job.status = JobStatus.WAITING_COVER.value
        job.error_code = "COVER_CATALOG_UNAVAILABLE"
        job.error_message = str(exc)
        db.commit()
        return False
    if not match:
        job.status = JobStatus.WAITING_COVER.value
        job.error_code = "COVER_NOT_FOUND"
        job.error_message = "Esperando un cover válido en 01_CEREBRO2"
        db.commit()
        return False
    job.cover_url = match.entry.cover_url
    job.cover_match_score = match.score
    job.artist = job.artist or match.entry.artist
    job.title = job.title or match.entry.title
    job.error_code = job.error_message = None
    db.commit()
    return True


def _process_job(job_id: str) -> None:
    settings = get_settings()
    storage = DriveAudioStorage()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status in {JobStatus.PAUSED.value, JobStatus.PUBLISHED.value}:
            return
        if not 0 < job.cut_seconds < job.original_duration_seconds:
            job.status = JobStatus.RENDER_ERROR.value
            job.error_code = "CUT_OUT_OF_RANGE"
            job.error_message = "El punto de corte no es válido para esta pista"
            db.commit()
            return
        if not _resolve_cover(db, job):
            return
        recovery_channel = None
        if job.previous_status in {JobStatus.UPLOADING_YOUTUBE.value, JobStatus.VERIFYING.value} and job.channel_id:
            recovery_channel = db.get(Channel, job.channel_id)
        if recovery_channel:
            channel = recovery_channel
        else:
            slot = choose_channel(db)
            if not slot:
                job.status = JobStatus.WAITING_SLOT.value
                job.progress = 0
                db.commit()
                return
            channel = slot.channel
            job.channel_id = channel.id
        commercial = _setting(db, "commercial_audio_path")
        background = channel.background_image_path or _setting(db, "general_background_path") or _setting(db, "fallback_background_path")
        qr_background = channel.qr_background_image_path or background
        if not commercial or not Path(commercial).is_file():
            job.status = JobStatus.RENDER_ERROR.value
            job.error_code = "COMMERCIAL_AUDIO_MISSING"
            job.error_message = "Configura el audio comercial predeterminado"
            db.commit()
            return
        if not background or not Path(background).is_file() or not qr_background or not Path(qr_background).is_file():
            job.status = JobStatus.RENDER_ERROR.value
            job.error_code = "BACKGROUND_MISSING"
            job.error_message = "Configura las dos imágenes 1280×720 del canal"
            db.commit()
            return
        workdir = settings.processing_dir / job.id
        workdir.mkdir(parents=True, exist_ok=True)
        cover_path = workdir / "cover.png"
        try:
            cover_provider.download(job.cover_url, cover_path)
        except ValueError as exc:
            job.cover_url = None
            job.cover_match_score = None
            job.status = JobStatus.WAITING_COVER.value
            job.error_code = "COVER_INVALID"
            job.error_message = str(exc)
            db.commit()
            return
        job.cover_path = str(cover_path)
        if not job.original_path or not Path(job.original_path).is_file():
            if not job.drive_file_id:
                job.status = JobStatus.RENDER_ERROR.value
                job.error_code = "SOURCE_MISSING"
                job.error_message = "No existe audio local ni referencia de Google Drive"
                db.commit()
                return
            extension = Path(job.drive_file_name or job.filename_original).suffix.lower() or ".audio"
            audio_path = workdir / f"original{extension}"
            job.status = JobStatus.DOWNLOADING_AUDIO.value
            db.commit()
            try:
                storage.download(job.drive_file_id, audio_path)
            except GoogleStorageError as exc:
                job.status = JobStatus.RENDER_ERROR.value
                job.error_code = "DRIVE_DOWNLOAD_ERROR"
                job.error_message = str(exc)
                db.commit()
                return
            job.original_path = str(audio_path)
            db.commit()
        redirect = _ensure_qr(db, job)
        intro_frame_path = workdir / "frame_intro.png"
        frame_path = workdir / "frame_qr.png"
        qr_url = whatsapp_url(settings.whatsapp_number, job.filename_original)
        output = settings.ready_dir / f"{job.id}.mp4"
        job.youtube_title = build_youtube_title(job.filename_original, job.artist, job.title)
        try:
            reusable = output.is_file()
            if reusable:
                try:
                    validate_demo_video(output, job.original_duration_seconds)
                except MediaError:
                    output.unlink(missing_ok=True)
                    reusable = False
            if not reusable:
                with FileLock(str(settings.processing_dir / "ffmpeg.lock"), timeout=12 * 60 * 60):
                    create_frame(Path(background), cover_path, job.artist or "", job.title or Path(job.filename_original).stem,
                                 qr_url, intro_frame_path, settings.whatsapp_number, include_qr=False)
                    create_frame(Path(qr_background), cover_path, job.artist or "", job.title or Path(job.filename_original).stem,
                                 qr_url, frame_path, settings.whatsapp_number, include_qr=True)
                    job.frame_path = str(frame_path)
                    job.status = JobStatus.RENDERING.value
                    job.progress = 5
                    db.commit()

                    def progress(value: int) -> None:
                        job.progress = min(value, 94)
                        db.commit()

                    transition_row = _setting(db, "audio_crossfade_seconds")
                    transition = float(transition_row) if transition_row is not None else settings.audio_crossfade_seconds
                    create_demo_video(
                        job.original_path,
                        intro_frame_path,
                        commercial,
                        job.cut_seconds,
                        output,
                        transition,
                        progress,
                        qr_background_image=frame_path,
                        image_switch_seconds=20.0,
                        image_transition=CHANNEL_IMAGE_TRANSITIONS.get(channel.id, "fade"),
                        animation_until_seconds=40.0,
                        animation_scene_seconds=5.0,
                        image_transition_seconds=0.5,
                    )
            job.rendered_path = str(output)
            job.status = JobStatus.MP4_READY.value
            job.progress = 95
            db.commit()
        except (MediaError, OSError) as exc:
            job.status = JobStatus.RENDER_ERROR.value
            job.error_code = "FFMPEG_OR_FRAME_ERROR"
            job.error_message = str(exc)[-2000:]
            db.commit()
            return
        try:
            job.status = JobStatus.UPLOADING_YOUTUBE.value
            job.progress = 0
            job.upload_operation_id = job.upload_operation_id or f"job:{job.id}"
            job.error_code = job.error_message = None
            db.commit()

            def youtube_progress(value: int) -> None:
                job.progress = max(0, min(100, int(value)))
                db.commit()

            video_id, url = upload_video(job, channel, progress_callback=youtube_progress)
            job.status = JobStatus.VERIFYING.value
            job.progress = 100
            db.commit()
            finalize_publication(db, job, channel, video_id, url)
            if settings.youtube_mode == "real":
                job.status = JobStatus.CLEANUP.value
                db.commit()
                cleanup_due(db, datetime.now(timezone.utc))
            else:
                job.status = JobStatus.PUBLISHED.value
                db.commit()
        except (YouTubeError, RefreshError) as exc:
            message = str(exc)
            job.status = JobStatus.UPLOAD_ERROR.value
            job.retry_count += 1
            if "invalid_grant" in message or "expired or revoked" in message:
                job.error_code = "YOUTUBE_OAUTH_RECONNECT_REQUIRED"
                job.error_message = "La autorización OAuth del canal venció o fue revocada. Reconecta el canal y pulsa Reintentar."
                channel.oauth_status = "RECONNECT_REQUIRED"
            else:
                job.error_code = "YOUTUBE_ERROR"
                job.error_message = message[-2000:]
            db.commit()
        except Exception as exc:
            # Never leave a failed RQ job pretending that it is still uploading.
            # Google API/httplib2 can raise exceptions outside the explicit
            # YouTubeError path (for example during channels/playlist preflight).
            log.exception("youtube_upload_unexpected job_id=%s", job.id)
            job.status = JobStatus.UPLOAD_ERROR.value
            job.retry_count += 1
            job.error_code = "YOUTUBE_UNEXPECTED_ERROR"
            job.error_message = f"{type(exc).__name__}: {exc}"[-2000:]
            db.commit()


def process_job(job_id: str) -> None:
    try:
        for attempt in range(6):
            try:
                _process_job(job_id)
                break
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt == 5:
                    raise
                delay = min(2 ** attempt, 15)
                log.warning("database_locked_retry job_id=%s attempt=%s delay=%ss", job_id, attempt + 1, delay)
                time.sleep(delay)
    finally:
        try:
            Redis.from_url(get_settings().redis_url).delete(f"djgabo:enqueued:{job_id}")
        except Exception:
            log.warning("queue_guard_release_failed job_id=%s", job_id)
