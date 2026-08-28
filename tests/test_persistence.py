from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import Job, JobStatus


def test_twenty_jobs_survive_database_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'persistent.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        for i in range(20):
            db.add(Job(filename_original=f"Artist - Track {i}.mp3", artist="Artist", title=f"Track {i}",
                       sha256=f"{i:064x}", original_duration_seconds=180, cut_seconds=80,
                       status=JobStatus.QUEUED.value))
        db.commit()
    engine.dispose()
    restarted = create_engine(url)
    with sessionmaker(bind=restarted)() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 20
        assert all(job.status == JobStatus.QUEUED.value for job in jobs)
