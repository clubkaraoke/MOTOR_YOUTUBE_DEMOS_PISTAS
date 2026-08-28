"""Regenera previews MOCK desde los audios maestros que continúan en Drive."""

import sys

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.entities import ChannelPublication, Job, JobStatus, QrRedirect


def main(job_ids: list[str]) -> None:
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(Job.id.in_(job_ids))).all()
        for job in jobs:
            if not job.drive_file_id:
                continue
            job.status = JobStatus.QUEUED.value
            job.youtube_video_id = None
            job.youtube_url = None
            job.youtube_deleted_at = None
            job.published_at = None
            job.cleanup_at = None
            job.rendered_path = None
            job.original_path = None
            job.progress = 0
            job.error_code = job.error_message = None
            redirect = db.scalar(select(QrRedirect).where(QrRedirect.job_id == job.id))
            if redirect:
                redirect.youtube_video_id = None
                redirect.youtube_url = None
            publication = db.scalar(select(ChannelPublication).where(ChannelPublication.job_id == job.id))
            if publication:
                publication.youtube_video_id = f"preview_pending:{job.id}"
        db.commit()
        print(f"requeued={len(jobs)}")


if __name__ == "__main__":
    main(sys.argv[1:])
