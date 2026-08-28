from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Channel, ChannelPublication


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class ChannelSlot:
    channel: Channel
    used: int
    next_slot: datetime | None


def channel_slots(db: Session, now: datetime | None = None) -> list[ChannelSlot]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    result: list[ChannelSlot] = []
    channels = db.scalars(select(Channel).where(Channel.enabled.is_(True)).order_by(Channel.id)).all()
    for channel in channels:
        statement = (
            select(ChannelPublication)
            .where(ChannelPublication.channel_id == channel.id)
            .where(ChannelPublication.published_at > since)
            .order_by(ChannelPublication.published_at)
        )
        if get_settings().youtube_mode == "real":
            statement = statement.where(~ChannelPublication.youtube_video_id.like("mock_%"))
        publications = db.scalars(statement).all()
        used = len(publications)
        next_slot = (_aware(publications[0].published_at) + timedelta(hours=24)) if used >= channel.max_uploads_24h else None
        result.append(ChannelSlot(channel, used, next_slot))
    return result


def choose_channel(db: Session, now: datetime | None = None) -> ChannelSlot | None:
    available = [slot for slot in channel_slots(db, now) if slot.used < slot.channel.max_uploads_24h]
    return min(available, key=lambda slot: (slot.used, slot.channel.id)) if available else None


def global_next_slot(db: Session, now: datetime | None = None) -> datetime | None:
    values = [slot.next_slot for slot in channel_slots(db, now) if slot.next_slot]
    return min(values) if values else None
