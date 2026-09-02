#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path("/opt/djgabo-cdg-ia-test")
SERVER = ROOT / "server.py"
PANEL = ROOT / "panel.html"
EDITOR = ROOT / "editor_v1" / "index.html"

for p in (SERVER, PANEL, EDITOR):
    if not p.is_file():
        raise SystemExit(f"Falta archivo del clon: {p}")

s = SERVER.read_text(encoding="utf-8")

if "DJGABO_TEST_MODE" not in s:
    s = s.replace(
        "ENVIRONMENT=str(os.getenv('DJGABO_ENV') or 'local').strip().lower()\n"
        "IS_PRODUCTION=ENVIRONMENT in ('production','prod','server','ovh')",
        "ENVIRONMENT=str(os.getenv('DJGABO_ENV') or 'local').strip().lower()\n"
        "IS_PRODUCTION=ENVIRONMENT in ('production','prod','server','ovh')\n"
        "TEST_MODE=env_bool('DJGABO_TEST_MODE', ENVIRONMENT in ('test','testing','lab'))\n"
        "URL_PREFIX=str(os.getenv('DJGABO_URL_PREFIX') or '').strip().rstrip('/')\n\n"
        "def public_path(path):\n"
        "    path='/' + str(path or '').lstrip('/')\n"
        "    return (URL_PREFIX + path) if URL_PREFIX else path"
    )

s = s.replace(
    "PORTAL_COOKIE_NAME='djgabo_portal_session'",
    "PORTAL_COOKIE_NAME='djgabo_portal_session_ia_test' if TEST_MODE else 'djgabo_portal_session'"
)

if "TEST_MODE and action in {'master_reserve'" not in s:
    s = s.replace(
        "def drive_bridge_call(action, payload=None, timeout=90):\n"
        "    \"\"\"Puente Drive con autorreparación contundente.",
        "def drive_bridge_call(action, payload=None, timeout=90):\n"
        "    if TEST_MODE and action in {'master_reserve','master_sync','master_file','master_state','mark_copied','autosave','save_corrected'}:\n"
        "        payload=dict(payload or {})\n"
        "        result={'ok':True,'test_mode':True,'blocked_action':action}\n"
        "        if 'lyrics' in payload: result['lyrics']=payload.get('lyrics','')\n"
        "        if 'version' in payload: result['version']=payload.get('version')\n"
        "        if 'status' in payload: result['status']=payload.get('status')\n"
        "        app.logger.info('TEST_MODE bloqueó escritura remota Drive/Sheet: %s',action)\n"
        "        return result\n"
        "    \"\"\"Puente Drive con autorreparación contundente."
    )

s = s.replace(
    "def drive_bridge_get_job(jid, open_job=False, actor='Valeria'):\n"
    "    action='open_job' if open_job else 'job'",
    "def drive_bridge_get_job(jid, open_job=False, actor='Valeria'):\n"
    "    action='job' if TEST_MODE else ('open_job' if open_job else 'job')"
)

s = s.replace(
    "def backup_voice_to_drive(jid):\n    with db() as c:",
    "def backup_voice_to_drive(jid):\n"
    "    if TEST_MODE:\n"
    "        app.logger.info('TEST_MODE: backup_voice_to_drive omitido para %s',jid)\n"
    "        return {'id':'TEST_LOCAL','test_mode':True}\n"
    "    with db() as c:"
)

s = s.replace(
    "def backup_timings_to_drive(jid,data):\n    raw=bytes(data)",
    "def backup_timings_to_drive(jid,data):\n"
    "    if TEST_MODE:\n"
    "        app.logger.info('TEST_MODE: backup_timings_to_drive omitido para %s',jid)\n"
    "        return {'id':'TEST_LOCAL','test_mode':True}\n"
    "    raw=bytes(data)"
)

s = s.replace(
    "def schedule_timings_backup(jid,data):\n    jid=str(jid)",
    "def schedule_timings_backup(jid,data):\n"
    "    if TEST_MODE:\n"
    "        return\n"
    "    jid=str(jid)"
)

