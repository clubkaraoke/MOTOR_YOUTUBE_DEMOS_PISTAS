from __future__ import annotations

import json
from pathlib import Path
from flask import Response, jsonify, redirect, request, send_file


def register_cdg_preview_routes(app, g):
    """Preview del CDG FINAL renderizado dentro del editor.

    No genera una simulacion: sirve exactamente el .cdg cacheado/publicado,
    la voz del trabajo y el WAV instrumental real.
    """

    def _job(jid):
        with g["db"]() as c:
            return dict(g["jobrow"](c, str(jid)))

    def _auth():
        token = str(request.args.get("token") or "")
        g["session"](token)
        return token

    def _local_instrumental(job):
        name = str(job.get("instrumental_filename") or "").strip()
        if not name:
            return None
        p = g["JOBS"] / str(job["id"]) / name
        return p if p.is_file() else None

    def _range_bytes(data: bytes, mime: str, name: str):
        raw = bytes(data)
        size = len(raw)
        parser = g.get("parse_http_byte_range")
        parsed = parser(request.headers.get("Range"), size) if parser else (0, max(0, size - 1), False)
        if parsed is None:
            return Response(status=416, headers={
                "Accept-Ranges": "bytes",
                "Content-Range": "bytes */" + str(size),
                "Content-Length": "0",
                "Cache-Control": "private, max-age=60",
            })
        start, end, is_range = parsed
        body = raw[start:end + 1]
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": mime,
            "Content-Disposition": 'inline; filename="' + Path(name or "archivo").name.replace('"', "") + '"',
            "Cache-Control": "private, max-age=60",
            "Content-Length": str(len(body)),
        }
        if is_range:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(body, status=206 if is_range else 200, headers=headers)

    def _dropbox_namespace(job):
        folder_ref = str(job.get("dropbox_folder_id") or "").strip()
        if folder_ref:
            try:
                meta = g["dropbox_folder_meta"](folder_ref)
                return meta.get("namespace_id") or g["dropbox_home_namespace_id"]()
            except Exception:
                pass
        return g["dropbox_home_namespace_id"]()

    def _dropbox_target(job, kind):
        if kind == "cdg":
            return str(job.get("cdg_dropbox_id") or job.get("cdg_dropbox_path") or "").strip()
        return str(job.get("instrumental_dropbox_id") or job.get("instrumental_dropbox_path") or "").strip()

    def _temporary_link(job, kind):
        target = _dropbox_target(job, kind)
        if not target:
            return ""
        ns = _dropbox_namespace(job)
        result = g["dropbox_rpc"]("files/get_temporary_link", {"path": target}, namespace_id=ns)
        return str(result.get("link") or "").strip()

    @app.get("/api/vendor/cdg-final-preview.js")
    def cdg_final_preview_asset():
        # Asset estatico: no contiene datos privados. Las rutas de medios si
        # exigen el token de sesion del trabajo.
        return send_file(g["ROOT"] / "vendor" / "cdg-final-preview.js", mimetype="application/javascript", conditional=True)

    @app.get("/api/jobs/<jid>/preview/meta")
    def cdg_preview_meta(jid):
        _auth()
        job = _job(jid)
        cdg_data, _ = g["_cdg_cache_get"](jid)
        cdg_local = g["_local_cdg_path"](job)
        wav_cache = g["_wav_cache_get"](jid)
        wav_local = _local_instrumental(job)
        has_cdg = bool(cdg_data or cdg_local or _dropbox_target(job, "cdg"))
        has_inst = bool(wav_cache or wav_local or _dropbox_target(job, "instrumental"))
        return jsonify(
            ok=True,
            job_id=str(jid),
            has_cdg=has_cdg,
            has_instrumental=has_inst,
            cdg_name=str(job.get("cdg_local_filename") or Path(str(job.get("cdg_dropbox_path") or "")).name or ""),
            instrumental_name=str(job.get("instrumental_filename") or Path(str(job.get("instrumental_dropbox_path") or "")).name or ""),
            render_status=str(job.get("render_status") or ""),
            render_progress=int(job.get("render_progress") or 0),
            cdg_url=f"/api/jobs/{jid}/preview/cdg",
            voice_url=f"/api/jobs/{jid}/voice",
            instrumental_url=f"/api/jobs/{jid}/preview/instrumental",
        )

    @app.get("/api/jobs/<jid>/preview/cdg")
    def cdg_preview_cdg(jid):
        _auth()
        data, name = g["_cdg_cache_get"](jid)
        if data:
            return _range_bytes(data, "application/octet-stream", name or f"{jid}.cdg")

        job = _job(jid)
        local = g["_local_cdg_path"](job)
        if local:
            return send_file(local, mimetype="application/octet-stream", conditional=True)

        try:
            link = _temporary_link(job, "cdg")
            if not link:
                return jsonify(ok=False, error="Todavia no hay un CDG renderizado para este trabajo."), 404
            # El CDG es pequeno. Se trae al backend para que fetch() siga siendo
            # same-origin y el reproductor pueda leer el ArrayBuffer sin CORS.
            r = g["requests"].get(link, timeout=120)
            if r.status_code >= 400:
                return jsonify(ok=False, error="Dropbox no pudo entregar el CDG final."), 502
            name = str(job.get("cdg_local_filename") or Path(str(job.get("cdg_dropbox_path") or "")).name or f"{jid}.cdg")
            return _range_bytes(r.content, "application/octet-stream", name)
        except Exception as e:
            app.logger.warning("preview CDG %s: %s", jid, e)
            return jsonify(ok=False, error="No pude abrir el CDG final: " + str(e)), 502

    @app.get("/api/jobs/<jid>/preview/instrumental")
    def cdg_preview_instrumental(jid):
        _auth()
        cached = g["_wav_cache_get"](jid)
        if cached:
            return _range_bytes(cached["data"], "audio/wav", cached.get("name") or f"{jid}.wav")

        job = _job(jid)
        local = _local_instrumental(job)
        if local:
            return send_file(local, mimetype="audio/wav", conditional=True)

        try:
            link = _temporary_link(job, "instrumental")
            if not link:
                return jsonify(ok=False, error="Este trabajo todavia no tiene WAV instrumental vinculado."), 404
            # El elemento <audio> puede seguir el enlace temporal de Dropbox y
            # conservar HTTP Range para seek sin hacer pasar el WAV completo por OVH.
            return redirect(link, code=302)
        except Exception as e:
            app.logger.warning("preview instrumental %s: %s", jid, e)
            return jsonify(ok=False, error="No pude abrir el instrumental: " + str(e)), 502
