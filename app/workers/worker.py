import logging

from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    connection = Redis.from_url(get_settings().redis_url)
    Worker([Queue("djgabo", connection=connection)], connection=connection).work(with_scheduler=True)

