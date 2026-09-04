#!/usr/bin/env python3
from pathlib import Path
import sys

SERVER_MARKER="DJGABO_DROPBOX_CONTENT_READ_SCOPE_V1"
ROUTES_MARKER="DJGABO_DOWNLOAD_SCOPE_GUARD_V1"
JS_MARKER="DJGABO_DOWNLOAD_SCOPE_UI_V1"

def one(t,old,new,label):
    n=t.count(old)
    if n!=1: raise RuntimeError(f"{label}: esperaba 1, encontre {n}")
    return t.replace(old,new,1)

def patch_server(p):
    t=p.read_text(encoding="utf-8")
    if SERVER_MARKER in t:
        print("SERVER_ALREADY=YES"); return

    old="""    q=urlencode({'client_id':cfg['app_key'],'response_type':'code','redirect_uri':DROPBOX_REDIRECT_URI,'token_access_type':'offline','state':state})"""
    new="""    # DJGABO_DROPBOX_CONTENT_READ_SCOPE_V1
    # Pedimos sólo el scope nuevo y conservamos los previamente autorizados.
    # El scope debe estar habilitado en Dropbox App Console > Permissions.
    q=urlencode({
        'client_id':cfg['app_key'],'response_type':'code','redirect_uri':DROPBOX_REDIRECT_URI,
        'token_access_type':'offline','state':state,
        'scope':'files.content.read','include_granted_scopes':'user'
    })"""
    t=one(t,old,new,"Dropbox OAuth incremental scope")

    old="""        j=r.json(); cfg['refresh_token']=j.get('refresh_token') or cfg.get('refresh_token')
        cfg['access_token']=j.get('access_token',''); cfg['expires_at']=time.time()+float(j.get('expires_in') or 14400)"""
    new="""        j=r.json(); cfg['refresh_token']=j.get('refresh_token') or cfg.get('refresh_token')
        cfg['access_token']=j.get('access_token',''); cfg['expires_at']=time.time()+float(j.get('expires_in') or 14400)
        cfg['granted_scopes']=str(j.get('scope') or cfg.get('granted_scopes') or '')"""
    t=one(t,old,new,"save granted scopes")

    old="""    return jsonify(ok=True,configured=bool(cfg.get('app_key') and cfg.get('app_secret')),
      connected=connected,app_key=cfg.get('app_key',DROPBOX_APP_KEY_DEFAULT),
      account_name=cfg.get('account_name',''),account_email=cfg.get('account_email',''),
      default_folder=default)"""
    new="""    scopes=set(str(cfg.get('granted_scopes') or '').split())
    return jsonify(ok=True,configured=bool(cfg.get('app_key') and cfg.get('app_secret')),
      connected=connected,app_key=cfg.get('app_key',DROPBOX_APP_KEY_DEFAULT),
      account_name=cfg.get('account_name',''),account_email=cfg.get('account_email',''),
      files_content_read=('files.content.read' in scopes),
      default_folder=default)"""
    t=one(t,old,new,"Dropbox status scope")

    p.write_text(t,encoding="utf-8")
    print("SERVER_PATCH=OK")

