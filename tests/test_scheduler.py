from datetime import datetime, timedelta, timezone

from app.models.entities import Channel, ChannelPublication, Job
from app.services.scheduler import channel_slots, choose_channel, global_next_slot


def make_job(index: int, channel: Channel) -> Job:
    return Job(id=f"00000000-0000-0000-0000-{index:012d}", filename_original=f"A - {index}.mp3",
               artist="A", title=str(index), sha256=f"{index:064x}", original_duration_seconds=180,
               cut_seconds=80, channel=channel)


def test_exact_seven_per_rolling_24h_and_next_slot_survives_new_session(db):
    now = datetime.now(timezone.utc)
    c1 = Channel(display_name="C1", max_uploads_24h=7)
    c2 = Channel(display_name="C2", max_uploads_24h=7)
    db.add_all([c1, c2]); db.flush()
    oldest = now - timedelta(hours=23)
    index = 1
    for channel, count in ((c1, 7), (c2, 6)):
        for offset in range(count):
            job = make_job(index, channel); db.add(job); db.flush()
            db.add(ChannelPublication(channel_id=channel.id, job_id=job.id,
                                      youtube_video_id=f"v{index}", published_at=oldest + timedelta(minutes=offset)))
            index += 1
    db.commit(); db.expire_all()
    slots = {s.channel.display_name: s for s in channel_slots(db, now)}
    assert slots["C1"].used == 7
    assert slots["C1"].next_slot == oldest + timedelta(hours=24)
    assert choose_channel(db, now).channel.display_name == "C2"
    assert global_next_slot(db, now) == oldest + timedelta(hours=24)


def test_all_channels_full_returns_waiting(db):
    now = datetime.now(timezone.utc)
    for cidx in range(1, 5):
        channel = Channel(display_name=f"C{cidx}", max_uploads_24h=7); db.add(channel); db.flush()
        for n in range(7):
            idx = cidx * 100 + n
            job = make_job(idx, channel); db.add(job); db.flush()
            db.add(ChannelPublication(channel_id=channel.id, job_id=job.id, youtube_video_id=f"v{idx}", published_at=now-timedelta(hours=1)))
    db.commit()
    assert choose_channel(db, now) is None

