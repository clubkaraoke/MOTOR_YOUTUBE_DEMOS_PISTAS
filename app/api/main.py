import hashlib
import logging
import os
from queue import Queue as LocalQueue
import shutil
import subprocess
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google_auth_oauthlib.flow import Flow
from PIL import Image, UnidentifiedImageError
from redis import Redis
from rq import Queue, Worker
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine, get_db
from app.core.migrations import apply_sqlite_upgrades
from app.models.entities import Channel, ChannelPublication, Job, JobStatus, QrRedirect, Setting
from app.schemas import AudioProtectionUpdate, BulkPrivacyUpdate, ChannelUpdate, JobOut, JobUpdate
from app.services.cleanup import cleanup_due
from app.services.cover_provider import cover_provider
from app.services.google_drive import DriveAudioStorage, GoogleStorageError
from app.services.frame_builder import create_frame
from app.services.media import MediaError, probe_duration
from app.services.oauth_client import YouTubeOAuthConfigError, google_client_config
from app.services.qr import whatsapp_url
from app.services.scheduler import channel_slots, global_next_slot
from app.services.youtube import (
    YouTubeError,
    change_privacy,
    delete_video,
    encrypt_token,
    is_quota_exceeded,
    next_general_quota_reset_utc,
    sync_video_status,
    sync_video_statuses,
)

log = logging.getLogger("djgabo.api")
settings = get_settings()
DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "assets" / "default_templates"
DEFAULT_TEMPLATE_MIGRATION_KEY = "single_template_defaults_v1"
queue = Queue("djgabo", connection=Redis.from_url(settings.redis_url), default_timeout="12h")
scheduler = BackgroundScheduler(timezone="UTC")
local_running: set[str] = set()
local_running_lock = threading.Lock()
local_jobs: LocalQueue[str] = LocalQueue()
local_worker_started = False


def _stored_float(db: Session, key: str, fallback: float) -> float:
    row = db.get(Setting, key)
    return float(row.value) if row else float(fallback)


def _local_worker_loop() -> None:
    """Single development worker: never run more than one FFmpeg render at once."""
    from app.workers.tasks import process_job
    while True:
        job_id = local_jobs.get()
        try:
            process_job(job_id)
        except Exception:
            log.exception("local_worker_failed job_id=%s", job_id)
        finally:
            with local_running_lock:
                local_running.discard(job_id)
            local_jobs.task_done()


def _ensure_local_worker() -> None:
    global local_worker_started
    with local_running_lock:
        if local_worker_started:
            return
        threading.Thread(target=_local_worker_loop, daemon=True, name="djgabo-local-worker").start()
        local_worker_started = True


def enqueue(job_id: str) -> bool:
    guard = f"djgabo:enqueued:{job_id}"
    try:
        if not queue.connection.set(guard, "1", nx=True, ex=12 * 60 * 60):
            return False
        # RQ 2.5 rejects job IDs containing ":". Use a safe separator so jobs
        # reach Redis instead of remaining indefinitely in QUEUED state.
        queue.enqueue("app.workers.tasks.process_job", job_id, job_id=f"process-{job_id}-{uuid.uuid4()}",
                      result_ttl=0, failure_ttl=3600)
        return True
    except Exception as exc:
        try:
            queue.connection.delete(guard)
        except Exception:
            pass
        if settings.local_sync_fallback:
            _ensure_local_worker()
            with local_running_lock:
                if job_id in local_running:
                    return False
                local_running.add(job_id)
            local_jobs.put(job_id)
            log.info("local_sync_fallback job_id=%s", job_id)
            return True
        log.warning("queue_unavailable job_id=%s error=%s", job_id, type(exc).__name__)
        return False


def dispatch_waiting() -> None:
    with SessionLocal() as db:
        # Pull a wider candidate window so jobs cooling down after a YouTube
        # uploadLimitExceeded do not block newer work behind them.
        jobs = db.scalars(select(Job).where(Job.status.in_([
            JobStatus.QUEUED.value, JobStatus.WAITING_SLOT.value, JobStatus.MP4_READY.value,
            JobStatus.RENDERING.value, JobStatus.VALIDATING.value,
        ])).order_by(Job.created_at).limit(max(50, settings.ready_buffer * 10))).all()
        now = datetime.now(timezone.utc)
        accepted = 0
        for job in jobs:
            updated = job.updated_at if job.updated_at.tzinfo else job.updated_at.replace(tzinfo=timezone.utc)

            # YouTube's per-channel upload-count limit is not worth probing
            # every 10 seconds. Recheck automatically every 15 minutes.
            if (
                job.status == JobStatus.WAITING_SLOT.value
                and job.error_code == "YOUTUBE_UPLOAD_LIMIT"
                and updated > now - timedelta(minutes=15)
            ):
                continue

            if job.status in {JobStatus.RENDERING.value, JobStatus.VALIDATING.value} and updated < now - timedelta(hours=1):
                try:
                    queue.connection.delete(f"djgabo:enqueued:{job.id}")
                except Exception:
                    pass
            if enqueue(job.id):
                accepted += 1
                if accepted >= settings.ready_buffer:
                    break


