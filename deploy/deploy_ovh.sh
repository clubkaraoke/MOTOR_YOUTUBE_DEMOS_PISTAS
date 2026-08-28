#!/usr/bin/env bash
set -euo pipefail

archive=${1:?Release archive is required}
revision=${2:?Git revision is required}
target=/opt/djgabo-youtube
staging="/tmp/djgabo-youtube-${revision}"
backup="${target}/backup-before-github-${revision}.tgz"

cleanup() {
  rm -rf -- "$staging" "$archive"
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
  -czf "$backup" \
  -C "$target" .

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

printf '\nDeployment %s completed successfully.\n' "$revision"
