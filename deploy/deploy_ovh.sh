#!/usr/bin/env bash
# Deployment trigger: pending privacy queue.
# Deployment trigger: privacy response fix.
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
  --exclude='/data/' \
  --exclude='backup-*.tgz' \
  --exclude='deploy/cdg-portal-auth.patch' \
  "$staging/" "$target/"

# Actualiza el portal principal.
sudo install -d -m 755 /opt/djgabo-portal
sudo install -m 644 "$target/deploy/portal/index.html" /opt/djgabo-portal/index.html

# Actualiza exactamente la configuración Nginx que ya sirve panel.kitkaraoke.com.
nginx_match="$(sudo sh -c "grep -Rsl 'server_name panel\.kitkaraoke\.com' /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null | head -n 1" || true)"
if [ -z "$nginx_match" ] && sudo test -e /etc/nginx/sites-available/panel.kitkaraoke.com; then
  nginx_match=/etc/nginx/sites-available/panel.kitkaraoke.com
fi
if [ -z "$nginx_match" ]; then
  echo "No se encontró la configuración Nginx activa de panel.kitkaraoke.com" >&2
  exit 1
fi

nginx_target="$(sudo readlink -f "$nginx_match")"
nginx_backup="${nginx_target}.backup-${revision}"
sudo cp -a "$nginx_target" "$nginx_backup"
sudo install -m 644 "$target/deploy/nginx-panel-integrado.conf" "$nginx_target"

if ! sudo nginx -t; then
  echo "La nueva configuración Nginx no es válida; restaurando respaldo." >&2
  sudo cp -a "$nginx_backup" "$nginx_target"
  sudo nginx -t
  exit 1
fi
sudo systemctl reload nginx

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
lyrics_health="$(curl \
  --fail \
  --silent \
  --show-error \
  --retry 24 \
  --retry-all-errors \
  --retry-delay 5 \
  --max-time 15 \
  http://127.0.0.1:8090/api/health)"

printf '%s\n' "$lyrics_health"

LYRICS_HEALTH="$lyrics_health" python3 - <<'PY'
import json
import os

health = json.loads(os.environ["LYRICS_HEALTH"])

assert health.get("ok") is True, health
assert health.get("version") == "0.7.0-lab", health
assert int(health.get("lexicon_words", 0)) >= 45000, health
assert int(health.get("lexicon_con", 0)) >= 100000, health

print(
    "CDG_LYRICS_HEALTH_OK",
    {
        "version": health["version"],
        "lexicon_words": health["lexicon_words"],
        "lexicon_con": health["lexicon_con"],
    },
)
PY

printf '\n'
grep -q 'MOTOR 03' /opt/djgabo-portal/index.html
sudo grep -q 'location /cdg-lyrics/' "$nginx_target"

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
    recent = db.execute(
        select(Job.filename_original, Job.channel_id, Job.youtube_video_id, Job.published_at)
        .where(Job.youtube_video_id.is_not(None))
        .order_by(Job.published_at.desc())
        .limit(10)
    ).all()
    print("RECENT_PUBLICATIONS", [
        {"file": file, "channel_id": channel_id, "video_id": video_id, "published_at": str(published_at)}
        for file, channel_id, video_id, published_at in recent
    ])
PY

printf '\nDeployment %s completed successfully.\n' "$revision"
