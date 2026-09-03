from __future__ import annotations

import json
import re
from pathlib import Path
from flask import Response, jsonify, request, send_file


def register_cdg_preview_routes(app, g):
    """Preview del CDG FINAL renderizado dentro del editor.

    V2:
    - CDG final real + VOZ del mismo trabajo.
    - El CDG se conserva en OVH para preview inmediato.
    - Dropbox queda solo como respaldo si la copia local no existe.
    - Sin WAV/instrumental en esta etapa.
    """

    preview_dir = Path(g["DATA"]) / "preview_cache_cdg"
    preview_dir.mkdir(parents=True, exist_ok=True)

    def _safe_jid(jid):
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(jid))

    def _preview_paths(jid):
        key = _safe_jid(jid)
        return preview_dir / (key + ".cdg"), preview_dir / (key + ".json")

    def _preview_put(jid, data, name):
        if not data:
            return None
        payload, meta = _preview_paths(jid)
        tmp = payload.with_suffix(".tmp")
        tmp.write_bytes(bytes(data))
        tmp.replace(payload)
        info = {
            "name": Path(str(name or (str(jid) + ".cdg"))).name,
            "size": payload.stat().st_size,
        }
        mt = meta.with_suffix(".tmp")
        mt.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        mt.replace(meta)
        return payload

    def _preview_get(jid):
        payload, meta = _preview_paths(jid)
        try:
            if not payload.is_file():
                return None, ""
            name = payload.name
            if meta.is_file():
                cfg = json.loads(meta.read_text(encoding="utf-8"))
                if int(cfg.get("size") or -1) != payload.stat().st_size:
                    return None, ""
                name = str(cfg.get("name") or name)
            return payload, name
        except Exception:
            return None, ""

    # Intercepta el borrado del cache "pendiente" que ocurre despues de subir
    # correctamente a Dropbox. Antes de borrarlo, conserva exactamente esos
    # mismos bytes como copia de preview. No es otro render.
    old_pop = g.get("_cdg_cache_pop")
    if old_pop and not getattr(old_pop, "_djgabo_preview_wrapped", False):
        def _preview_preserving_pop(jid):
            try:
                data, name = g["_cdg_cache_get"](jid)
                if data:
                    _preview_put(jid, data, name)
            except Exception as e:
                app.logger.warning("guardar preview CDG %s: %s", jid, e)
            return old_pop(jid)
        _preview_preserving_pop._djgabo_preview_wrapped = True
        g["_cdg_cache_pop"] = _preview_preserving_pop

    def _job(jid):
        with g["db"]() as c:
            return dict(g["jobrow"](c, str(jid)))

    def _auth():
        token = str(request.args.get("token") or "")
        g["session"](token)
        return token

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

    def _dropbox_target(job):
        return str(job.get("cdg_dropbox_id") or job.get("cdg_dropbox_path") or "").strip()

    def _dropbox_download_cdg(job):
        target = _dropbox_target(job)
        if not target:
            return None

        tok = g["dropbox_access_token"]()
        headers = {"Authorization": "Bearer " + tok}

        # Con file_id no hace falta reconstruir ninguna ruta ni namespace.
        # Si solo hay path legado, usamos el namespace de la carpeta vinculada.
        if not target.startswith("id:"):
            folder_ref = str(job.get("dropbox_folder_id") or "").strip()
            ns = None
            if folder_ref:
                try:
                    meta = g["dropbox_folder_meta"](folder_ref)
                    ns = meta.get("namespace_id") or g["dropbox_home_namespace_id"]()
                except Exception:
                    ns = g["dropbox_home_namespace_id"]()
            pr = g["dropbox_path_root_value"](ns) if ns else ""
            if pr:
                headers["Dropbox-API-Path-Root"] = pr

        headers["Dropbox-API-Arg"] = json.dumps({"path": target}, ensure_ascii=True, separators=(",", ":"))
        r = g["requests"].post(
            "https://content.dropboxapi.com/2/files/download",
            headers=headers,
            data=b"",
            timeout=120,
        )
        if r.status_code >= 400:
            raise ValueError("Dropbox download HTTP " + str(r.status_code) + ": " + (r.text or "")[:300])
        return r.content

    @app.get("/api/vendor/cdg-final-preview.js")
    def cdg_final_preview_asset():
        return send_file(g["ROOT"] / "vendor" / "cdg-final-preview.js", mimetype="application/javascript", conditional=True)

    @app.get("/api/jobs/<jid>/preview/meta")
    def cdg_preview_meta(jid):
        _auth()
        job = _job(jid)
        preview_local, _ = _preview_get(jid)
        cdg_data, _ = g["_cdg_cache_get"](jid)
        legacy_local = g["_local_cdg_path"](job)
        has_cdg = bool(preview_local or cdg_data or legacy_local or _dropbox_target(job))
        return jsonify(
            ok=True,
            job_id=str(jid),
            has_cdg=has_cdg,
            cdg_name=str(job.get("cdg_local_filename") or Path(str(job.get("cdg_dropbox_path") or "")).name or ""),
            render_status=str(job.get("render_status") or ""),
            render_progress=int(job.get("render_progress") or 0),
            cdg_url=f"/api/jobs/{jid}/preview/cdg",
            voice_url=f"/api/jobs/{jid}/voice",
        )

    @app.get("/api/jobs/<jid>/preview/cdg")
    def cdg_preview_cdg(jid):
        _auth()

        # 1) Copia persistente de preview en OVH: camino normal.
        local, name = _preview_get(jid)
        if local:
            return send_file(local, mimetype="application/octet-stream", conditional=True)

        # 2) Si justo acaba de renderizar y aun sigue en cache pendiente,
        # se persiste y se sirve sin ir a Dropbox.
        data, name = g["_cdg_cache_get"](jid)
        if data:
            _preview_put(jid, data, name)
            return _range_bytes(data, "application/octet-stream", name or f"{jid}.cdg")

        # 3) Compatibilidad con renders locales antiguos.
        job = _job(jid)
        legacy = g["_local_cdg_path"](job)
        if legacy:
            try:
                raw = legacy.read_bytes()
                _preview_put(jid, raw, legacy.name)
                return _range_bytes(raw, "application/octet-stream", legacy.name)
            except Exception as e:
                app.logger.warning("preview CDG legado %s: %s", jid, e)

        # 4) Respaldo: recuperar una vez desde Dropbox y volver a cachear en OVH.
        try:
            raw = _dropbox_download_cdg(job)
            if not raw:
                return jsonify(ok=False, error="Todavia no hay un CDG renderizado para este trabajo."), 404
            name = str(job.get("cdg_local_filename") or Path(str(job.get("cdg_dropbox_path") or "")).name or f"{jid}.cdg")
            _preview_put(jid, raw, name)
            return _range_bytes(raw, "application/octet-stream", name)
        except Exception as e:
            app.logger.warning("preview CDG %s: %s", jid, e)
            return jsonify(ok=False, error="No pude abrir el CDG final: " + str(e)), 502
