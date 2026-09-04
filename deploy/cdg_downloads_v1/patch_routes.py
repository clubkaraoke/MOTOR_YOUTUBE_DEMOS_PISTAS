#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER="DJGABO_CDG_DOWNLOADS_V1"

def one(t,old,new,label):
    n=t.count(old)
    if n!=1: raise RuntimeError(f"{label}: esperaba 1, encontre {n}")
    return t.replace(old,new,1)

def main():
    if len(sys.argv)!=2: raise SystemExit("uso: patch_routes.py cdg_preview_routes.py")
    p=Path(sys.argv[1]);t=p.read_text(encoding="utf-8")
    if MARKER in t:
        print("ROUTES_ALREADY=YES");return

    t=one(t,
      "import json\nimport re\nfrom pathlib import Path",
      "import json\nimport re\nfrom io import BytesIO\nfrom pathlib import Path\nfrom zipfile import ZIP_STORED, ZipFile",
      "imports")

    t=one(t,
      "    def _dropbox_download_cdg(job):\n        target = _dropbox_target(job)",
      "    def _dropbox_download_cdg(job, target_override=\"\"):\n        target = str(target_override or _dropbox_target(job)).strip()",
      "generic existing downloader")

    insert=r'''    # DJGABO_CDG_DOWNLOADS_V1
    def _wav_target(job):
        return str(job.get("instrumental_dropbox_id") or job.get("instrumental_dropbox_path") or "").strip()

    def _has_wav(job,jid):
        name=Path(str(job.get("instrumental_filename") or "")).name
        if name and (Path(g["JOBS"])/str(jid)/name).is_file():
            return True
        getter=g.get("_wav_cache_get")
        try:
            if getter and getter(jid): return True
        except Exception:
            pass
        return bool(_wav_target(job))

    def _download_cdg_bytes(job,jid):
        local,name=_preview_get(jid)
        if local:return local.read_bytes(),(name or local.name)
        data,name=g["_cdg_cache_get"](jid)
        if data:return bytes(data),(name or str(jid)+".cdg")
        legacy=g["_local_cdg_path"](job)
        if legacy:return legacy.read_bytes(),legacy.name
        raw=_dropbox_download_cdg(job)
        if raw:
            name=str(job.get("cdg_local_filename") or Path(str(job.get("cdg_dropbox_path") or "")).name or (str(jid)+".cdg"))
            return raw,name
        return None,""

    def _download_wav_bytes(job,jid):
        name=Path(str(job.get("instrumental_filename") or "")).name
        if name:
            local=Path(g["JOBS"])/str(jid)/name
            if local.is_file():return local.read_bytes(),name
        getter=g.get("_wav_cache_get")
        cached=getter(jid) if getter else None
        if cached and cached.get("data"):
            return bytes(cached["data"]),Path(str(cached.get("name") or name or (str(jid)+".wav"))).name
        target=_wav_target(job)
        raw=_dropbox_download_cdg(job,target) if target else None
        if raw:
            remote=Path(str(job.get("instrumental_dropbox_path") or "")).name
            return raw,(name or remote or (str(jid)+".wav"))
        return None,""

    def _attachment(data,name,mime):
        if not data:return jsonify(ok=False,error="Archivo no disponible."),404
        return send_file(BytesIO(bytes(data)),mimetype=mime,as_attachment=True,download_name=Path(str(name)).name,max_age=0)

'''
    anchor='''    @app.get("/api/vendor/cdg-final-preview.js")'''
    t=one(t,anchor,insert+anchor,"download helpers")

    old='''        has_cdg = bool(preview_local or cdg_data or legacy_local or _dropbox_target(job))
        return jsonify(
            ok=True,
            job_id=str(jid),
            has_cdg=has_cdg,
            cdg_name=str(job.get("cdg_local_filename") or Path(str(job.get("cdg_dropbox_path") or "")).name or ""),
            render_status=str(job.get("render_status") or ""),
            render_progress=int(job.get("render_progress") or 0),
            cdg_url=f"/api/jobs/{jid}/preview/cdg",
            voice_url=f"/api/jobs/{jid}/voice",
        )'''
    new='''        has_cdg=bool(preview_local or cdg_data or legacy_local or _dropbox_target(job))
        has_wav=_has_wav(job,jid)
        return jsonify(
            ok=True,job_id=str(jid),has_cdg=has_cdg,has_wav=has_wav,
            cdg_name=str(job.get("cdg_local_filename") or Path(str(job.get("cdg_dropbox_path") or "")).name or ""),
            wav_name=str(job.get("instrumental_filename") or Path(str(job.get("instrumental_dropbox_path") or "")).name or ""),
            render_status=str(job.get("render_status") or ""),render_progress=int(job.get("render_progress") or 0),
            cdg_url=f"/api/jobs/{jid}/preview/cdg",voice_url=f"/api/jobs/{jid}/voice",
            download_cdg_url=f"/api/jobs/{jid}/download/cdg",
            download_wav_url=f"/api/jobs/{jid}/download/wav",
            download_zip_url=f"/api/jobs/{jid}/download/zip",
        )'''
    t=one(t,old,new,"meta")

    routes=r'''
    @app.get("/api/jobs/<jid>/download/cdg")
    def cdg_download_file(jid):
        _auth();job=_job(jid)
        raw,name=_download_cdg_bytes(job,jid)
        if not raw:return jsonify(ok=False,error="Todavía no hay un CDG final para descargar."),404
        return _attachment(raw,name or (str(jid)+".cdg"),"application/octet-stream")

    @app.get("/api/jobs/<jid>/download/wav")
    def cdg_download_wav(jid):
        _auth();job=_job(jid)
        raw,name=_download_wav_bytes(job,jid)
        if not raw:return jsonify(ok=False,error="No hay instrumental WAV disponible para este trabajo."),404
        return _attachment(raw,name or (str(jid)+".wav"),"audio/wav")

    @app.get("/api/jobs/<jid>/download/zip")
    def cdg_download_zip(jid):
        _auth();job=_job(jid)
        cdg_raw,cdg_name=_download_cdg_bytes(job,jid)
        wav_raw,wav_name=_download_wav_bytes(job,jid)
        if not cdg_raw:return jsonify(ok=False,error="Primero crea el CDG final."),404
        if not wav_raw:return jsonify(ok=False,error="No hay instrumental WAV disponible."),404
        cdg_name=Path(str(cdg_name or (str(jid)+".cdg"))).name
        wav_name=Path(str(wav_name or (str(jid)+".wav"))).name
        buf=BytesIO()
        with ZipFile(buf,"w",compression=ZIP_STORED) as zf:
            zf.writestr(cdg_name,cdg_raw);zf.writestr(wav_name,wav_raw)
        buf.seek(0)
        return send_file(buf,mimetype="application/zip",as_attachment=True,
                         download_name=(Path(cdg_name).stem+" - CDG + Instrumental.zip"),max_age=0)
'''
    t=t.rstrip()+"\n"+routes+"\n"
    p.write_text(t,encoding="utf-8")
    print("ROUTES_PATCH=OK")

if __name__=="__main__":main()
