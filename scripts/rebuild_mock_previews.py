"""Rebuild existing local preview MP4s after a visual/QR template change."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filelock import FileLock
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import Job, JobStatus, Setting
from app.services.frame_builder import create_frame
from app.services.media import create_demo_video, validate_demo_video
from app.services.qr import whatsapp_url


def rebuild(job_ids: set[str] | None = None) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        commercial_row = db.get(Setting, "commercial_audio_path")
        general_row = db.get(Setting, "general_background_path") or db.get(Setting, "fallback_background_path")
        if not commercial_row or not Path(commercial_row.value).is_file():
            raise RuntimeError("Falta el audio comercial")
        statement = select(Job).where(
                Job.status == JobStatus.PUBLISHED.value,
                Job.qr_token.is_not(None),
                Job.cover_path.is_not(None),
            )
        if job_ids:
            statement = statement.where(Job.id.in_(job_ids))
        jobs = db.scalars(statement.order_by(Job.created_at)).all()
        for index, job in enumerate(jobs, start=1):
            source = Path(job.original_path or "")
            cover = Path(job.cover_path or "")
            background_value = job.channel.background_image_path if job.channel and job.channel.background_image_path else None
            background = Path(background_value or (general_row.value if general_row else ""))
            if not all(path.is_file() for path in (source, cover, background)):
                print(f"SKIP {job.filename_original}: faltan archivos locales")
                continue
            frame = settings.processing_dir / job.id / "frame_final.png"
            output = settings.ready_dir / f"{job.id}.mp4"
            print(f"[{index}/{len(jobs)}] {job.filename_original}", flush=True)
            with FileLock(str(settings.processing_dir / "ffmpeg.lock"), timeout=12 * 60 * 60):
                create_frame(
                    background,
                    cover,
                    job.artist or "",
                    job.title or Path(job.filename_original).stem,
                    whatsapp_url(settings.whatsapp_number, job.filename_original),
                    frame,
                    settings.whatsapp_number,
                )
                create_demo_video(
                    source,
                    frame,
                    commercial_row.value,
                    job.cut_seconds,
                    output,
                    settings.audio_crossfade_seconds,
                )
                validate_demo_video(output, job.original_duration_seconds)
            job.frame_path = str(frame)
            job.rendered_path = str(output)
            db.commit()
            print("OK", flush=True)


if __name__ == "__main__":
    rebuild(set(sys.argv[1:]) or None)
