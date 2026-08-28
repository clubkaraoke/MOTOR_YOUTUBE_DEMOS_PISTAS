from datetime import datetime, timedelta, timezone

from app.models.entities import Channel, Job, JobStatus
from app.services.cleanup import cleanup_job_files
from app.services.youtube import finalize_publication, upload_video


def job_with_files(tmp_path, status):
    original = tmp_path / "original.mp3"; original.write_bytes(b"source")
    rendered = tmp_path / "video.mp4"; rendered.write_bytes(b"render")
    job = Job(filename_original="A - T.mp3", artist="A", title="T", sha256="a"*64,
              original_path=str(original), rendered_path=str(rendered), original_duration_seconds=180,
              cut_seconds=80, status=status)
    return job, original, rendered


def test_cleanup_never_deletes_before_published(tmp_path):
    job, original, rendered = job_with_files(tmp_path, JobStatus.UPLOAD_ERROR.value)
    job.cleanup_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert cleanup_job_files(job) is False
    assert original.exists() and rendered.exists()


def test_cleanup_after_retention_keeps_database_record(tmp_path, db, monkeypatch):
    from app.services import youtube
    monkeypatch.setattr(youtube.get_settings(), "youtube_mode", "mock")
    monkeypatch.setattr(youtube.get_settings(), "success_retention_minutes", 0)
    channel = Channel(display_name="C1"); db.add(channel)
    job, original, rendered = job_with_files(tmp_path, JobStatus.MP4_READY.value)
    job.channel = channel; db.add(job); db.commit()
    video_id, url = upload_video(job, channel)
    finalize_publication(db, job, channel, video_id, url)
    assert job.status == JobStatus.PUBLISHED.value and job.youtube_url
    assert job.cleanup_at is None
    job.cleanup_at = datetime.now(timezone.utc)
    assert cleanup_job_files(job, datetime.now(timezone.utc)+timedelta(seconds=1)) is True
    assert not original.exists() and not rendered.exists()
    assert db.get(Job, job.id).youtube_url == url


def test_mock_upload_is_idempotent(tmp_path, db, monkeypatch):
    from app.services import youtube
    monkeypatch.setattr(youtube.get_settings(), "youtube_mode", "mock")
    channel = Channel(display_name="C1"); db.add(channel)
    job, _, _ = job_with_files(tmp_path, JobStatus.MP4_READY.value); job.channel = channel; db.add(job); db.commit()
    first = upload_video(job, channel); job.youtube_video_id, job.youtube_url = first
    second = upload_video(job, channel)
    assert first == second
    assert f"/api/jobs/{job.id}/video" in first[1]


def test_mock_youtube_never_deletes_real_drive_master(tmp_path, db, monkeypatch):
    from app.services import cleanup
    channel = Channel(display_name="C1"); db.add(channel)
    job, _, _ = job_with_files(tmp_path, JobStatus.PUBLISHED.value)
    job.channel = channel
    job.youtube_video_id = "mock_video"
    job.drive_file_id = "real_drive_id"
    job.cleanup_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(job); db.commit()
    deleted = []
    monkeypatch.setattr(cleanup.get_settings(), "youtube_mode", "mock")
    monkeypatch.setattr(cleanup.DriveAudioStorage, "delete", lambda self, file_id: deleted.append(file_id))
    cleanup.cleanup_due(db)
    assert deleted == []
    assert job.drive_deleted_at is None
