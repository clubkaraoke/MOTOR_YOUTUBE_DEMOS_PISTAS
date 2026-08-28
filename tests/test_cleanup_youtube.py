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


def test_mock_upload_reports_progress(tmp_path, db, monkeypatch):
    from app.services import youtube
    monkeypatch.setattr(youtube.get_settings(), "youtube_mode", "mock")
    channel = Channel(display_name="C1"); db.add(channel)
    job, _, _ = job_with_files(tmp_path, JobStatus.MP4_READY.value); job.channel = channel; db.add(job); db.commit()
    progress = []
    upload_video(job, channel, progress_callback=progress.append)
    assert progress == [100]


def test_real_upload_reports_chunk_progress(tmp_path, db, monkeypatch):
    from app.services import youtube
    monkeypatch.setattr(youtube.get_settings(), "youtube_mode", "real")
    channel = Channel(display_name="C1"); db.add(channel)
    job, _, rendered = job_with_files(tmp_path, JobStatus.MP4_READY.value); job.channel = channel; db.add(job); db.commit()

    class Result:
        def __init__(self, payload):
            self.payload = payload
        def execute(self):
            return self.payload

    class Status:
        def __init__(self, value):
            self.value = value
        def progress(self):
            return self.value

    class Request:
        def __init__(self):
            self.calls = 0
        def next_chunk(self, num_retries=0):
            self.calls += 1
            if self.calls == 1:
                return Status(0.5), None
            return Status(1.0), {"id": "video123"}

    request = Request()

    class Videos:
        def insert(self, **_kwargs):
            return request

    class Channels:
        def list(self, **_kwargs):
            return Result({"items": []})

    class Service:
        def channels(self):
            return Channels()
        def videos(self):
            return Videos()

    monkeypatch.setattr(youtube, "_service", lambda _channel: Service())
    monkeypatch.setattr(youtube, "MediaFileUpload", lambda *_args, **_kwargs: object())
    progress = []
    video_id, url = upload_video(job, channel, progress_callback=progress.append)
    assert rendered.exists()
    assert video_id == "video123"
    assert url.endswith("video123")
    assert progress == [0, 50, 100]