s = s.replace(
    "def schedule_timings_rename(jid):\n    with db() as c:",
    "def schedule_timings_rename(jid):\n"
    "    if TEST_MODE:\n"
    "        return\n"
    "    with db() as c:"
)

s = s.replace(
    "def dropbox_connected(cfg=None):\n    cfg=cfg or load_dropbox_cfg()",
    "def dropbox_connected(cfg=None):\n"
    "    if TEST_MODE:\n"
    "        return False\n"
    "    cfg=cfg or load_dropbox_cfg()"
)

if "Dropbox desactivado en CDG Editor IA TEST." not in s:
    s = s.replace(
        "def dropbox_access_token():",
        "def dropbox_access_token():\n"
        "    if TEST_MODE:\n"
        "        raise ValueError('Dropbox desactivado en CDG Editor IA TEST.')"
    )

s = s.replace(
    "return jsonify(ok=True,service='djgabo-cdg',version='16.14-server',environment=ENVIRONMENT,checks=checks),200 if ok else 503",
    "return jsonify(ok=True,service='djgabo-cdg',version='16.14-server',environment=ENVIRONMENT,test_mode=TEST_MODE,url_prefix=URL_PREFIX,checks=checks),200 if ok else 503"
)

s = s.replace(
    "voice_url=f'/api/jobs/{jid}/voice',peaks_url=f'/api/jobs/{jid}/peaks',peaks_status_url=f'/api/jobs/{jid}/peaks-status'",
    "voice_url=public_path(f'/api/jobs/{jid}/voice'),peaks_url=public_path(f'/api/jobs/{jid}/peaks'),peaks_status_url=public_path(f'/api/jobs/{jid}/peaks-status')"
)
s = s.replace(
    "status_url='/api/render/status/'+task_id",
    "status_url=public_path('/api/render/status/'+task_id)"
)

SERVER.write_text(s, encoding="utf-8")

def patch_html(path: Path, panel=False):
    h = path.read_text(encoding="utf-8")
    if "/cdg-editor-ia/api/" not in h:
        h = h.replace("'/api/", "'/cdg-editor-ia/api/")
        h = h.replace('"/api/', '"/cdg-editor-ia/api/')
        h = h.replace("`/api/", "`/cdg-editor-ia/api/")
    if panel:
        h = h.replace("'/editor-v1", "'/cdg-editor-ia/editor-v1")
        h = h.replace('"/editor-v1', '"/cdg-editor-ia/editor-v1')
        h = h.replace("`/editor-v1", "`/cdg-editor-ia/editor-v1")
    if "/cdg-editor-ia/dropbox/" not in h:
        h = h.replace("'/dropbox/", "'/cdg-editor-ia/dropbox/")
        h = h.replace('"/dropbox/', '"/cdg-editor-ia/dropbox/')
        h = h.replace("`/dropbox/", "`/cdg-editor-ia/dropbox/")
    if "CDG_EDITOR_IA_TEST_BADGE" not in h:
        h = h.replace("</title>", " · IA TEST</title>", 1)
        h = h.replace(
            "<body>",
            '<body>\n<div id="CDG_EDITOR_IA_TEST_BADGE" style="position:fixed;top:8px;right:10px;z-index:99999;background:#7c3aed;color:white;border:1px solid rgba(255,255,255,.35);border-radius:999px;padding:5px 10px;font:700 11px/1.1 Arial,sans-serif;letter-spacing:.08em;box-shadow:0 4px 18px rgba(0,0,0,.3)">IA TEST · NO PRODUCCIÓN</div>',
            1
        )
    path.write_text(h, encoding="utf-8")

patch_html(PANEL, panel=True)
patch_html(EDITOR, panel=False)

required = [
    "TEST_MODE=env_bool('DJGABO_TEST_MODE'",
    "PORTAL_COOKIE_NAME='djgabo_portal_session_ia_test'",
    "Dropbox desactivado en CDG Editor IA TEST.",
    "public_path(f'/api/jobs/{jid}/voice')",
]
final = SERVER.read_text(encoding="utf-8")
for marker in required:
    if marker not in final:
        raise SystemExit(f"Patch incompleto: falta {marker}")

print("CDG_EDITOR_IA_TEST_PATCH=OK")