def patch_routes(p):
    t=p.read_text(encoding="utf-8")
    if ROUTES_MARKER in t:
        print("ROUTES_ALREADY=YES"); return

    anchor="""    def _wav_target(job):
        return str(job.get("instrumental_dropbox_id") or job.get("instrumental_dropbox_path") or "").strip()
"""
    add=anchor+"""
    # DJGABO_DOWNLOAD_SCOPE_GUARD_V1
    def _dropbox_content_read_granted():
        try:
            cfg=g["load_dropbox_cfg"]()
            return "files.content.read" in set(str(cfg.get("granted_scopes") or "").split())
        except Exception:
            return False

    def _content_read_error():
        return (
            "El WAV está en Dropbox, pero la conexión actual no tiene el permiso "
            "files.content.read. Habilita ese permiso en Dropbox App Console > "
            "Permissions y luego reconecta Dropbox desde Ajustes."
        )

    def _local_wav_available(job,jid):
        name=Path(str(job.get("instrumental_filename") or "")).name
        if name and (Path(g["JOBS"])/str(jid)/name).is_file():
            return True
        getter=g.get("_wav_cache_get")
        try:
            return bool(getter and getter(jid))
        except Exception:
            return False
"""
    t=one(t,anchor,add,"scope guard helpers")

    old="""    def _has_wav(job,jid):
        name=Path(str(job.get("instrumental_filename") or "")).name
        if name and (Path(g["JOBS"])/str(jid)/name).is_file():
            return True
        getter=g.get("_wav_cache_get")
        try:
            if getter and getter(jid): return True
        except Exception:
            pass
        return bool(_wav_target(job))
"""
    new="""    def _has_wav(job,jid):
        return _local_wav_available(job,jid) or bool(_wav_target(job))
"""
    t=one(t,old,new,"has wav")

    old="""        raw=_dropbox_download_cdg(job,target) if target else None
        if raw:
            remote=Path(str(job.get("instrumental_dropbox_path") or "")).name
            return raw,(name or remote or (str(jid)+".wav"))
        return None,""
"""
    new="""        if target and not _dropbox_content_read_granted():
            raise PermissionError(_content_read_error())
        raw=_dropbox_download_cdg(job,target) if target else None
        if raw:
            remote=Path(str(job.get("instrumental_dropbox_path") or "")).name
            return raw,(name or remote or (str(jid)+".wav"))
        return None,""
"""
    t=one(t,old,new,"wav remote scope")

    old="""        has_wav=_has_wav(job,jid)
        return jsonify(
            ok=True,job_id=str(jid),has_cdg=has_cdg,has_wav=has_wav,
"""
    new="""        has_wav=_has_wav(job,jid)
        local_wav=_local_wav_available(job,jid)
        remote_wav=bool(_wav_target(job))
        wav_downloadable=bool(local_wav or (remote_wav and _dropbox_content_read_granted()))
        return jsonify(
            ok=True,job_id=str(jid),has_cdg=has_cdg,has_wav=has_wav,
            wav_downloadable=wav_downloadable,
            download_blocker=("" if wav_downloadable or not remote_wav else _content_read_error()),
"""
    t=one(t,old,new,"meta downloadable")

    old="""    @app.get("/api/jobs/<jid>/download/wav")
    def cdg_download_wav(jid):
        _auth();job=_job(jid)
        raw,name=_download_wav_bytes(job,jid)
        if not raw:return jsonify(ok=False,error="No hay instrumental WAV disponible para este trabajo."),404
        return _attachment(raw,name or (str(jid)+".wav"),"audio/wav")
"""
    new="""    @app.get("/api/jobs/<jid>/download/wav")
    def cdg_download_wav(jid):
        _auth();job=_job(jid)
        try:
            raw,name=_download_wav_bytes(job,jid)
            if not raw:return jsonify(ok=False,error="No hay instrumental WAV disponible para este trabajo."),404
            return _attachment(raw,name or (str(jid)+".wav"),"audio/wav")
        except PermissionError as e:
            return jsonify(ok=False,error=str(e),code="DROPBOX_CONTENT_READ_REQUIRED"),409
        except Exception as e:
            app.logger.exception("download WAV %s",jid)
            return jsonify(ok=False,error="No pude preparar el WAV instrumental: "+str(e)),502
"""
    t=one(t,old,new,"wav route error guard")

    old="""    @app.get("/api/jobs/<jid>/download/zip")
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
"""
    new="""    @app.get("/api/jobs/<jid>/download/zip")
    def cdg_download_zip(jid):
        _auth();job=_job(jid)
        try:
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
        except PermissionError as e:
            return jsonify(ok=False,error=str(e),code="DROPBOX_CONTENT_READ_REQUIRED"),409
        except Exception as e:
            app.logger.exception("download ZIP %s",jid)
            return jsonify(ok=False,error="No pude preparar el ZIP: "+str(e)),502
"""
    t=one(t,old,new,"zip route error guard")

    p.write_text(t,encoding="utf-8")
    print("ROUTES_PATCH=OK")

def patch_js(p):
    t=p.read_text(encoding="utf-8")
    if JS_MARKER in t:
        print("JS_ALREADY=YES"); return

    old="""    const hasCdg=!!meta?.has_cdg,hasWav=!!meta?.has_wav;
    const bZip=q('cdgDownloadZip'),bCdg=q('cdgDownloadCdg'),bWav=q('cdgDownloadWav'),st=q('cdgDownloadStatus');
    if(bCdg)bCdg.disabled=!hasCdg;
    if(bWav)bWav.disabled=!hasWav;
    if(bZip)bZip.disabled=!(hasCdg&&hasWav);
    if(st){
      if(hasCdg&&hasWav)st.textContent='Listo: CDG y WAV instrumental disponibles.';
      else if(!hasCdg&&hasWav)st.textContent='WAV disponible · crea el CDG final para habilitar CDG y ZIP.';
      else if(hasCdg&&!hasWav)st.textContent='CDG disponible · instrumental WAV no disponible.';
      else st.textContent='Todavía no hay CDG final ni WAV instrumental disponibles.';
    }
"""
    new="""    // DJGABO_DOWNLOAD_SCOPE_UI_V1
    const hasCdg=!!meta?.has_cdg,hasWav=!!meta?.has_wav;
    const wavReady=(meta?.wav_downloadable===undefined)?hasWav:!!meta.wav_downloadable;
    const bZip=q('cdgDownloadZip'),bCdg=q('cdgDownloadCdg'),bWav=q('cdgDownloadWav'),st=q('cdgDownloadStatus');
    if(bCdg)bCdg.disabled=!hasCdg;
    if(bWav)bWav.disabled=!wavReady;
    if(bZip)bZip.disabled=!(hasCdg&&wavReady);
    if(st){
      if(meta?.download_blocker)st.textContent=meta.download_blocker;
      else if(hasCdg&&wavReady)st.textContent='Listo: CDG y WAV instrumental disponibles.';
      else if(!hasCdg&&wavReady)st.textContent='WAV disponible · crea el CDG final para habilitar CDG y ZIP.';
      else if(hasCdg&&!hasWav)st.textContent='CDG disponible · instrumental WAV no disponible.';
      else st.textContent='Todavía no hay CDG final ni WAV instrumental disponibles.';
    }
"""
    t=one(t,old,new,"download UI scope state")
    p.write_text(t,encoding="utf-8")
    print("JS_PATCH=OK")

def main():
    if len(sys.argv)!=4:
        raise SystemExit("uso: patch_production.py server.py cdg_preview_routes.py cdg-final-preview.js")
    patch_server(Path(sys.argv[1]));patch_routes(Path(sys.argv[2]));patch_js(Path(sys.argv[3]))
    print("PATCH_ALL=OK")

if __name__=="__main__": main()
