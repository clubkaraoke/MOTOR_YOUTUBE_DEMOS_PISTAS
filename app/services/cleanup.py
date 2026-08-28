import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Job, JobStatus
from app.services.google_drive import DriveAudioStorage, GoogleStorageError


def _safe_remove_workdir(job: Job) -> None:
    root = get_settings().processing_dir.resolve()
    workdir = (root / job.id).resolve()
    try:
        workdir.relative_to(root)
    except ValueError:
        return
    if workdir.is_dir():
        shutil.rmtree(workdir)


def cleanup_job_files(job: Job, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if job.status not in {JobStatus.PUBLISHED.value, JobStatus.CLEANUP.value} or not job.cleanup_at:
        return False
    cleanup_at = job.cleanup_at if job.cleanup_at.tzinfo else job.cleanup_at.replace(tzinfo=timezone.utc)
    if cleanup_at > now or not job.youtube_video_id:
        return False
    for value in (job.original_path, job.rendered_path, job.cover_path, job.frame_path):
        if value:
            path = Path(value)
            if path.is_file():
                path.unlink()
    _safe_remove_workdir(job)
    return True


def cleanup_due(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    if settings.youtube_mode != "real":
        return 0
    jobs = db.scalars(select(Job).where(Job.cleanup_at.is_not(None))).all()
    count = 0
    storage = DriveAudioStorage()
    for job in jobs:
        cleanup_at = job.cleanup_at if job.cleanup_at and job.cleanup_at.tzinfo else (
            job.cleanup_at.replace(tzinfo=timezone.utc) if job.cleanup_at else None)
        if not cleanup_at or cleanup_at > now or not job.youtube_video_id:
            continue
        job.status = JobStatus.CLEANUP.value
        db.commit()
        try:
            # A simulated YouTube id is not proof of a real publication. Keep
            # the real Drive master until YouTube itself is operating in real mode.
            if settings.youtube_mode == "real" and job.drive_file_id and not job.drive_deleted_at:
                other_references = db.scalar(select(func.count(Job.id)).where(
                    Job.drive_file_id == job.drive_file_id,
                    Job.id != job.id,
                    Job.drive_deleted_at.is_(None),
                )) or 0
                # Each completed job releases its reference. The physical Drive
                # file is removed only when no other live job still shares it.
                if not other_references:
                    storage.delete(job.drive_file_id)
                job.drive_deleted_at = now
            if cleanup_job_files(job, now):
                count += 1
            job.status = JobStatus.PUBLISHED.value
            job.error_code = job.error_message = None
        except GoogleStorageError as exc:
            job.status = JobStatus.CLEANUP.value
            job.error_code = "DRIVE_CLEANUP_ERROR"
            job.error_message = str(exc)
        db.commit()
    failed_before = now - timedelta(days=settings.failed_retention_days)
    failed = db.scalars(select(Job).where(Job.status.in_([
        JobStatus.RENDER_ERROR.value, JobStatus.UPLOAD_ERROR.value,
    ])).where(Job.updated_at < failed_before)).all()
    for job in failed:
        for value in (job.original_path, job.rendered_path, job.cover_path, job.frame_path):
            if value:
                path = Path(value)
                if path.is_file():
                    path.unlink()
                    count += 1
        _safe_remove_workdir(job)
    db.commit()
    return count
