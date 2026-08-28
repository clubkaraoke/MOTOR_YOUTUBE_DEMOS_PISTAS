#!/usr/bin/env bash
set -euo pipefail

archive=${1:?Release archive is required}
revision=${2:?Git revision is required}
target=/opt/djgabo-youtube
staging="/tmp/djgabo-youtube-${revision}"
backup="${target}/backup-before-github-${revision}.tgz"
backup_tmp="/tmp/backup-before-github-${revision}.tgz"

cleanup() {
  rm -rf -- "$staging" "$archive"
  sudo rm -f -- "$backup_tmp"
}
trap cleanup EXIT

test -f "$archive"
sudo test -f "$target/.env"
sudo test -d "$target/data"

rm -rf -- "$staging"
mkdir -p -- "$staging"
tar -xzf "$archive" -C "$staging"

sudo tar \
  --exclude='./.env' \
  --exclude='./data' \
  --exclude='./backup-*.tgz' \
  -czf "$backup_tmp" \
  -C "$target" .
sudo mv -- "$backup_tmp" "$backup"

sudo rsync -a --delete \
  --exclude='.env' \
  --exclude='data/' \
  --exclude='backup-*.tgz' \
  --exclude='deploy/cdg-portal-auth.patch' \
  "$staging/" "$target/"

cd "$target"
sudo docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
sudo docker compose -f docker-compose.prod.yml ps

curl \
  --fail \
  --silent \
  --show-error \
  --retry 24 \
  --retry-all-errors \
  --retry-delay 5 \
  --max-time 10 \
  http://127.0.0.1:8088/health

printf '\n'
sudo docker compose -f docker-compose.prod.yml exec -T web python - <<'PY'
from sqlalchemy import func, select
from app.core.database import SessionLocal
from app.models.entities import Job

with SessionLocal() as db:
    counts = db.execute(
        select(Job.status, func.count(Job.id)).group_by(Job.status).order_by(Job.status)
    ).all()
    print("JOB_STATUS_COUNTS", {status: count for status, count in counts})
    pending = db.scalar(select(func.count(Job.id)).where(Job.youtube_video_id.is_(None))) or 0
    published = db.scalar(select(func.count(Job.id)).where(Job.youtube_video_id.is_not(None))) or 0
    print("JOB_RECOVERY_SUMMARY", {"pending_without_video": pending, "with_video_id": published})
PY

printf '\nDeployment %s completed successfully.\n' "$revision"
