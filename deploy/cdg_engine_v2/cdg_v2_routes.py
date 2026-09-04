from __future__ import annotations

import json
import re
import shutil
import uuid
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
        return jsonify(ok=True, engine=ENGINE_VERSION, isolated=True, publish_to_dropbox=False, local_lab_upload=True, external_backups=False)

    @app.get("/api/vendor/cdg-v2-studio.js")
    def cdg_v2_studio_asset():
        return send_file(root / "vendor" / "cdg-v2-studio.js", mimetype="application/javascript", conditional=True)

    @app.get("/api/vendor/cdg-v2-lab.js")
    def cdg_v2_lab_asset():
        return send_file(root / "vendor" / "cdg-v2-lab.js", mimetype="application/javascript", conditional=True)

    def _upload_root():
        p = data / "v2_uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _upload_dir(upload_id):
        if not re.fullmatch(r"[0-9a-f]{32}", str(upload_id)):
            raise ValueError("Upload LAB inválido.")
        p = _upload_root() / str(upload_id)
        if not p.is_dir():
            raise ValueError("Upload LAB no encontrado o expirado.")
        return p

    @app.post("/api/v2/uploads/init")
    def cdg_v2_upload_init():
        body = request.get_json(silent=True) or {}
        try:
            g["session"](str(body.get("token") or ""), "ADMIN")
            artist = str(body.get("artist") or "").strip()
            title = str(body.get("title") or "").strip()
            voice_name = Path(str(body.get("voice_name") or "voice.mp3")).name
            inst_name = Path(str(body.get("instrumental_name") or "instrumental.wav")).name
            voice_size = int(body.get("voice_size") or 0)
            inst_size = int(body.get("instrumental_size") or 0)
            if not artist or not title:
                raise ValueError("Completa Artista y Título.")
            if Path(voice_name).suffix.lower() != ".mp3":
                raise ValueError("La VOZ debe ser MP3.")
            if Path(inst_name).suffix.lower() != ".wav":
                raise ValueError("El INSTRUMENTAL debe ser WAV.")
            if voice_size <= 0 or inst_size <= 0:
                raise ValueError("Los archivos no pueden estar vacíos.")
            if voice_size > 400 * 1024 * 1024 or inst_size > 900 * 1024 * 1024:
                raise ValueError("Archivo demasiado grande para el LAB.")
            upload_id = uuid.uuid4().hex
            up = _upload_root() / upload_id
            up.mkdir(parents=False, exist_ok=False)
            meta = {
                "artist": artist, "title": title,
                "lyrics": str(body.get("lyrics") or ""),
                "voice_duration": float(body.get("voice_duration") or 0),
                "voice_name": voice_name, "voice_size": voice_size,
                "instrumental_name": inst_name, "instrumental_size": inst_size,
                "created": g["now"](),
            }
            (up / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return jsonify(ok=True, upload_id=upload_id, chunk_size=8 * 1024 * 1024)
        except PermissionError as e:
            return jsonify(ok=False, error=str(e)), 401
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            app.logger.exception("V2 upload init")
            return jsonify(ok=False, error="No pude iniciar la subida LAB: " + str(e)), 500

    @app.route("/api/v2/uploads/<upload_id>/<kind>", methods=["POST","PUT"])
    def cdg_v2_upload_chunk(upload_id, kind):
        try:
            g["session"](str(request.headers.get("X-Session-Token") or ""), "ADMIN")
            if kind not in ("voice", "instrumental"):
                raise ValueError("Tipo de archivo LAB inválido.")
            up = _upload_dir(upload_id)
            meta = json.loads((up / "meta.json").read_text(encoding="utf-8"))
            expected = int(meta["voice_size"] if kind == "voice" else meta["instrumental_size"])
            offset = int(request.headers.get("X-Upload-Offset") or 0)
            part = up / (kind + ".part")
            current = part.stat().st_size if part.exists() else 0
            if offset != current:
                return jsonify(ok=False, error="OFFSET_MISMATCH", expected_offset=current), 409
            payload = request.get_data(cache=False)
            if not payload:
                raise ValueError("Chunk vacío.")
            if len(payload) > 9 * 1024 * 1024:
                raise ValueError("Chunk demasiado grande.")
            if current + len(payload) > expected:
                raise ValueError("La subida supera el tamaño declarado.")
            with part.open("ab") as fh:
                fh.write(payload)
                fh.flush()
            received = part.stat().st_size
            return jsonify(ok=True, received=received, expected=expected, complete=(received == expected))
        except PermissionError as e:
            return jsonify(ok=False, error=str(e)), 401
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            app.logger.exception("V2 upload chunk %s %s", upload_id, kind)
            return jsonify(ok=False, error="Falló un bloque de subida LAB: " + str(e)), 500

    @app.post("/api/v2/uploads/<upload_id>/finalize")
    def cdg_v2_upload_finalize(upload_id):
        body = request.get_json(silent=True) or {}
        try:
            g["session"](str(body.get("token") or ""), "ADMIN")
            up = _upload_dir(upload_id)
            meta = json.loads((up / "meta.json").read_text(encoding="utf-8"))
            vp, ip = up / "voice.part", up / "instrumental.part"
            if not vp.is_file() or vp.stat().st_size != int(meta["voice_size"]):
                raise ValueError("La VOZ no terminó de subir.")
            if not ip.is_file() or ip.stat().st_size != int(meta["instrumental_size"]):
                raise ValueError("El INSTRUMENTAL no terminó de subir.")
            artist, title = meta["artist"], meta["title"]
            jobs = Path(g["JOBS"])
            with g["db"]() as c:
                jid = g["next_id"](c)
            final_folder = jobs / jid
            if final_folder.exists():
                raise ValueError("Ya existe la carpeta " + jid + ".")
            stage = jobs / ("." + jid + ".v2-finalizing")
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True, exist_ok=False)
            voice_name = g["safe_name"](f"{artist} - {title} (Voz).mp3")
            inst_name = g["safe_name"](meta["instrumental_name"])
            try:
                shutil.move(str(vp), str(stage / voice_name))
                shutil.move(str(ip), str(stage / inst_name))
                info = {
                    "idTrabajo": jid, "artista": artist, "titulo": title,
                    "voz": voice_name, "instrumental": inst_name,
                    "origen": "V2_LAB", "dropbox": False, "drive": False, "sheet": False,
                    "creado": g["now"](),
                }
                (stage / "trabajo.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
                (stage / "letra_moises.txt").write_text(str(meta.get("lyrics") or ""), encoding="utf-8")
                stage.rename(final_folder)
                with g["db"]() as c:
                    t = g["now"]()
                    c.execute(
                        """INSERT INTO jobs(
                           id,artist,title,status,created,updated,voice_filename,
                           voice_original_filename,voice_drive_status,
                           instrumental_filename,lyrics_moises,dropbox_path,duration,
                           size_bytes,dropbox_status,origin,sheet_master_status
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            jid, artist, title, g["EST_P"], t, t, voice_name,
                            g["safe_name"](meta["voice_name"]), "DISABLED_V2",
                            inst_name, str(meta.get("lyrics") or ""), "",
                            float(meta.get("voice_duration") or 0),
                            (final_folder / voice_name).stat().st_size,
                            "V2_LAB_ONLY", "V2_LAB", "DISABLED_V2",
                        ),
                    )
                    g["log"](c, jid, "V2 LAB · CREAR TRABAJO CHUNKED", g["EST_P"])
                shutil.rmtree(up, ignore_errors=True)
                return jsonify(
                    ok=True, idTrabajo=jid, storage="OVH_V2_LAB",
                    folder=str(final_folder), dropbox=False, drive=False, sheet=False,
                )
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise
        except PermissionError as e:
            return jsonify(ok=False, error=str(e)), 401
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            app.logger.exception("V2 upload finalize %s", upload_id)
            return jsonify(ok=False, error="No pude finalizar la subida LAB: " + str(e)), 500

    @app.post("/api/v2/uploads/<upload_id>/cancel")
    def cdg_v2_upload_cancel(upload_id):
        try:
            g["session"](str(request.headers.get("X-Session-Token") or ""), "ADMIN")
            up = _upload_dir(upload_id)
            shutil.rmtree(up, ignore_errors=True)
            return jsonify(ok=True)
        except Exception:
            return jsonify(ok=True)

    @app.post("/api/v2/jobs/create-local")
    def cdg_v2_create_local():
        """Crea un trabajo SOLO en el almacenamiento aislado del clon.

        No Dropbox, no Drive, no Sheet maestro. La voz y el WAV quedan bajo
        DJGABO_JOBS_DIR del servicio V2 y luego el flujo normal puede mandar la
        voz a ElevenLabs Scribe v2.
        """
        token = str(request.form.get("session_token") or "")
        try:
            g["session"](token, "ADMIN")
            voice = request.files.get("voice")
            inst = request.files.get("instrumental")
            lyrics = str(request.form.get("lyrics") or "").strip()
            artist = str(request.form.get("artist") or "").strip()
            title = str(request.form.get("title") or "").strip()
            if not voice or not voice.filename:
                raise ValueError("Falta la voz MP3.")
            if not inst or not inst.filename:
                raise ValueError("Falta el instrumental WAV.")
            if Path(voice.filename).suffix.lower() != ".mp3":
                raise ValueError("La VOZ debe ser MP3.")
            if Path(inst.filename).suffix.lower() != ".wav":
                raise ValueError("El INSTRUMENTAL debe ser WAV.")
            if not artist or not title:
                try:
                    artist, title = g["master_identity"](inst.filename)
                except Exception:
                    raise ValueError("Completa Artista y Título para este trabajo LAB.")
            duration = float(request.form.get("voice_duration") or 0)
            jobs = Path(g["JOBS"])
            safe_name = g["safe_name"]
            with g["db"]() as c:
                jid = g["next_id"](c)
            final_folder = jobs / jid
            tmp_folder = jobs / ("." + jid + ".v2-uploading")
            if tmp_folder.exists():
                import shutil
                shutil.rmtree(tmp_folder, ignore_errors=True)
            tmp_folder.mkdir(parents=True, exist_ok=True)
            voice_name = safe_name(f"{artist} - {title} (Voz).mp3")
            inst_name = safe_name(Path(inst.filename).name)
            voice_path = tmp_folder / voice_name
            inst_path = tmp_folder / inst_name
            try:
                voice.save(voice_path)
                inst.save(inst_path)
                if not voice_path.is_file() or voice_path.stat().st_size <= 0:
                    raise ValueError("La voz MP3 llegó vacía.")
                if not inst_path.is_file() or inst_path.stat().st_size <= 0:
                    raise ValueError("El instrumental WAV llegó vacío.")
                meta = {
                    "idTrabajo": jid, "artista": artist, "titulo": title,
                    "voz": voice_name, "instrumental": inst_name,
                    "origen": "V2_LAB", "almacenamiento": str(final_folder),
                    "dropbox": False, "drive": False, "sheet": False,
                    "creado": g["now"](),
                }
                (tmp_folder / "trabajo.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (tmp_folder / "letra_moises.txt").write_text(lyrics, encoding="utf-8")
                if final_folder.exists():
                    import shutil
                    shutil.rmtree(final_folder, ignore_errors=True)
                tmp_folder.rename(final_folder)
                with g["db"]() as c:
                    t = g["now"]()
                    c.execute(
                        """INSERT INTO jobs(
                           id,artist,title,status,created,updated,voice_filename,
                           voice_original_filename,voice_drive_status,
                           instrumental_filename,lyrics_moises,dropbox_path,duration,
                           size_bytes,dropbox_status,origin,sheet_master_status
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            jid, artist, title, g["EST_P"], t, t, voice_name,
                            safe_name(Path(voice.filename).name), "DISABLED_V2",
                            inst_name, lyrics, "", duration, voice_path.stat().st_size,
                            "V2_LAB_ONLY", "V2_LAB", "DISABLED_V2",
                        ),
                    )
                    g["log"](c, jid, "V2 LAB · CREAR TRABAJO LOCAL", g["EST_P"])
            except Exception:
                import shutil
                shutil.rmtree(tmp_folder, ignore_errors=True)
                if final_folder.exists():
                    shutil.rmtree(final_folder, ignore_errors=True)
                raise
            return jsonify(
                ok=True, idTrabajo=jid, artista=artist, titulo=title,
                vozGuardada=voice_name, instrumentalGuardado=inst_name,
                storage="OVH_V2_LAB", folder=str(final_folder),
                dropbox=False, drive=False, sheet=False,
            )
        except PermissionError as e:
            return jsonify(ok=False, error=str(e)), 401
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            app.logger.exception("V2 local create")
            return jsonify(ok=False, error="No pude crear el trabajo LAB: " + str(e)), 500

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
