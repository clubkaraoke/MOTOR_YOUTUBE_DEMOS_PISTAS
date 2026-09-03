#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

MARKER = "PROD_FINAL_CDG_PREVIEW_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontre {count}")
    return text.replace(old, new, 1)


def patch_normalize(path: Path):
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    old = """        start = line[0][\"start_time\"]
        gap = start if prev_end is None else start - prev_end
        base = prev_end or 0.0
"""
    new = """        start = line[0][\"start_time\"]
        # PROD_FINAL_CDG_PREVIEW_V1
        # El primer bloque INSTRUMENTAL comparte la MISMA timeline absoluta
        # que el opening. Nunca puede empezar en t=0 y quedar en cola detras
        # del opening: eso obliga a cdgmaker a \"ponerse al dia\" despues de
        # los 6 s y desplaza visualmente los circulos/letras en el CDG final.
        # Esta base coincide exactamente con pvInstrumentalState() del editor.
        if prev_end is None:
            base = max(0.0, float(style.get(\"intro_duration_seconds\", 0.0) or 0.0) + 0.25)
            gap = start - base
        else:
            base = prev_end
            gap = start - prev_end
"""
    text = replace_once(text, old, new, "normalize first instrumental base")
    path.write_text(text, encoding="utf-8")


def patch_server(path: Path):
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    anchor = """@app.post('/api/jobs/<jid>/dropbox/retry-cdg')
def retry_cdg_dropbox(jid):
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    try:
        pub=publish_job_to_dropbox(jid)
        return jsonify(ok=True,dropbox_folder=pub.get('folder',''),dropbox_status=pub.get('status',''),uploaded_cdg=pub.get('uploaded_cdg'),uploaded_wav=pub.get('uploaded_wav'))
    except Exception as e: return jsonify(ok=False,error=str(e)),502

"""
    addition = anchor + """# PROD_FINAL_CDG_PREVIEW_V1
# Rutas del preview del archivo CDG REAL + Voz + WAV, registradas aparte para
# no mezclar el motor de reproduccion con el backend historico.
from cdg_preview_routes import register_cdg_preview_routes
register_cdg_preview_routes(app, globals())

"""
    text = replace_once(text, anchor, addition, "server preview routes")
    path.write_text(text, encoding="utf-8")


def patch_editor(path: Path):
    text = path.read_text(encoding="utf-8")
    if "cdg-final-preview.js" not in text:
        anchor = "</body>"
        addition = """<!-- PROD_FINAL_CDG_PREVIEW_V1 · motor basado en CDG_PLAYER_ONLINE -->
<script src="/api/vendor/cdg-final-preview.js"></script>
</body>"""
        text = replace_once(text, anchor, addition, "editor preview asset")

    # Cuando el render se dispara desde la pestaña CDG debemos quedarnos en el
    # mismo trabajo para poder revisar el archivo final. El flujo normal de
    # Exportar conserva su avance automático al siguiente trabajo.
    nav = """if(window.parent!==window) window.parent.postMessage({type:'panel:export-success',job_id:PANEL_JOB_ID,next_job_id:done.next_job_id||''},location.origin);"""
    guarded = """if(!window.DJGABO_CDG_PREVIEW_RENDERING && window.parent!==window) window.parent.postMessage({type:'panel:export-success',job_id:PANEL_JOB_ID,next_job_id:done.next_job_id||''},location.origin);"""
    if guarded not in text:
        text = replace_once(text, nav, guarded, "editor preview keep current job")

    path.write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--routes-source", required=True)
    ap.add_argument("--player-source", required=True)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    server = root / "server.py"
    normalize = root / "renderer" / "normalize.py"
    editor = root / "editor_v1" / "index.html"
    for p in (server, normalize, editor):
        if not p.is_file():
            raise SystemExit(f"Falta {p}")

    shutil.copy2(args.routes_source, root / "cdg_preview_routes.py")
    vendor = root / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.player_source, vendor / "cdg-final-preview.js")

    patch_normalize(normalize)
    patch_server(server)
    patch_editor(editor)

    print("PATCH=OK")
    print("MARKER=" + MARKER)


if __name__ == "__main__":
    main()
