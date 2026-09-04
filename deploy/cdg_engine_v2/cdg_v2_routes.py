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
        return jsonify(ok=True, engine=ENGINE_VERSION, isolated=True, publish_to_dropbox=False, local_lab_upload=True, external_backups=False)

    @app.get("/api/vendor/cdg-v2-studio.js")
    def cdg_v2_studio_asset():
        return send_file(root / "vendor" / "cdg-v2-studio.js", mimetype="application/javascript", conditional=True)

    @app.get("/api/vendor/cdg-v2-lab.js")
    def cdg_v2_lab_asset():
        return send_file(root / "vendor" / "cdg-v2-lab.js", mimetype="application/javascript", conditional=True)

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
