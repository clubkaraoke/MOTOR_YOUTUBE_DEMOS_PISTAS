#!/usr/bin/env bash
set -euo pipefail

PATCH=/tmp/patch_cdg_editor_ia_7_improvements.py
REBUILD=/tmp/rebuild_latest_ia_test_after_7.py
ROOT=/opt/djgabo-cdg-ia-test

echo "TIMESTAMP=$(date -Is)"
echo "PRODUCTION_BEFORE=$(sudo systemctl is-active djgabo-cdg)"
echo "TEST_BEFORE=$(sudo systemctl is-active djgabo-cdg-ia-test)"
test "$(sudo systemctl is-active djgabo-cdg)" = "active"

sudo python3 "$PATCH"
sudo -u djgabo-cdg "$ROOT/.venv/bin/python" -m py_compile "$ROOT/server.py" "$ROOT/renderer/normalize.py" "$ROOT/renderer/render.py"
sudo systemctl restart djgabo-cdg-ia-test

ok=0
for i in $(seq 1 50); do
  if curl -fsS http://127.0.0.1:8775/healthz >/tmp/ia-health-7.json 2>/dev/null; then
    ok=1
    break
  fi
  sleep 2
done
test "$ok" = 1
echo "TEST_HEALTH=OK"

cd "$ROOT"
sudo -u djgabo-cdg env \
  HOME=/var/lib/djgabo-cdg-ia-test \
  DJGABO_ENV=test \
  DJGABO_TEST_MODE=1 \
  DJGABO_URL_PREFIX=/cdg-editor-ia \
  DJGABO_DATA_DIR=/var/lib/djgabo-cdg-ia-test \
  DJGABO_CONFIG_DIR=/var/lib/djgabo-cdg-ia-test/config \
  DJGABO_DB_PATH=/var/lib/djgabo-cdg-ia-test/local.db \
  DJGABO_JOBS_DIR=/var/lib/djgabo-cdg-ia-test/jobs \
  DJGABO_OUTPUT_DIR=/var/lib/djgabo-cdg-ia-test/output \
  DJGABO_VOICE_CACHE_DIR=/var/lib/djgabo-cdg-ia-test/voice_cache \
  DJGABO_SKIP_PREV_MIGRATION=1 \
  "$ROOT/.venv/bin/python" "$REBUILD"

sudo -u djgabo-cdg "$ROOT/.venv/bin/python" - <<'PY'
import sys
sys.path.insert(0,'/opt/djgabo-cdg-ia-test/renderer')
import normalize as N
a=[[{'text':'a'}],[{'text':'b'}],[{'text':'c'}],[{'text':'d'}],[],[{'text':'e'}],[{'text':'f'}]]
out=N.center_stanza_pages(a,6)
pos=[i%6 for i,line in enumerate(out) if line and line[0]['text'] in ('e','f')]
print('CENTER_ROWS='+str(pos))
assert pos==[2,3],pos
PY

curl -fsS http://127.0.0.1:8775/ >/tmp/panel7.html
curl -fsS 'http://127.0.0.1:8775/editor-v1?embed=1' >/tmp/editor7.html
grep -q 'iaProgressFill' /tmp/panel7.html
grep -q 'create-job/start' /tmp/panel7.html
grep -q 'aiQaBar' /tmp/editor7.html
grep -q 'VOZ SIN TEXTO' /tmp/editor7.html
grep -q 'const ss = 2' /tmp/editor7.html
echo "FRONTEND_7=OK"

test "$(sudo systemctl is-active djgabo-cdg-ia-test)" = "active"
test "$(sudo systemctl is-active djgabo-cdg)" = "active"
prod_http="$(curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/)"
test "$prod_http" = "200"
echo "TEST_AFTER=active"
echo "PRODUCTION_AFTER=active"
echo "PRODUCTION_HTTP=$prod_http"
echo "DEPLOY_7_IMPROVEMENTS=OK"