def recover_stalled() -> dict:
    """Recover abandoned stages only when RQ no longer owns the job."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    recovered: list[str] = []
    failed: list[str] = []
    redis = queue.connection
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(
            Job.status.in_([
                JobStatus.UPLOADING_TO_DRIVE.value,
                JobStatus.DOWNLOADING_AUDIO.value,
                JobStatus.RENDERING.value,
                JobStatus.VALIDATING.value,
                JobStatus.UPLOADING_YOUTUBE.value,
                JobStatus.VERIFYING.value,
            ]),
            Job.updated_at < cutoff,
        ).order_by(Job.updated_at)).all()
        storage = DriveAudioStorage()
        active_job_ids: set[str] = set()
        try:
            for worker in Worker.all(connection=redis):
                current = worker.get_current_job()
                if current and current.args:
                    active_job_ids.add(str(current.args[0]))
        except Exception:
            # If Redis/RQ worker inspection fails, do not risk duplicating a
            # publication that may still be active.
            log.exception("stalled_worker_inspection_failed")
            return {"recovered": recovered, "failed": failed}

        for job in jobs:
            guard = f"djgabo:enqueued:{job.id}"
            if job.id in active_job_ids:
                log.info(
                    "stalled_skip_active job_id=%s status=%s updated_at=%s",
                    job.id, job.status, job.updated_at,
                )
                continue
            if redis.exists(guard):
                log.warning(
                    "stalled_guard_released_no_active_worker job_id=%s status=%s updated_at=%s",
                    job.id, job.status, job.updated_at,
                )
                redis.delete(guard)
            if job.status == JobStatus.UPLOADING_TO_DRIVE.value:
                source = Path(job.original_path) if job.original_path else None
                try:
                    if not job.drive_file_id:
                        if source and source.is_file():
                            drive_file = storage.upload(source, job.drive_file_name or job.filename_original,
                                                        sha256=job.sha256)
                        else:
                            drive_file = storage.find_duplicate(job.sha256, job.source_md5 or "",
                                                                job.source_size_bytes or 0)
                            if not drive_file:
                                raise GoogleStorageError("No se encontró la fuente local ni el audio ya subido en Drive")
                        job.drive_file_id = drive_file["id"]
                        job.drive_file_name = drive_file.get("name", job.filename_original)
                    if source:
                        source.unlink(missing_ok=True)
                    job.original_path = None
                    job.status = JobStatus.WAITING_COVER.value
                    job.progress = 100
                    job.error_code = "RECOVERED_STALLED_DRIVE"
                    job.error_message = "Subida a Drive recuperada automáticamente"
                    recovered.append(job.id)
                except GoogleStorageError as exc:
                    job.status = JobStatus.UPLOAD_ERROR.value
                    job.error_code = "STALLED_DRIVE_RECOVERY_ERROR"
                    job.error_message = str(exc)
                    failed.append(job.id)
                db.commit()
                continue
            job.previous_status = job.status
            job.status = JobStatus.VERIFYING.value if job.youtube_video_id else JobStatus.QUEUED.value
            job.progress = 95 if job.youtube_video_id else 0
            job.error_code = "RECOVERED_STALLED_JOB"
            job.error_message = "Trabajo reanudado automáticamente después de una interrupción"
            db.commit()
            redis.delete(guard)
            if enqueue(job.id):
                recovered.append(job.id)
    return {"recovered": recovered, "failed": failed}


def initialize() -> None:
    settings.ensure_directories()
    Base.metadata.create_all(engine)
    apply_sqlite_upgrades(engine)
    with SessionLocal() as db:
        def portable_data_path(value: str | None) -> str | None:
            if not value:
                return value
            normalized = value.replace("\\", "/")
            if normalized.startswith("data/"):
                return str(settings.data_root / normalized.removeprefix("data/"))
            return value

        for channel in db.scalars(select(Channel)).all():
            channel.background_image_path = portable_data_path(channel.background_image_path)
            channel.qr_background_image_path = portable_data_path(channel.qr_background_image_path)
        for key in ("commercial_audio_path", "general_background_path", "fallback_background_path"):
            row = db.get(Setting, key)
            if row:
                row.value = portable_data_path(row.value) or row.value
        if not db.scalar(select(Channel.id).limit(1)):
            for index in range(1, 5):
                db.add(Channel(display_name=f"C{index}", max_uploads_24h=settings.max_uploads_per_channel_24h,
                               oauth_status="MOCK" if settings.youtube_mode == "mock" else "DISCONNECTED"))
            db.flush()

        # Migración única al nuevo sistema: una sola plantilla 1280×720 por canal.
        # Las cuatro plantillas vienen empaquetadas con la aplicación y se copian
        # al volumen persistente para que después puedan reemplazarse desde el panel.
        if not db.get(Setting, DEFAULT_TEMPLATE_MIGRATION_KEY):
            for index in range(1, 5):
                channel = db.scalar(select(Channel).where(Channel.display_name == f"C{index}"))
                source = DEFAULT_TEMPLATE_DIR / f"C{index}.png"
                if not channel or not source.is_file():
                    raise RuntimeError(f"No se encontró la plantilla predeterminada C{index}: {source}")
                target = settings.assets_dir / f"channel_{channel.id}_template.png"
                shutil.copy2(source, target)
                channel.background_image_path = str(target)
                # La segunda imagen del sistema anterior queda fuera de uso.
                channel.qr_background_image_path = None
            db.merge(Setting(key=DEFAULT_TEMPLATE_MIGRATION_KEY, value="1"))

        if not db.get(Setting, "default_cut_seconds"):
            db.add(Setting(key="default_cut_seconds", value=str(settings.default_cut_seconds)))
        if not db.get(Setting, "audio_crossfade_seconds"):
            db.add(Setting(key="audio_crossfade_seconds", value=str(settings.audio_crossfade_seconds)))
        elif settings.youtube_mode == "real":
            for channel in db.scalars(select(Channel).where(Channel.oauth_status == "MOCK")).all():
                channel.oauth_status = "DISCONNECTED"
        if settings.youtube_mode == "mock":
            completed_previews = db.scalars(select(Job).where(
                Job.status == JobStatus.CLEANUP.value,
                Job.youtube_video_id.like("mock_%"),
            )).all()
            for job in completed_previews:
                job.status = JobStatus.PUBLISHED.value
                job.cleanup_at = None
        interrupted_drive = db.scalars(select(Job).where(Job.status == JobStatus.UPLOADING_TO_DRIVE.value)).all()
        storage = DriveAudioStorage()
        if settings.google_mode == "real":
            mock_jobs = db.scalars(select(Job).where(
                Job.drive_file_id.like("mock_%"), Job.youtube_video_id.is_(None)
            )).all()
            for job in mock_jobs:
                matches = list(settings.mock_drive_dir.glob(f"{job.drive_file_id}.*"))
                if not matches:
                    job.status = JobStatus.UPLOAD_ERROR.value
                    job.error_code = "MOCK_SOURCE_MISSING"
                    job.error_message = "No se encontró el audio local pendiente para migrarlo a Google Drive"
                    continue
                try:
                    old_id = job.drive_file_id
                    drive_file = storage.upload(matches[0], job.drive_file_name or job.filename_original)
                    job.drive_file_id = drive_file["id"]
                    job.drive_file_name = drive_file.get("name", job.filename_original)
                    job.drive_folder_id = settings.drive_audio_folder_id
                    job.status = JobStatus.WAITING_COVER.value
                    job.error_code = "COVER_PENDING_AFTER_MIGRATION"
                    job.error_message = "Audio migrado a Google Drive; esperando validación del cover"
                    db.commit()
                    for source in settings.mock_drive_dir.glob(f"{old_id}.*"):
                        source.unlink(missing_ok=True)
                except GoogleStorageError as exc:
                    job.status = JobStatus.UPLOAD_ERROR.value
                    job.error_code = "DRIVE_MIGRATION_ERROR"
                    job.error_message = str(exc)
        for job in interrupted_drive:
            source = Path(job.original_path) if job.original_path else None
            try:
                if not job.drive_file_id:
                    if source and source.is_file():
                        drive_file = storage.upload(source, job.drive_file_name or job.filename_original,
                                                    sha256=job.sha256)
                    else:
                        drive_file = storage.find_duplicate(job.sha256, job.source_md5 or "",
                                                            job.source_size_bytes or 0)
                        if not drive_file:
                            raise GoogleStorageError("No existe la fuente local ni se encontró el audio subido en Drive")
                    job.drive_file_id = drive_file["id"]
                    job.drive_file_name = drive_file.get("name", job.filename_original)
                if source:
                    source.unlink(missing_ok=True)
                job.original_path = None
                job.status = JobStatus.WAITING_COVER.value
                job.error_code = "COVER_PENDING_AFTER_RESTART"
                job.error_message = "La subida a Drive se recuperó; esperando validación del cover"
            except GoogleStorageError as exc:
                job.status = JobStatus.UPLOAD_ERROR.value
                job.error_code = "DRIVE_UPLOAD_RECOVERY_ERROR"
                job.error_message = str(exc)
        quota_preflight_failures = db.scalars(select(Job).where(
            Job.status == JobStatus.UPLOAD_ERROR.value,
            Job.youtube_video_id.is_(None),
            Job.retry_count == 1,
            Job.error_message.contains("quotaExceeded"),
            Job.error_message.contains("/youtube/v3/channels"),
        )).all()
        for job in quota_preflight_failures:
            # channels.list failed before videos.insert was called, so this
            # specific retry is safe to send directly to the upload endpoint.
            job.status = JobStatus.QUEUED.value
            job.error_code = "RECOVERED_YOUTUBE_PREFLIGHT_QUOTA"
            try:
                queue.connection.delete(f"djgabo:enqueued:{job.id}")
            except Exception:
                pass

        # Existing jobs that YouTube rejected with uploadLimitExceeded are not
        # permanent failures. Preserve their assigned channel and move them to
        # the automatic waiting queue.
        upload_limit_failures = db.scalars(select(Job).where(
            Job.youtube_video_id.is_(None),
            or_(
                Job.error_code == "YOUTUBE_UPLOAD_LIMIT",
                Job.error_message.contains("uploadLimitExceeded"),
                Job.error_message.contains("exceeded the number of videos they may upload"),
                # Repair the five C3 jobs touched by the immediately previous
                # restart path, which copied WAITING_SLOT into previous_status.
                (Job.previous_status == JobStatus.WAITING_SLOT.value) & (Job.retry_count > 0),
            ),
        )).all()
        c3 = db.scalar(select(Channel).where(Channel.display_name == "C3"))
        for job in upload_limit_failures:
            if job.previous_status == JobStatus.WAITING_SLOT.value and c3:
                # One-time repair for the known C3 upload-limit incident.
                job.channel_id = c3.id
            job.previous_status = JobStatus.UPLOADING_YOUTUBE.value
            job.status = JobStatus.WAITING_SLOT.value
            job.progress = 0
            job.error_code = "YOUTUBE_UPLOAD_LIMIT"
            channel = db.get(Channel, job.channel_id) if job.channel_id else None
            channel_name = channel.display_name if channel else "El canal asignado"
            job.error_message = (
                f"{channel_name} alcanzó temporalmente el límite de subidas de YouTube. "
                "La pista queda reservada para ese mismo canal y se reintentará automáticamente."
            )
            try:
                queue.connection.delete(f"djgabo:enqueued:{job.id}")
            except Exception:
                pass

        interrupted = db.scalars(select(Job).where(Job.status.in_([
            JobStatus.RENDERING.value, JobStatus.VALIDATING.value, JobStatus.MP4_READY.value,
            JobStatus.DOWNLOADING_AUDIO.value,
            JobStatus.UPLOADING_YOUTUBE.value, JobStatus.VERIFYING.value,
        ]))).all()
        for job in interrupted:
            job.previous_status = job.status
            if job.youtube_video_id:
                job.status = JobStatus.VERIFYING.value
            else:
                job.status = JobStatus.QUEUED.value
            try:
                queue.connection.delete(f"djgabo:enqueued:{job.id}")
            except Exception:
                pass
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    scheduler.add_job(dispatch_waiting, "interval", seconds=10, id="dispatch", replace_existing=True, max_instances=1)
    scheduler.add_job(recover_stalled, "interval", minutes=1, id="stalled-recovery",
                      replace_existing=True, max_instances=1)
    scheduler.add_job(lambda: _cleanup(), "interval", minutes=1, id="cleanup", replace_existing=True, max_instances=1)
    scheduler.add_job(_refresh_covers, "interval", seconds=settings.cover_cache_seconds,
                      id="covers", replace_existing=True, max_instances=1)
    scheduler.add_job(_sync_youtube_statuses, "interval", minutes=15,
                      id="youtube-status", replace_existing=True, max_instances=1)
    scheduler.add_job(_process_pending_privacy_changes, "interval", minutes=30,
                      id="youtube-privacy-queue", replace_existing=True, max_instances=1)
    scheduler.start()
    _process_pending_privacy_changes()
    dispatch_waiting()
    yield
    scheduler.shutdown(wait=False)


def _cleanup() -> None:
    with SessionLocal() as db:
        cleanup_due(db)


def _queue_privacy_change(job: Job, target: str, error: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    job.pending_privacy_status = target
    job.privacy_pending_since = job.privacy_pending_since or now
    job.privacy_last_attempt_at = now
    job.privacy_last_error = (error or "Esperando cuota disponible de YouTube")[-2000:]
    job.privacy_attempt_count = (job.privacy_attempt_count or 0) + 1


def _quota_blocked_until(db: Session) -> datetime | None:
    row = db.get(Setting, "youtube_general_quota_blocked_until")
    if not row or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _set_quota_blocked_until(db: Session, value: datetime) -> None:
    db.merge(Setting(key="youtube_general_quota_blocked_until", value=value.isoformat()))
    db.commit()


def _ensure_auto_public_queue(db: Session) -> int:
    """Keep every published engine video on the path to public."""
    jobs = db.scalars(
        select(Job).where(
            Job.youtube_video_id.is_not(None),
            Job.youtube_video_id.not_like("mock_%"),
            Job.youtube_deleted_at.is_(None),
        ).order_by(Job.published_at)
    ).all()
    queued = 0
    now = datetime.now(timezone.utc)
    for job in jobs:
        actual = job.youtube_actual_privacy or job.privacy_status
        if actual == "public" and not job.pending_privacy_status:
            continue
        if actual != "public" or job.pending_privacy_status != "public":
            job.pending_privacy_status = "public"
            job.privacy_pending_since = job.privacy_pending_since or job.published_at or now
            if not job.privacy_last_error:
                job.privacy_last_error = "Modo automático: pendiente de pasar a público"
            queued += 1
    if queued:
        db.commit()
    return queued


def _process_pending_privacy_changes() -> dict:
    """Make every published video public while keeping a conservative quota budget."""
    changed: list[str] = []
    errors: list[dict] = []
    quota_blocked = False
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        _ensure_auto_public_queue(db)

        blocked_until = _quota_blocked_until(db)
        if blocked_until and blocked_until > now:
            pending = db.scalar(
                select(func.count(Job.id)).where(Job.pending_privacy_status == "public")
            ) or 0
            return {
                "changed": 0,
                "ids": [],
                "errors": [],
                "quota_blocked": True,
                "blocked_until": blocked_until.isoformat(),
                "pending": pending,
            }

        # 3 updates every 30 minutes = at most 144 privacy updates/day.
        # At 50 general-quota units each, that caps this automation near
        # 7,200 units/day and leaves margin for status checks and admin actions.
        jobs = db.scalars(
            select(Job).where(
                Job.pending_privacy_status == "public",
                Job.youtube_video_id.is_not(None),
                Job.youtube_video_id.not_like("mock_%"),
                Job.youtube_deleted_at.is_(None),
            ).order_by(Job.privacy_pending_since, Job.published_at).limit(3)
        ).all()

        for job in jobs:
            actual = job.youtube_actual_privacy or job.privacy_status
            if actual == "public":
                job.pending_privacy_status = None
                job.privacy_pending_since = None
                job.privacy_last_error = None
                job.privacy_attempt_count = 0
                db.commit()
                changed.append(job.id)
                continue

            job.privacy_last_attempt_at = datetime.now(timezone.utc)
            job.privacy_attempt_count = (job.privacy_attempt_count or 0) + 1
            try:
                actual = change_privacy(job, job.channel, "public")
                if actual != "public":
                    job.privacy_last_error = (
                        f"YouTube respondió con privacidad {actual or 'desconocida'} en vez de public"
                    )
                    errors.append({"id": job.id, "error": job.privacy_last_error})
                else:
                    changed.append(job.id)
                db.commit()
            except YouTubeError as exc:
                message = str(exc)
                if is_quota_exceeded(message):
                    reset_at = next_general_quota_reset_utc()
                    job.privacy_last_error = (
                        f"Cuota general de YouTube agotada. Reintento automático después de "
                        f"{reset_at.isoformat()}."
                    )
                    db.commit()
                    _set_quota_blocked_until(db, reset_at)
                    quota_blocked = True
                    break
                job.privacy_last_error = message[-2000:]
                db.commit()
                errors.append({"id": job.id, "error": message})

        pending = db.scalar(
            select(func.count(Job.id)).where(Job.pending_privacy_status == "public")
        ) or 0

    return {
        "changed": len(changed),
        "ids": changed,
        "errors": errors,
        "quota_blocked": quota_blocked,
        "pending": pending,
    }


def _sync_youtube_statuses() -> dict:
    """Refresh published-video health in batches instead of one API call per video."""
    checked = 0
    errors: list[dict] = []
    with SessionLocal() as db:
        rows = db.execute(
            select(Job.id, Job.channel_id).where(
                Job.youtube_video_id.is_not(None),
                Job.youtube_video_id.not_like("mock_%"),
                Job.youtube_deleted_at.is_(None),
            ).order_by(Job.published_at.desc())
        ).all()

    by_channel: dict[int, list[str]] = {}
    for job_id, channel_id in rows:
        if channel_id is None:
            errors.append({"id": job_id, "error": "El trabajo no tiene un canal válido"})
            continue
        by_channel.setdefault(channel_id, []).append(job_id)

    for channel_id, job_ids in by_channel.items():
        for start in range(0, len(job_ids), 50):
            chunk_ids = job_ids[start:start + 50]
            with SessionLocal() as db:
                jobs = [job for job_id in chunk_ids if (job := db.get(Job, job_id))]
                try:
                    channel = db.get(Channel, channel_id)
                    if not channel:
                        raise YouTubeError("El canal ya no existe")
                    sync_video_statuses(jobs, channel)
                    db.commit()
                    checked += len(jobs)
                except Exception as exc:
                    db.rollback()
                    log.warning(
                        "youtube_status_batch_failed channel_id=%s count=%s error=%s",
                        channel_id, len(chunk_ids), type(exc).__name__,
                    )
                    errors.append({
                        "channel_id": channel_id,
                        "count": len(chunk_ids),
                        "error": str(exc),
                    })
    return {"checked": checked, "errors": errors}


def _refresh_covers() -> dict:
    matched: list[str] = []
    try:
        cover_provider.refresh(force=True)
    except GoogleStorageError as exc:
        log.warning("cover_refresh_failed error=%s", exc)
        return {"matched": 0, "error": str(exc)}
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(Job.status == JobStatus.WAITING_COVER.value).order_by(Job.created_at)).all()
        for job in jobs:
            match = cover_provider.find(job.artist, job.title, job.filename_original)
            if not match:
                continue
            check_path = settings.processing_dir / job.id / "cover_check.png"
            try:
                cover_provider.download(match.entry.cover_url, check_path)
                check_path.unlink(missing_ok=True)
            except ValueError as exc:
                job.error_code = "COVER_INVALID"
                job.error_message = str(exc)
                continue
            job.cover_url = match.entry.cover_url
            job.cover_match_score = match.score
            job.artist = job.artist or match.entry.artist
            job.title = job.title or match.entry.title
            job.status = JobStatus.QUEUED.value
            job.error_code = job.error_message = None
            matched.append(job.id)
        db.commit()
    for job_id in matched:
        enqueue(job_id)
    return {"matched": len(matched), "ids": matched}


app = FastAPI(title="DJGABO YouTube Demo Engine V2", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parents[1] / "static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parents[1] / "static" / "index.html")


@app.get("/q/app-info", include_in_schema=False)
def oauth_app_info():
    return FileResponse(Path(__file__).parents[1] / "static" / "oauth-info.html")


@app.get("/q/privacy", include_in_schema=False)
def oauth_privacy():
    return FileResponse(Path(__file__).parents[1] / "static" / "oauth-privacy.html")


@app.get("/q/terms", include_in_schema=False)
def oauth_terms():
    return FileResponse(Path(__file__).parents[1] / "static" / "oauth-terms.html")


@app.get("/q/{token}", include_in_schema=False)
def qr_redirect(token: str, db: Session = Depends(get_db)):
    redirect = db.scalar(select(QrRedirect).where(QrRedirect.token == token))
    if not redirect:
        raise HTTPException(404, "QR no encontrado")
    job = db.get(Job, redirect.job_id)
    original_name = job.filename_original if job else f"{redirect.artist} - {redirect.title}"
    return RedirectResponse(whatsapp_url(settings.whatsapp_number, original_name), status_code=302)


@app.get("/health")
def health():
    database = "ok"
    redis_state = "ok"
    try:
        with SessionLocal() as db:
            db.execute(select(1))
    except Exception:
        database = "error"
    try:
        queue.connection.ping()
    except Exception:
        redis_state = "error"
    status = "ok" if database == "ok" else "degraded"
    return {"status": status, "database": database, "redis": redis_state,
            "local_fallback": settings.local_sync_fallback, "youtube_mode": settings.youtube_mode}


def _parse_filename(name: str) -> tuple[str | None, str | None]:
    stem = Path(name).stem.strip()
    if " - " not in stem:
        return None, None
    artist, title = (part.strip() for part in stem.split(" - ", 1))
    return (artist or None), (title or None)


def _prepare_job_after_drive(job: Job, db: Session) -> bool:
    """Resolve the cover and leave a Drive-backed job ready for the worker."""
    try:
        match = cover_provider.find(job.artist, job.title, job.filename_original)
    except GoogleStorageError as exc:
        job.status = JobStatus.WAITING_COVER.value
        job.error_code = "COVER_CATALOG_ERROR"
        job.error_message = str(exc)
        db.commit()
        return False
    if not match:
        job.status = JobStatus.WAITING_COVER.value
        job.error_code = "COVER_NOT_FOUND"
        job.error_message = "Esperando un cover válido en 01_CEREBRO2"
        db.commit()
        return False
    check_path = settings.processing_dir / job.id / "cover_check.png"
    try:
        cover_provider.download(match.entry.cover_url, check_path)
        check_path.unlink(missing_ok=True)
    except ValueError as exc:
        job.status = JobStatus.WAITING_COVER.value
        job.error_code = "COVER_INVALID"
        job.error_message = str(exc)
        db.commit()
        return False
    job.cover_url = match.entry.cover_url
    job.cover_match_score = match.score
    job.artist = job.artist or match.entry.artist
    job.title = job.title or match.entry.title
    job.status = JobStatus.QUEUED.value
    job.progress = 0
    job.error_code = job.error_message = None
    db.commit()
    return True


@app.post("/api/jobs/upload", response_model=list[JobOut])
async def upload_jobs(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    created: list[Job] = []
    allowed_ext = {".mp3", ".wav"}
    allowed_mime = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "application/octet-stream"}
    max_bytes = settings.max_upload_file_mb * 1024 * 1024
    used_bytes = sum(path.stat().st_size for path in settings.data_root.rglob("*") if path.is_file())
    if used_bytes >= settings.max_storage_gb * 1024 * 1024 * 1024:
        raise HTTPException(507, "La cuota de almacenamiento temporal está llena")
    for upload in files:
        original_name = Path(upload.filename or "").name
        extension = Path(original_name).suffix.lower()
        if extension not in allowed_ext or (upload.content_type and upload.content_type not in allowed_mime):
            raise HTTPException(415, f"Formato no permitido: {original_name}")
        job_id = str(uuid.uuid4())
        target = settings.incoming_dir / f"{job_id}{extension}"
        digest = hashlib.sha256()
        md5_digest = hashlib.md5()
        size = 0
        try:
            with target.open("wb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(413, f"{original_name} supera {settings.max_upload_file_mb} MB")
                    digest.update(chunk)
                    md5_digest.update(chunk)
                    handle.write(chunk)
            checksum = digest.hexdigest()
            source_md5 = md5_digest.hexdigest()
            existing_job = db.scalar(select(Job).where(Job.sha256 == checksum).order_by(Job.created_at.desc()).limit(1))
            if existing_job and not existing_job.drive_file_id:
                target.unlink(missing_ok=True)
                raise HTTPException(409, f"Este audio ya existe en el panel (trabajo {existing_job.id})")
            duration = probe_duration(target)
            artist, title = _parse_filename(original_name)
            default_cut_seconds = _stored_float(db, "default_cut_seconds", settings.default_cut_seconds)
            valid_cut = 0 < default_cut_seconds < duration
            job = Job(id=job_id, filename_original=original_name, artist=artist, title=title, sha256=checksum,
                      source_md5=source_md5, source_size_bytes=size,
                      original_path=str(target), original_duration_seconds=duration,
                      cut_seconds=default_cut_seconds, privacy_status="public",
                      drive_folder_id=settings.drive_audio_folder_id,
                      status=JobStatus.UPLOADING_TO_DRIVE.value, progress=90)
            if not valid_cut:
                job.status = JobStatus.RENDER_ERROR.value
                job.error_code = "CUT_OUT_OF_RANGE"
                job.error_message = f"El corte debe ser menor que {duration:.2f}s"
            db.add(job)
            db.commit()
            if valid_cut:
                try:
                    storage = DriveAudioStorage()
                    duplicate_file = None
                    if existing_job and existing_job.drive_file_id and not existing_job.drive_deleted_at:
                        duplicate_file = {"id": existing_job.drive_file_id,
                                          "name": existing_job.drive_file_name or existing_job.filename_original}
                    if not duplicate_file:
                        duplicate_file = storage.find_duplicate(checksum, source_md5, size)
                    if duplicate_file:
                        job.status = JobStatus.DRIVE_DUPLICATE_CONFIRMATION.value
                        job.progress = 100
                        job.duplicate_drive_file_id = duplicate_file["id"]
                        job.duplicate_drive_file_name = duplicate_file.get("name", original_name)
                        job.error_code = "DRIVE_DUPLICATE_FOUND"
                        job.error_message = "Este audio ya existe en Google Drive. ¿Deseas utilizar el archivo existente?"
                        db.commit()
                        db.refresh(job)
                        created.append(job)
                        continue
                    drive_file = storage.upload(target, original_name, sha256=checksum)
                    job.drive_file_id = drive_file["id"]
                    job.drive_file_name = drive_file.get("name", original_name)
                    db.commit()
                    target.unlink(missing_ok=True)
                    job.original_path = None
                except GoogleStorageError as exc:
                    job.status = JobStatus.UPLOAD_ERROR.value
                    job.error_code = "DRIVE_UPLOAD_ERROR"
                    job.error_message = str(exc)
                else:
                    _prepare_job_after_drive(job, db)
                db.commit()
            db.refresh(job)
            created.append(job)
            if job.status == JobStatus.QUEUED.value:
                enqueue(job.id)
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        except MediaError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(422, f"Audio ilegible {original_name}: {exc}") from exc
        finally:
            await upload.close()
    return created


@app.post("/api/jobs/{job_id}/use-drive-source", response_model=JobOut)
def use_existing_drive_source(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado")
    if job.status != JobStatus.DRIVE_DUPLICATE_CONFIRMATION.value or not job.duplicate_drive_file_id:
        raise HTTPException(409, "Este trabajo no tiene un duplicado de Drive pendiente")
    storage = DriveAudioStorage()
    try:
        metadata = storage.get_metadata(job.duplicate_drive_file_id)
    except GoogleStorageError as exc:
        raise HTTPException(409, str(exc)) from exc
    if metadata.get("trashed"):
        raise HTTPException(409, "El audio existente fue enviado a la papelera de Drive")
    if job.source_size_bytes and metadata.get("size") and int(metadata["size"]) != job.source_size_bytes:
        raise HTTPException(409, "El archivo de Drive cambió y ya no coincide con el audio subido")
    if job.source_md5 and metadata.get("md5Checksum") and metadata["md5Checksum"] != job.source_md5:
        raise HTTPException(409, "El contenido del archivo de Drive ya no coincide")
    job.drive_file_id = metadata["id"]
    job.drive_file_name = metadata.get("name", job.filename_original)
    job.drive_reused = True
    job.duplicate_drive_file_id = None
    job.duplicate_drive_file_name = None
    if job.original_path:
        Path(job.original_path).unlink(missing_ok=True)
    job.original_path = None
    job.error_code = job.error_message = None
    should_enqueue = _prepare_job_after_drive(job, db)
    db.refresh(job)
    if should_enqueue:
        enqueue(job.id)
    return job


@app.get("/api/jobs", response_model=list[JobOut])
def list_jobs(q: str | None = None, status: str | None = None, history: bool = False, db: Session = Depends(get_db)):
    stmt = select(Job)
    if history:
        stmt = stmt.where(Job.youtube_video_id.is_not(None))
    if status:
        stmt = stmt.where(Job.status == status)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.join(Channel, Job.channel_id == Channel.id, isouter=True).where(or_(
            Job.artist.ilike(pattern), Job.title.ilike(pattern), Job.filename_original.ilike(pattern),
            Job.status.ilike(pattern), Channel.display_name.ilike(pattern),
        ))
    return db.scalars(stmt.order_by(Job.created_at.desc())).all()


@app.get("/api/jobs/{job_id}/video", include_in_schema=False)
def local_video(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or not job.rendered_path:
        raise HTTPException(404, "Video local no disponible")
    path = Path(job.rendered_path)
    if not path.is_file():
        raise HTTPException(404, "El MP4 local ya no existe")
    return FileResponse(path, media_type="video/mp4", headers={"Content-Disposition": "inline"})


@app.get("/api/history", response_model=list[JobOut])
def history(db: Session = Depends(get_db)):
    return db.scalars(select(Job).where(Job.youtube_video_id.is_not(None)).order_by(Job.published_at.desc())).all()


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado")
    return job


@app.patch("/api/jobs/{job_id}", response_model=JobOut)
def update_job(job_id: str, patch: JobUpdate, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado")
    data = patch.model_dump(exclude_unset=True)
    if "privacy_status" in data and data["privacy_status"] not in {"public", "unlisted", "private"}:
        raise HTTPException(422, "Privacidad inválida")
    if job.youtube_deleted_at and "privacy_status" in data:
        raise HTTPException(409, "El video ya fue eliminado de YouTube")
    if job.status == JobStatus.PUBLISHED.value and "privacy_status" in data:
        requested_privacy = data.pop("privacy_status")
        try:
            actual_privacy = change_privacy(job, job.channel, requested_privacy)
        except YouTubeError as exc:
            message = str(exc)
            if is_quota_exceeded(message):
                _queue_privacy_change(job, requested_privacy, message)
                db.commit()
                db.refresh(job)
                return job
            raise HTTPException(502, f"No se pudo cambiar la privacidad en YouTube: {exc}") from exc
        if actual_privacy != requested_privacy:
            raise HTTPException(
                502,
                f"YouTube respondió con privacidad {actual_privacy or 'desconocida'} en vez de {requested_privacy}",
            )
    for key, value in data.items():
        setattr(job, key, value)
    if job.status in {JobStatus.PREPARED.value, JobStatus.REVIEW_REQUIRED.value}:
        if job.artist and job.title and 0 < job.cut_seconds < job.original_duration_seconds:
            job.status = JobStatus.PREPARED.value
            job.error_code = job.error_message = None
        else:
            job.status = JobStatus.REVIEW_REQUIRED.value
    db.commit()
    db.refresh(job)
    return job


@app.post("/api/youtube/privacy/bulk")
def bulk_youtube_privacy(body: BulkPrivacyUpdate, db: Session = Depends(get_db)):
    if body.privacy_status not in {"public", "unlisted", "private"}:
        raise HTTPException(422, "Privacidad inválida")
    stmt = select(Job).where(
        Job.youtube_video_id.is_not(None),
        Job.youtube_video_id.not_like("mock_%"),
        Job.youtube_deleted_at.is_(None),
    ).order_by(Job.published_at)
    if body.ids is not None:
        stmt = stmt.where(Job.id.in_(body.ids))
    jobs = db.scalars(stmt).all()
    changed: list[str] = []
    queued: list[str] = []
    failures: list[dict] = []
    quota_blocked = False
    for job in jobs:
        title = job.youtube_title or f"{job.artist} - {job.title}"
        if quota_blocked:
            _queue_privacy_change(job, body.privacy_status, "Esperando que se restablezca la cuota de YouTube")
            queued.append(job.id)
            continue
        try:
            actual_privacy = change_privacy(job, job.channel, body.privacy_status)
            if actual_privacy != body.privacy_status:
                failures.append({
                    "id": job.id,
                    "title": title,
                    "error": f"YouTube respondió con privacidad {actual_privacy or 'desconocida'}",
                })
            else:
                changed.append(job.id)
        except YouTubeError as exc:
            message = str(exc)
            if is_quota_exceeded(message):
                _queue_privacy_change(job, body.privacy_status, message)
                queued.append(job.id)
                quota_blocked = True
            else:
                failures.append({"id": job.id, "title": title, "error": message})
        except Exception as exc:
            failures.append({"id": job.id, "title": title, "error": str(exc)})
    db.commit()
    return {
        "requested": len(jobs),
        "changed": len(changed),
        "ids": changed,
        "queued": len(queued),
        "queued_ids": queued,
        "failures": failures,
        "quota_blocked": quota_blocked,
    }


@app.post("/api/youtube/status/sync")
def refresh_youtube_statuses():
    return _sync_youtube_statuses()


@app.post("/api/jobs/{job_id}/youtube-alert/acknowledge")
def acknowledge_youtube_alert(job_id: str, db: Session = Depends(get_db)):
    job = _action_job(db, job_id)
    if not (job.youtube_restriction_status or job.youtube_rejection_reason or job.youtube_failure_reason or job.youtube_deleted_at):
        raise HTTPException(409, "Este video no tiene una alerta de YouTube")
    job.youtube_attention_acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    return {"acknowledged": True}


class StartRequest(JobUpdate):
    ids: list[str] | None = None


@app.post("/api/jobs/start")
def start_jobs(body: StartRequest, db: Session = Depends(get_db)):
    stmt = select(Job).where(Job.status == JobStatus.PREPARED.value)
    if body.ids:
        stmt = stmt.where(Job.id.in_(body.ids))
    jobs = db.scalars(stmt).all()
    for job in jobs:
        job.status = JobStatus.QUEUED.value
        job.progress = 0
    db.commit()
    queued = [job.id for job in jobs if enqueue(job.id)]
    return {"accepted": len(jobs), "enqueued": len(queued), "ids": [job.id for job in jobs]}


def _action_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado")
    return job


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str, db: Session = Depends(get_db)):
    job = _action_job(db, job_id)
    if job.status in {JobStatus.UPLOADING_YOUTUBE.value, JobStatus.VERIFYING.value, JobStatus.PUBLISHED.value}:
        raise HTTPException(409, "No se puede pausar en este estado")
    job.previous_status = job.status
    job.status = JobStatus.PAUSED.value
    db.commit()
    return {"status": job.status}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str, db: Session = Depends(get_db)):
    job = _action_job(db, job_id)
    if job.status != JobStatus.PAUSED.value:
        raise HTTPException(409, "El trabajo no está pausado")
    job.status = JobStatus.QUEUED.value
    db.commit()
    enqueue(job.id)
    return {"status": job.status}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, db: Session = Depends(get_db)):
    job = _action_job(db, job_id)
    if job.status not in {JobStatus.RENDER_ERROR.value, JobStatus.UPLOAD_ERROR.value, JobStatus.REVIEW_REQUIRED.value}:
        raise HTTPException(409, "Este estado no admite reintento")
    if not job.artist or not job.title or not 0 < job.cut_seconds < job.original_duration_seconds:
        raise HTTPException(409, "Corrige metadatos y corte antes de reintentar")
    job.status = JobStatus.QUEUED.value
    job.error_code = job.error_message = None
    db.commit()
    enqueue(job.id)
    return {"status": job.status}


@app.delete("/api/jobs/{job_id}")
def remove_job(job_id: str, confirm: str | None = Query(None), db: Session = Depends(get_db)):
    job = _action_job(db, job_id)
    if job.status in {JobStatus.UPLOADING_YOUTUBE.value, JobStatus.VERIFYING.value}:
        raise HTTPException(409, "No se puede borrar durante una publicación")
    if job.youtube_video_id:
        if job.youtube_video_id.startswith("mock_"):
            publication = db.scalar(select(ChannelPublication).where(ChannelPublication.job_id == job.id))
            if publication:
                db.delete(publication)
            redirect = db.scalar(select(QrRedirect).where(QrRedirect.job_id == job.id))
            if redirect:
                db.delete(redirect)
            db.delete(job)
            db.commit()
            return {"removed_demo_from_panel": True}
        if job.youtube_deleted_at:
            publication = db.scalar(select(ChannelPublication).where(ChannelPublication.job_id == job.id))
            if publication:
                db.delete(publication)
            redirect = db.scalar(select(QrRedirect).where(QrRedirect.job_id == job.id))
            if redirect:
                db.delete(redirect)
            db.delete(job)
            db.commit()
            return {"removed_from_panel": True}
        if confirm != "DELETE":
            raise HTTPException(409, "Confirma con ?confirm=DELETE para borrar también en YouTube")
        delete_video(job, job.channel)
        publication = db.scalar(select(ChannelPublication).where(ChannelPublication.job_id == job.id))
        if publication:
            publication.deleted_at = datetime.now(timezone.utc)
        job.youtube_deleted_at = datetime.now(timezone.utc)
        job.error_code = "YOUTUBE_DELETED"
        job.error_message = "Video eliminado a solicitud del usuario"
        db.commit()
        return {"deleted_from_youtube": True}
    for value in (job.original_path, job.rendered_path):
        if value:
            Path(value).unlink(missing_ok=True)
    if job.drive_file_id and not job.drive_deleted_at and not job.drive_reused:
        other_references = db.scalar(select(func.count(Job.id)).where(
            Job.drive_file_id == job.drive_file_id,
            Job.id != job.id,
            Job.drive_deleted_at.is_(None),
        )) or 0
        if not other_references:
            try:
                DriveAudioStorage().delete(job.drive_file_id)
            except GoogleStorageError as exc:
                raise HTTPException(502, str(exc)) from exc
    redirect = db.scalar(select(QrRedirect).where(QrRedirect.job_id == job.id))
    if redirect:
        db.delete(redirect)
    db.delete(job)
    db.commit()
    return {"deleted": True}


@app.get("/api/channels")
def list_channels(db: Session = Depends(get_db)):
    rows = []
    for slot in channel_slots(db):
        channel = slot.channel
        template = Path(channel.background_image_path) if channel.background_image_path else None
        has_template = bool(template and template.is_file())
        template_url = (
            f"/api/channels/{channel.id}/background/image?v={template.stat().st_mtime_ns}"
            if has_template else None
        )
        rows.append({
            "id": channel.id,
            "display_name": channel.display_name,
            "enabled": channel.enabled,
            "oauth_status": channel.oauth_status,
            "background_image_path": channel.background_image_path,
            "background_name": template.name if has_template else None,
            "background_url": template_url,
            "template_name": template.name if has_template else None,
            "template_url": template_url,
            "max_uploads_24h": channel.max_uploads_24h,
            "used_24h": slot.used,
            "youtube_description": channel.youtube_description,
            "next_slot": slot.next_slot.isoformat() if slot.next_slot else None,
        })
    return rows


@app.patch("/api/channels/{channel_id}")
def update_channel(channel_id: int, patch: ChannelUpdate, db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")
    for key, value in patch.model_dump(exclude_unset=True).items():
        setattr(channel, key, value)
    db.commit()
    return {"ok": True}


def _save_asset(upload: UploadFile, prefix: str, extensions: set[str]) -> Path:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in extensions:
        raise HTTPException(415, "Tipo de archivo no permitido")
    target = settings.assets_dir / f"{prefix}{extension}"
    with target.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    return target


def _save_background_asset(upload: UploadFile, prefix: str) -> Path:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(415, "La plantilla debe ser PNG, JPG o WEBP")
    try:
        with Image.open(upload.file) as image:
            image.load()
            if image.size != (1280, 720):
                raise HTTPException(
                    422,
                    f"La plantilla debe medir exactamente 1280×720 px; mide {image.width}×{image.height} px",
                )
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(422, "El archivo no es una imagen válida") from exc
    finally:
        upload.file.seek(0)
    target = settings.assets_dir / f"{prefix}{extension}"
    with target.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    return target


@app.post("/api/settings/commercial-audio")
def commercial_audio(file: UploadFile = File(...), db: Session = Depends(get_db)):
    target = _save_asset(file, "commercial", {".mp3", ".wav"})
    try:
        duration = probe_duration(target)
    except MediaError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    db.merge(Setting(key="commercial_audio_path", value=str(target)))
    db.commit()
    return {"filename": file.filename, "duration": duration}


@app.post("/api/settings/background")
def fallback_background(file: UploadFile = File(...), db: Session = Depends(get_db)):
    target = _save_background_asset(file, "general_background")
    db.merge(Setting(key="general_background_path", value=str(target)))
    db.commit()
    return {"filename": file.filename}


@app.post("/api/channels/{channel_id}/background")
def channel_background(channel_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")
    target = _save_background_asset(file, f"channel_{channel_id}_template")
    channel.background_image_path = str(target)
    channel.qr_background_image_path = None
    db.commit()
    return {
        "filename": file.filename,
        "channel_id": channel.id,
        "channel": channel.display_name,
        "template_url": f"/api/channels/{channel.id}/background/image?v={target.stat().st_mtime_ns}",
    }


@app.post("/api/channels/{channel_id}/qr-background")
def channel_qr_background(channel_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")
    target = _save_background_asset(file, f"channel_{channel_id}_qr")
    channel.qr_background_image_path = str(target)
    db.commit()
    return {"filename": file.filename, "channel_id": channel.id, "channel": channel.display_name}


@app.get("/api/settings/background/image", include_in_schema=False)
def fallback_background_image(db: Session = Depends(get_db)):
    row = db.get(Setting, "general_background_path") or db.get(Setting, "fallback_background_path")
    if not row or not Path(row.value).is_file():
        raise HTTPException(404, "Fondo general no configurado")
    return FileResponse(Path(row.value), headers={"Cache-Control": "no-store"})


@app.get("/api/channels/{channel_id}/background/image", include_in_schema=False)
def channel_background_image(channel_id: int, db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if not channel or not channel.background_image_path or not Path(channel.background_image_path).is_file():
        raise HTTPException(404, "Fondo propio no configurado")
    return FileResponse(Path(channel.background_image_path), headers={"Cache-Control": "no-store"})


@app.get("/api/channels/{channel_id}/qr-background/image", include_in_schema=False)
def channel_qr_background_image(channel_id: int, db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if not channel or not channel.qr_background_image_path or not Path(channel.qr_background_image_path).is_file():
        raise HTTPException(404, "Fondo QR propio no configurado")
    return FileResponse(Path(channel.qr_background_image_path), headers={"Cache-Control": "no-store"})


ANIMATION_PREVIEW_TRANSITIONS = {
    1: ("diagtr", "Diagonal"),
    2: ("smoothleft", "Barrido suave"),
    3: ("circleopen", "Círculo"),
    4: ("dissolve", "Disolución"),
}


def _render_animation_preview(first_frame: Path, qr_frame: Path, output: Path, transition: str) -> None:
    """Render a 20 s real FFmpeg xfade preview from two still-image scenes."""
    first_clip = output.with_name(f"{output.stem}_first_clip.mp4")
    qr_clip = output.with_name(f"{output.stem}_qr_clip.mp4")

    def encode_still(frame: Path, clip: Path) -> None:
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", "25", "-loop", "1", "-i", str(frame),
            "-t", "5.5", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-r", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(clip),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=90)

    filter_complex = (
        f"[0:v][1:v]xfade=transition={transition}:duration=0.5:offset=5.0[x1];"
        f"[x1][2:v]xfade=transition={transition}:duration=0.5:offset=10.0[x2];"
        f"[x2][3:v]xfade=transition={transition}:duration=0.5:offset=15.0[vout]"
    )

    try:
        encode_still(first_frame, first_clip)
        encode_still(qr_frame, qr_clip)
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(first_clip), "-i", str(qr_clip),
            "-i", str(first_clip), "-i", str(qr_clip),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-an", "-t", "20",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-r", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(output),
        ], check=True, capture_output=True, text=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(detail[-2000:]) from exc
    finally:
        first_clip.unlink(missing_ok=True)
        qr_clip.unlink(missing_ok=True)

@app.post("/api/channels/{channel_id}/animation-preview")
def channel_animation_preview(channel_id: int, db: Session = Depends(get_db)):
    """Render the exact single-template production frame without publishing."""
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")

    template = Path(channel.background_image_path) if channel.background_image_path else None
    if not template or not template.is_file():
        raise HTTPException(409, "Configura la plantilla 1280×720 del canal antes de probar")

    job = db.scalar(
        select(Job)
        .where(Job.channel_id == channel_id, Job.cover_url.is_not(None))
        .order_by(Job.updated_at.desc())
        .limit(1)
    )
    if not job or not job.cover_url:
        raise HTTPException(409, "No hay una canción reciente con cover para crear la prueba")

    preview_dir = settings.assets_dir / "animation_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    cover_path = preview_dir / f"channel_{channel_id}_cover.png"
    frame_path = preview_dir / f"channel_{channel_id}_single.png"

    try:
        cover_provider.download(job.cover_url, cover_path)
        redirect = db.scalar(select(QrRedirect).where(QrRedirect.job_id == job.id))
        if not redirect:
            token = uuid.uuid4().hex
            redirect = QrRedirect(
                token=token,
                job_id=job.id,
                artist=job.artist or "",
                title=job.title or "",
            )
            job.qr_token = token
            db.add(redirect)
            db.commit()
        qr_target = f"{settings.public_base_url.rstrip('/')}/q/{redirect.token}"
        create_frame(
            template,
            cover_path,
            job.artist or "",
            job.title or "",
            qr_target,
            frame_path,
            settings.whatsapp_number,
            include_qr=True,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, f"No se pudo crear la prueba: {exc}") from exc

    stamp = frame_path.stat().st_mtime_ns
    return {
        "channel": channel.display_name,
        "artist": job.artist,
        "title": job.title,
        "image_url": f"/api/channels/{channel_id}/animation-preview/frame?v={stamp}",
        # Aliases temporales para clientes del panel que aún tengan JS en caché.
        "first_url": f"/api/channels/{channel_id}/animation-preview/frame?v={stamp}",
        "qr_url": f"/api/channels/{channel_id}/animation-preview/frame?v={stamp}",
        "qr_target": qr_target,
        "cover_box": {"x": 142, "y": 189, "width": 381, "height": 381, "border": 8},
        "qr_box": {"x": 1066, "y": 419, "width": 138, "height": 138},
        "single_template": True,
    }


@app.get("/api/channels/{channel_id}/animation-preview/{scene}", include_in_schema=False)
def channel_animation_preview_asset(channel_id: int, scene: str):
    preview_dir = settings.assets_dir / "animation_previews"
    if scene in {"frame", "first", "qr"}:
        path = preview_dir / f"channel_{channel_id}_single.png"
    else:
        raise HTTPException(404, "Escena no encontrada")
    if not path.is_file():
        raise HTTPException(404, "Primero genera la prueba de cover + QR")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.delete("/api/channels/{channel_id}/background")
def clear_channel_background(channel_id: int, db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")
    channel.background_image_path = None
    db.commit()
    return {"ok": True, "channel": channel.display_name, "uses_general_background": True}


@app.delete("/api/channels/{channel_id}/qr-background")
def clear_channel_qr_background(channel_id: int, db: Session = Depends(get_db)):
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")
    channel.qr_background_image_path = None
    db.commit()
    return {"ok": True, "channel": channel.display_name}


@app.patch("/api/settings/audio-protection")
def update_audio_protection(patch: AudioProtectionUpdate, db: Session = Depends(get_db)):
    db.merge(Setting(key="default_cut_seconds", value=str(patch.default_cut_seconds)))
    db.merge(Setting(key="audio_crossfade_seconds", value=str(patch.transition_seconds)))
    db.commit()
    return {
        "default_cut_seconds": patch.default_cut_seconds,
        "transition_seconds": patch.transition_seconds,
    }


@app.get("/api/settings")
def get_public_settings(db: Session = Depends(get_db)):
    commercial = db.get(Setting, "commercial_audio_path")
    fallback = db.get(Setting, "general_background_path") or db.get(Setting, "fallback_background_path")
    fallback_path = Path(fallback.value) if fallback else None
    fallback_configured = bool(fallback_path and fallback_path.is_file())
    return {
        "default_cut_seconds": _stored_float(db, "default_cut_seconds", settings.default_cut_seconds),
        "transition_seconds": _stored_float(db, "audio_crossfade_seconds", settings.audio_crossfade_seconds),
        "single_template_mode": True,
        "youtube_mode": settings.youtube_mode,
        "google_mode": settings.google_mode,
        "public_base_url": settings.public_base_url,
        "whatsapp_number": settings.whatsapp_number,
        "commercial_audio_configured": bool(commercial and Path(commercial.value).is_file()),
        "commercial_audio_name": Path(commercial.value).name if commercial else None,
        "fallback_background_configured": fallback_configured,
        "fallback_background_name": Path(fallback.value).name if fallback else None,
        "fallback_background_url": f"/api/settings/background/image?v={fallback_path.stat().st_mtime_ns}" if fallback_configured else None,
        "next_global_slot": (global_next_slot(db).isoformat() if global_next_slot(db) else None),
    }


@app.post("/api/covers/refresh")
def refresh_covers_now():
    return _refresh_covers()


@app.post("/api/channels/{channel_id}/oauth/start")
def oauth_start(channel_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        client_config = google_client_config(
            f"{settings.oauth_redirect_base_url}/api/channels/{channel_id}/oauth/callback"
        )
    except YouTubeOAuthConfigError as exc:
        raise HTTPException(409, str(exc)) from exc
    channel = db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")
    redirect_uri = f"{settings.oauth_redirect_base_url}/api/channels/{channel_id}/oauth/callback"
    flow = Flow.from_client_config(client_config, scopes=["https://www.googleapis.com/auth/youtube"], redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    db.merge(Setting(key=f"oauth_state_{channel_id}", value=state))
    db.commit()
    return {"authorization_url": url}


@app.get("/api/channels/{channel_id}/oauth/callback")
def oauth_callback(channel_id: int, request: Request, state: str, db: Session = Depends(get_db)):
    expected = db.get(Setting, f"oauth_state_{channel_id}")
    channel = db.get(Channel, channel_id)
    if not expected or expected.value != state or not channel:
        raise HTTPException(400, "Estado OAuth inválido")
    redirect_uri = f"{settings.oauth_redirect_base_url}/api/channels/{channel_id}/oauth/callback"
    try:
        client_config = google_client_config(redirect_uri)
    except YouTubeOAuthConfigError as exc:
        raise HTTPException(409, str(exc)) from exc
    flow = Flow.from_client_config(client_config, scopes=["https://www.googleapis.com/auth/youtube"], state=state, redirect_uri=redirect_uri)
    parsed_redirect = urlparse(redirect_uri)
    local_http = parsed_redirect.scheme == "http" and parsed_redirect.hostname in {"localhost", "127.0.0.1", "::1"}
    previous_insecure = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
    if local_http:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    try:
        flow.fetch_token(authorization_response=str(request.url))
    finally:
        if local_http:
            if previous_insecure is None:
                os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
            else:
                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = previous_insecure
    credentials = flow.credentials
    channel.token_reference = encrypt_token({"token": credentials.token, "refresh_token": credentials.refresh_token})
    channel.oauth_status = "CONNECTED"
    db.delete(expected)
    db.commit()
    return {"connected": True, "channel": channel.display_name}
