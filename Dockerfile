FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data/incoming /data/processing /data/ready /data/assets /data/failed /data/db
EXPOSE 8088
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8088"]
