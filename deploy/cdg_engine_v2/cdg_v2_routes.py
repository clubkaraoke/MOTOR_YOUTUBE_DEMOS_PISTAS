from __future__ import annotations

import json
import re
from pathlib import Path

from flask import jsonify, request, send_file

from engine_v2 import ENGINE_VERSION, EngineV2Error, build_timeline, render_cdg


def register_cdg_v2_routes(app, g):
    root = Path(g["ROOT"])
    data = Path(g["DATA"])
    v2_root = data / "v2_renders"
    v2_root.mkdir(parents=True, exist_ok=True)

    def _safe_jid(jid):
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(jid))

    def _job_dir(jid):
        p = v2_root / _safe_jid(jid)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _auth_from_body(body=None):
        body = body or {}
        token = str(body.get("token") or request.args.get("token") or "")
        g["session"](token)
        return token

    @app.get("/api/v2/healthz")
    def cdg_v2_healthz():
        return jsonify(ok=True, engine=ENGINE_VERSION, isolated=True, publish_to_dropbox=False)

    @app.get("/api/vendor/cdg-v2-studio.js")
    def cdg_v2_studio_asset():
        return send_file(root / "vendor" / "cdg-v2-studio.js", mimetype="application/javascript", conditional=True)

    @app.post("/api/v2/jobs/<jid>/timeline")
    def cdg_v2_timeline_build(jid):
        body = request.get_json(silent=True) or {}
        _auth_from_body(body)
        project = body.get("project")
        if not isinstance(project, dict):
            return jsonify(ok=False, error="Falta project JSON."), 400
        try:
            timeline = build_timeline(project, body.get("options") or {})
            out = _job_dir(jid) / "timeline_v2.json"
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(out)
            return jsonify(ok=True, timeline=timeline, engine=ENGINE_VERSION)
        except EngineV2Error as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            app.logger.exception("CDG V2 timeline %s", jid)
            return jsonify(ok=False, error="No pude construir timeline V2: " + str(e)), 500

    @app.get("/api/v2/jobs/<jid>/timeline")
    def cdg_v2_timeline_get(jid):
        _auth_from_body({})
        p = _job_dir(jid) / "timeline_v2.json"
        if not p.is_file():
            return jsonify(ok=False, error="Todavia no hay timeline V2 para este trabajo."), 404
        return send_file(p, mimetype="application/json", conditional=True)

    @app.post("/api/v2/jobs/<jid>/render")
    def cdg_v2_render(jid):
        body = request.get_json(silent=True) or {}
        _auth_from_body(body)
        project = body.get("project")
        if not isinstance(project, dict):
            return jsonify(ok=False, error="Falta project JSON."), 400
        try:
            result = render_cdg(project, _job_dir(jid), body.get("options") or {})
            return jsonify(
                ok=True,
                engine=ENGINE_VERSION,
                cdg_size=result["cdg_size"],
                warnings=result["warnings"],
                timeline=result["timeline"],
                cdg_url=f"/api/v2/jobs/{jid}/cdg",
                timeline_url=f"/api/v2/jobs/{jid}/timeline",
                publish_to_dropbox=False,
            )
        except EngineV2Error as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            app.logger.exception("CDG V2 render %s", jid)
            return jsonify(ok=False, error="Fallo el render V2: " + str(e)), 500

    @app.get("/api/v2/jobs/<jid>/cdg")
    def cdg_v2_cdg(jid):
        _auth_from_body({})
        p = _job_dir(jid) / "output_v2.cdg"
        if not p.is_file():
            return jsonify(ok=False, error="Todavia no hay CDG V2 generado."), 404
        return send_file(
            p,
            mimetype="application/octet-stream",
            conditional=True,
            download_name=f"{_safe_jid(jid)}-V2.cdg",
        )
