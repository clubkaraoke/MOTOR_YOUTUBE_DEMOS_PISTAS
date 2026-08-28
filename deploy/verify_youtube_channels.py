from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.entities import Channel
from app.services.youtube import _service


with SessionLocal() as database:
    channels = database.scalars(select(Channel).order_by(Channel.id)).all()
    for channel in channels:
        response = _service(channel).channels().list(part="id,snippet", mine=True).execute()
        items = response.get("items", [])
        if not items:
            raise RuntimeError(f"C{channel.id}: Google no devolvió un canal")
        item = items[0]
        print(f"C{channel.id}: {item['snippet']['title']} · API OK")
