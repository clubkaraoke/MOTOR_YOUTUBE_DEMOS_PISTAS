#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

PREFIX = "/cdg-v2"
MARK = "DJGABO_CDG_ENGINE_V2_CLONE_PATCH"


def patch_html(path: Path, is_panel: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        return
    # El navegador ve /cdg-v2/... pero Nginx quita ese prefijo antes de llegar
    # al Flask aislado. Cualquier URL absoluta del HTML debe conservarlo.
    text = text.replace("/api/", PREFIX + "/api/")
    if is_panel:
        text = text.replace("/editor-v1", PREFIX + "/editor-v1")
    else:
        # El player V1 queda fuera del LAB: el V2 trae su propio decoder CDG.
        text = re.sub(r'<script[^>]+cdg-final-preview\.js[^>]*></script>\s*', '', text, flags=re.I)
        script = f'<script src="{PREFIX}/api/vendor/cdg-v2-studio.js"></script>'
        mobile = f'<script src="{PREFIX}/api/vendor/cdg-v2-mobile.js"></script>'
        if "</body>" not in text:
            raise RuntimeError(f"{path}: no encuentro </body>")
        text = text.replace("</body>", script + "\n" + mobile + "\n<!-- " + MARK + " -->\n</body>", 1)
        text = text.replace("Sincronizador de karaoke · DJGABO · IA TEST", "Sincronizador de karaoke · DJGABO · CDG ENGINE V2 LAB")
    if is_panel:
        script = f'<script src="{PREFIX}/api/vendor/cdg-v2-lab.js"></script>'
        mobile = f'<script src="{PREFIX}/api/vendor/cdg-v2-mobile.js"></script>'
        text = text.replace("</body>", script + "\n" + mobile + "\n<!-- " + MARK + " -->\n</body>", 1)
    path.write_text(text, encoding="utf-8")


def patch_server(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        return
    old = "voice_url=f'/api/jobs/{jid}/voice',peaks_url=f'/api/jobs/{jid}/peaks',peaks_status_url=f'/api/jobs/{jid}/peaks-status'"
    new = "voice_url=f'/cdg-v2/api/jobs/{jid}/voice',peaks_url=f'/cdg-v2/api/jobs/{jid}/peaks',peaks_status_url=f'/cdg-v2/api/jobs/{jid}/peaks-status'"
    if old not in text:
        raise RuntimeError("server.py: no encuentro editor-data URLs")
    text = text.replace(old, new, 1)
    # Puerto propio del clon; producción conserva 8765.
    old_run = "app.run(host='127.0.0.1',port=8765,debug=False,threaded=True)"
    if old_run not in text:
        raise RuntimeError("server.py: no encuentro app.run de producción")
    text = text.replace(
        old_run,
        "app.run(host='127.0.0.1',port=int(os.getenv('DJGABO_BIND_PORT') or 8787),debug=False,threaded=True)",
        1,
    )

    # Cookie propia del LAB: nunca sobrescribir djgabo_portal_session de producción.
    old_cookie = "PORTAL_COOKIE_NAME='djgabo_portal_session'"
    if old_cookie not in text:
        raise RuntimeError("server.py: no encuentro PORTAL_COOKIE_NAME de producción")
    text = text.replace(old_cookie, "PORTAL_COOKIE_NAME='djgabo_v2_portal_session'", 1)

    old_status = "status_url='/api/render/status/'+task_id"
    if old_status not in text:
        raise RuntimeError("server.py: no encuentro render status_url")
    text = text.replace(old_status, "status_url='/cdg-v2/api/render/status/'+task_id", 1)

    # El clon conserva el guardado local y la lectura de Drive/Dropbox, pero no
    # debe actualizar estados/autosaves externos del panel productivo al abrir
    # un trabajo de laboratorio.
    safe_flag = "env_bool('DJGABO_V2_CLONE_SAFE',False)"
    text = text.replace(
        "if managed and job_snapshot.get('origin')!='HISTORICO_DRIVE':",
        "if managed and job_snapshot.get('origin')!='HISTORICO_DRIVE' and not " + safe_flag + ":",
        1,
    )
    text = text.replace(
        "if _sheet_managed(r): drive_bridge_call('autosave',",
        "if _sheet_managed(r) and not " + safe_flag + ": drive_bridge_call('autosave',",
    )
    text = text.replace(
        "    schedule_timings_backup(jid,raw)\n    return jsonify(ok=True)",
        "    if not " + safe_flag + ": schedule_timings_backup(jid,raw)\n    return jsonify(ok=True)",
        1,
    )

    anchor = "if __name__=='__main__':"
    if anchor not in text:
        raise RuntimeError("server.py: no encuentro bloque __main__")
    register = '''# DJGABO_CDG_ENGINE_V2_CLONE_PATCH\n# Rutas exclusivas del clon. Nunca se registran en /opt/djgabo-cdg.\n# En modo LAB anulamos cualquier respaldo/escritura externa heredada del panel.\nif env_bool("DJGABO_V2_CLONE_SAFE", False):\n    master_sync = lambda *args, **kwargs: {"ok": True, "v2_disabled": True}\n    schedule_timings_backup = lambda *args, **kwargs: None\n    backup_voice_to_drive = lambda *args, **kwargs: {"ok": True, "v2_disabled": True}\nfrom cdg_v2_routes import register_cdg_v2_routes\nregister_cdg_v2_routes(app, globals())\n\n'''
    text = text.replace(anchor, register + anchor, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("uso: patch_clone.py ROOT SOURCE_DIR")
    root = Path(sys.argv[1]).resolve()
    src = Path(sys.argv[2]).resolve()
    if not (root / "server.py").is_file():
        raise RuntimeError(f"No parece clon CDG: {root}")
    editor = root / "editor_v1" / "index.html"
    panel = root / "panel.html"
    if not editor.is_file() or not panel.is_file():
        raise RuntimeError("Faltan panel.html/editor_v1/index.html")

    shutil.copy2(src / "engine_v2.py", root / "engine_v2.py")
    shutil.copy2(src / "cdg_v2_routes.py", root / "cdg_v2_routes.py")
    vendor = root / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    (vendor / "__init__.py").write_text("# DJGABO CDG V2 vendor namespace\n", encoding="utf-8")
    shutil.copy2(src / "cdg-v2-studio.js", vendor / "cdg-v2-studio.js")
    shutil.copy2(src / "cdg-v2-lab.js", vendor / "cdg-v2-lab.js")
    shutil.copy2(src / "cdg-v2-mobile.js", vendor / "cdg-v2-mobile.js")
    qr_src = src / "vendor"
    shutil.copy2(qr_src / "jsQR.js", vendor / "jsQR.js")
    shutil.copy2(qr_src / "jsQR.LICENSE.txt", vendor / "jsQR.LICENSE.txt")
    shutil.copy2(qr_src / "jsQR.UPSTREAM.txt", vendor / "jsQR.UPSTREAM.txt")
    shutil.copy2(src / "LICENSE.nomad-karaoke.txt", root / "LICENSE.nomad-karaoke.txt")
    shutil.copy2(src / "UPSTREAM.txt", root / "UPSTREAM.txt")

    patch_html(panel, is_panel=True)
    patch_html(editor, is_panel=False)
    patch_server(root / "server.py")
    print("CLONE_PATCH=OK")
    print("ROOT=" + str(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
