#!/usr/bin/env bash
set -euo pipefail

PATCH_FILE="${1:-/tmp/patch_cdg_editor_ia_test.py}"
NGINX_FILE="${2:-/tmp/nginx-panel-cdg-editor-ia.conf}"

SRC=/opt/djgabo-cdg
DST=/opt/djgabo-cdg-ia-test
DATA_SRC=/var/lib/djgabo-cdg
DATA_DST=/var/lib/djgabo-cdg-ia-test
SERVICE=djgabo-cdg-ia-test
PORT=8775

echo "TIMESTAMP=$(date -Is)"
echo "=== PRECHECK ==="
test -d "$SRC"
test -f "$SRC/server.py"
test -f "$SRC/editor_v1/index.html"
test -f "$PATCH_FILE"
test -f "$NGINX_FILE"
systemctl is-active --quiet djgabo-cdg
echo "PRODUCTION_SERVICE=active"

echo "=== COPY CODE BASELINE ==="
sudo mkdir -p "$DST"
sudo rsync -a --delete \
  --exclude='.venv/' \
  --exclude='deploy-backups/' \
  --exclude='__pycache__/' \
  "$SRC/" "$DST/"
sudo chown -R djgabo-cdg:djgabo-cdg "$DST"

echo "=== COPY TEST DATA ON FIRST BOOT ==="
sudo mkdir -p "$DATA_DST"
if [ ! -f "$DATA_DST/local.db" ]; then
  sudo rsync -a \
    --exclude='dropbox_oauth.json' \
    --exclude='pending/' \
    "$DATA_SRC/" "$DATA_DST/"
fi
sudo rm -f "$DATA_DST/dropbox_oauth.json"
sudo mkdir -p "$DATA_DST/config" "$DATA_DST/output" "$DATA_DST/voice_cache"
sudo chown -R djgabo-cdg:djgabo-cdg "$DATA_DST"

echo "=== APPLY TEST SAFETY PATCH ==="
sudo python3 "$PATCH_FILE"

echo "=== PYTHON CHECK ==="
sudo -u djgabo-cdg python3 -m py_compile "$DST/server.py" "$DST/renderer/render.py" "$DST/renderer/normalize.py"

echo "=== VENV ==="
if [ ! -x "$DST/.venv/bin/python" ]; then
  sudo -u djgabo-cdg python3 -m venv "$DST/.venv"
fi
sudo -u djgabo-cdg "$DST/.venv/bin/pip" install --disable-pip-version-check -q -r "$DST/requirements.txt"

echo "=== SYSTEMD TEST SERVICE ==="
sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<'UNIT'
[Unit]
Description=DJGABO CDG Editor IA TEST
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=djgabo-cdg
Group=djgabo-cdg
WorkingDirectory=/opt/djgabo-cdg-ia-test
EnvironmentFile=/etc/djgabo-cdg/djgabo-cdg.env
Environment=DJGABO_ENV=test
Environment=DJGABO_TEST_MODE=1
Environment=DJGABO_URL_PREFIX=/cdg-editor-ia
Environment=DJGABO_DATA_DIR=/var/lib/djgabo-cdg-ia-test
Environment=DJGABO_CONFIG_DIR=/var/lib/djgabo-cdg-ia-test/config
Environment=DJGABO_DB_PATH=/var/lib/djgabo-cdg-ia-test/local.db
Environment=DJGABO_JOBS_DIR=/var/lib/djgabo-cdg-ia-test/jobs
Environment=DJGABO_OUTPUT_DIR=/var/lib/djgabo-cdg-ia-test/output
Environment=DJGABO_VOICE_CACHE_DIR=/var/lib/djgabo-cdg-ia-test/voice_cache
Environment=DJGABO_SKIP_PREV_MIGRATION=1
Environment=DJGABO_BIND=127.0.0.1:8775
Environment=DROPBOX_APP_KEY=
Environment=DROPBOX_APP_SECRET=
Environment=DROPBOX_REFRESH_TOKEN=
ExecStart=/opt/djgabo-cdg-ia-test/.venv/bin/gunicorn --config /opt/djgabo-cdg-ia-test/deploy/gunicorn.conf.py server:app
Restart=on-failure
RestartSec=5
TimeoutStartSec=90
TimeoutStopSec=180
KillSignal=SIGTERM
KillMode=mixed
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/djgabo-cdg-ia-test

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null
sudo systemctl restart "$SERVICE"

echo "=== WAIT TEST SERVICE ==="
ok=0
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/tmp/cdg-editor-ia-health.json 2>/dev/null; then
    ok=1
    break
  fi
  sleep 2
done
test "$ok" = 1
cat /tmp/cdg-editor-ia-health.json
echo

echo "=== VERIFY TEST FLAGS ==="
python3 - <<'PY'
import json
d=json.load(open('/tmp/cdg-editor-ia-health.json'))
assert d.get('ok') is True, d
assert d.get('test_mode') is True, d
assert d.get('environment') == 'test', d
assert d.get('url_prefix') == '/cdg-editor-ia', d
print('TEST_FLAGS=OK')
PY

curl -fsS "http://127.0.0.1:$PORT/" >/tmp/cdg-editor-ia-panel.html
curl -fsS "http://127.0.0.1:$PORT/editor-v1?embed=1" >/tmp/cdg-editor-ia-editor.html
grep -q 'CDG_EDITOR_IA_TEST_BADGE' /tmp/cdg-editor-ia-panel.html
grep -q 'CDG_EDITOR_IA_TEST_BADGE' /tmp/cdg-editor-ia-editor.html
grep -q '/cdg-editor-ia/api/' /tmp/cdg-editor-ia-panel.html
grep -q '/cdg-editor-ia/api/' /tmp/cdg-editor-ia-editor.html
echo "HTML_PREFIX=OK"

echo "=== INSTALL NGINX ROUTE ==="
sudo cp "$NGINX_FILE" /etc/nginx/sites-available/panel.kitkaraoke.com
sudo nginx -t
sudo systemctl reload nginx

echo "=== FINAL VERIFY ==="
echo "TEST_SERVICE=$(sudo systemctl is-active "$SERVICE")"
echo "PRODUCTION_SERVICE=$(sudo systemctl is-active djgabo-cdg)"
echo "PRODUCTION_HTTP=$(curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/)"
echo "TEST_HTTP=$(curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8775/)"
echo "TEST_CODE_SIZE=$(sudo du -sh "$DST" | awk '{print $1}')"
echo "TEST_DATA_SIZE=$(sudo du -sh "$DATA_DST" | awk '{print $1}')"
echo "DROPBOX_OAUTH_FILE_PRESENT=$(test -f "$DATA_DST/dropbox_oauth.json" && echo yes || echo no)"
echo "BOOTSTRAP_CDG_EDITOR_IA_TEST=OK"
