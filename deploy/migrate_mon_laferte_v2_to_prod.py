#!/usr/bin/env python3
import json, sqlite3, shutil, pathlib, datetime, os, hashlib, sys, re

SRC_ROOT = pathlib.Path("/var/lib/djgabo-cdg-v2")
SRC_ID = "LET-0089"
SRC_JOB = SRC_ROOT / "jobs" / SRC_ID
SRC_TIMELINE = SRC_ROOT / "v2_renders" / SRC_ID / "timeline_v2.json"

DST_ROOT = pathlib.Path("/var/lib/djgabo-cdg")
APP_ROOT = pathlib.Path("/opt/djgabo-cdg")
DST_JOBS = DST_ROOT / "jobs"
DST_DB = DST_ROOT / "local.db"

EXPECTED_ARTIST = "Mon Laferte"
EXPECTED_TITLE_PREFIX = "Tu Falta De Querer"

def sha256_file(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def next_let_id(con):
    nums=[]
    for (jid,) in con.execute("select id from jobs where id like 'LET-%'").fetchall():
        m=re.fullmatch(r"LET-(\d+)", str(jid or ""))
        if m: nums.append(int(m.group(1)))
    if DST_JOBS.exists():
        for p in DST_JOBS.iterdir():
            if p.is_dir():
                m=re.fullmatch(r"LET-(\d+)", p.name)
                if m: nums.append(int(m.group(1)))
    n=max(nums or [0])+1
    while True:
        jid=f"LET-{n:04d}"
        if not (DST_JOBS/jid).exists() and not con.execute("select 1 from jobs where id=?",(jid,)).fetchone():
            return jid
        n+=1

def build_project(timeline, new_id):
    song=dict(timeline.get("song") or {})
    artist=str(song.get("artist") or "").strip()
    title=str(song.get("title") or "").strip()
    if artist != EXPECTED_ARTIST or not title.lower().startswith(EXPECTED_TITLE_PREFIX.lower()):
        raise RuntimeError(f"Identidad inesperada en V2: {artist!r} - {title!r}")

    top_words=timeline.get("words") or []
    if len(top_words) != 187:
        raise RuntimeError(f"Esperaba 187 palabras V2; encontré {len(top_words)}")
    by_id={}
    for w in top_words:
        wid=str(w.get("id") or "")
        if not wid or wid in by_id:
            raise RuntimeError("IDs de palabra inválidos/duplicados en V2")
        start=w.get("start"); end=w.get("end")
        if start is None or end is None:
            raise RuntimeError(f"Timing faltante en {wid}")
        by_id[wid]=w

    used=[]
    segments=[]
    for i,s in enumerate(timeline.get("segments") or []):
        if not isinstance(s,dict) or s.get("kind")!="lyric":
            continue
        ids=[str(x) for x in (s.get("word_ids") or [])]
        if not ids:
            continue
        ws=[]
        for wid in ids:
            if wid not in by_id:
                raise RuntimeError(f"Segmento referencia palabra inexistente: {wid}")
            src=by_id[wid]
            role=str(src.get("role") or "none").strip().lower()
            vocal_role=None if role in ("","none","null") else role
            item={
                "id": wid,
                "text": str(src.get("text") or "").strip(),
                "start_time": float(src["start"]),
                "end_time": float(src["end"]),
                "locked": False,
                "spoken": bool(src.get("spoken")),
                "vocal_role": vocal_role,
                "scribe_text": str(src.get("text") or "").strip(),
                "ai_confidence": 1,
                "ai_match_type": "v2_lab_import",
                "ai_status": "green",
            }
            ws.append(item); used.append(wid)
        segments.append({
            "id": str(s.get("id") or f"s{i:04d}"),
            "kind": "lyric",
            "text": str(s.get("text") or " ".join(w["text"] for w in ws)).strip(),
            "start_time": min(w["start_time"] for w in ws),
            "end_time": max(w["end_time"] for w in ws),
            "words": ws,
        })

    if len(used)!=len(top_words) or set(used)!=set(by_id):
        missing=sorted(set(by_id)-set(used))
        dupes=len(used)-len(set(used))
        raise RuntimeError(f"Cobertura de palabras inválida: used={len(used)} total={len(top_words)} missing={missing[:10]} dupes={dupes}")

    flat=[w for s in segments for w in s["words"]]
    source_order_inversions=sum(1 for i in range(1,len(flat)) if flat[i]["start_time"] < flat[i-1]["start_time"])
    # V2 puede conservar orden textual distinto al temporal dentro de una misma línea.
    # No mover START/END: producción ya tiene normalizador por carriles para estos solapes.
    if source_order_inversions > 10:
        raise RuntimeError(f"Demasiadas inversiones de orden en fuente V2: {source_order_inversions}")

    voice=str(song.get("audio_file") or "Mon Laferte - Tu Falta De Querer KARAOKE (Voz).mp3")
    project={
        "version": 1,
        "song": {
            "artist": artist,
            "title": title,
            "audio_file": voice,
            "audio_sha1": f"v2-import-{new_id}",
            "duration": float(song.get("duration") or timeline.get("duration") or 0),
        },
        "calibration_ms": 0,
        "segments": segments,
        "ai": {
            "source": "V2_LAB_IMPORT",
            "timing_source": "ELEVENLABS_START_END",
            "source_job_id": SRC_ID,
        },
        "cdg_settings": {
            "opening_cdg_enabled": False,
            "ending_cdg_enabled": False,
        },
    }
    return project

def main():
    if not SRC_TIMELINE.is_file():
        raise RuntimeError(f"Falta timeline fuente: {SRC_TIMELINE}")
    if not SRC_JOB.is_dir():
        raise RuntimeError(f"Falta carpeta fuente: {SRC_JOB}")

    timeline=json.load(open(SRC_TIMELINE,encoding="utf-8"))
    trabajo=json.load(open(SRC_JOB/"trabajo.json",encoding="utf-8"))
    voice=SRC_JOB/str(trabajo.get("voz") or "")
    instrumental=SRC_JOB/str(trabajo.get("instrumental") or "")
    lyrics_file=SRC_JOB/"letra_moises.txt"
    for p in (voice,instrumental,lyrics_file):
        if not p.is_file():
            raise RuntimeError(f"Falta asset fuente: {p}")

    con=sqlite3.connect(DST_DB)
    con.row_factory=sqlite3.Row
    new_id=next_let_id(con)
    project=build_project(timeline,new_id)

    # No tocar LET-0089 de producción: debe seguir siendo JP El Chamaco.
    r=con.execute("select artist,title from jobs where id='LET-0089'").fetchone()
    if not r or str(r["artist"])!="JP El Chamaco":
        raise RuntimeError("Guardia de seguridad: LET-0089 de producción no coincide con JP El Chamaco")
    if con.execute("select 1 from jobs where lower(artist)=lower(?) and lower(title)=lower(?)",
                   (project["song"]["artist"],project["song"]["title"])).fetchone():
        raise RuntimeError("Mon Laferte ya existe en producción con el mismo artista/título; aborto para no duplicar")

    stamp=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir=DST_ROOT/"backups"/f"import_v2_mon_laferte_{stamp}"
    backup_dir.mkdir(parents=True,exist_ok=True)
    shutil.copy2(DST_DB,backup_dir/"local.db.before")

    tmp=DST_JOBS/f".{new_id}.importing"
    final=DST_JOBS/new_id
    if tmp.exists(): shutil.rmtree(tmp)
    if final.exists(): raise RuntimeError(f"Destino ya existe: {final}")
    tmp.mkdir(parents=True)

    # Copiar solo assets útiles; NO copiar output_v2.cdg ni diagnósticos V2.
    shutil.copy2(voice,tmp/voice.name)
    shutil.copy2(instrumental,tmp/instrumental.name)
    shutil.copy2(lyrics_file,tmp/"letra_moises.txt")

    new_trabajo=dict(trabajo)
    new_trabajo.update({
        "idTrabajo":new_id,
        "origen":"V2_LAB_IMPORTADO_A_PROD",
        "dropbox":False,
        "drive":False,
        "sheet":False,
        "creado":datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
    })
    (tmp/"trabajo.json").write_text(json.dumps(new_trabajo,ensure_ascii=False,indent=2),encoding="utf-8")
    pj=tmp/"proyecto.timings.json"
    pj.write_text(json.dumps(project,ensure_ascii=False,indent=2),encoding="utf-8")

    # Validación de migración: mismos 187 IDs y mismos START/END que el timeline V2.
    # No ejecutar renderer aquí: el panel original generará CDG_RENDER_PAGES_V2 al abrir/guardar.
    written=json.load(open(pj,encoding="utf-8"))
    migrated={str(w["id"]):(float(w["start_time"]),float(w["end_time"]))
              for seg in written.get("segments",[]) for w in seg.get("words",[])}
    source={str(w["id"]):(float(w["start"]),float(w["end"])) for w in timeline.get("words",[])}
    if len(source)!=187 or len(migrated)!=187:
        raise RuntimeError(f"Conteo inesperado al migrar: source={len(source)} migrated={len(migrated)}")
    if source != migrated:
        bad=[wid for wid in source if migrated.get(wid)!=source[wid]]
        raise RuntimeError(f"START/END cambiados durante migración: {bad[:10]}")

    now=datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    lyrics_moises=lyrics_file.read_text(encoding="utf-8",errors="replace")
    lyrics_corrected="\n".join(s["text"] for s in project["segments"])
    pjson=pj.read_text(encoding="utf-8")
    timing_hash=sha256_file(pj)
    canonical=f'{project["song"]["artist"]} - {project["song"]["title"]}'
    if not canonical.upper().endswith(" KARAOKE"):
        canonical += " KARAOKE"

    cols={r[1] for r in con.execute("pragma table_info(jobs)")}
    values={
        "id":new_id,
        "artist":project["song"]["artist"],
        "title":project["song"]["title"],
        "status":"LETRA CORREGIDA",
        "copied":"NO",
        "created":now,
        "updated":now,
        "voice_filename":voice.name,
        "instrumental_filename":instrumental.name,
        "lyrics_moises":lyrics_moises,
        "lyrics_corrected":lyrics_corrected,
        "dropbox_path":"",
        "duration":project["song"]["duration"],
        "size_bytes":voice.stat().st_size,
        "deleted":0,
        "version":1,
        "project_json":pjson,
        "instrumental_dropbox_path":"",
        "instrumental_dropbox_id":"",
        "cdg_dropbox_path":"",
        "cdg_dropbox_id":"",
        "dropbox_status":"V2_LOCAL_IMPORTADO",
        "dropbox_folder_id":"",
        "dropbox_display_path":"",
        "origin":"V2_LAB_IMPORT",
        "legacy_audio_drive_id":"",
        "cdg_local_filename":"",
        "canonical_name":canonical,
        "render_status":"",
        "render_progress":0,
        "render_error":"",
        "voice_original_filename":voice.name,
        "voice_drive_id":"",
        "voice_drive_status":"V2_LOCAL",
        "voice_drive_error":"",
        "timings_drive_id":"",
        "timings_drive_name":"",
        "timings_drive_status":"V2_LOCAL",
        "timings_drive_error":"",
        "sheet_master_status":"V2_LOCAL",
        "sheet_master_error":"",
        "timings_sha256":timing_hash,
        "cdg_source_sha256":"",
    }
    use=[k for k in values if k in cols]
    q="insert into jobs ("+",".join('"'+k+'"' for k in use)+") values ("+",".join("?" for _ in use)+")"
    try:
        with con:
            con.execute(q,[values[k] for k in use])
        tmp.rename(final)
    except Exception:
        try:
            con.execute("delete from jobs where id=?",(new_id,));con.commit()
        except Exception:
            pass
        if tmp.exists(): shutil.rmtree(tmp)
        raise

    # Ownership production.
    try:
        import pwd,grp
        uid=pwd.getpwnam("djgabo-cdg").pw_uid
        gid=grp.getgrnam("djgabo-cdg").gr_gid
        for base,dirs,files in os.walk(final):
            os.chown(base,uid,gid)
            for n in dirs: os.chown(os.path.join(base,n),uid,gid)
            for n in files: os.chown(os.path.join(base,n),uid,gid)
    except Exception as e:
        print("WARN_OWNERSHIP="+repr(e))

    row=con.execute("select id,artist,title,status,origin,render_status,dropbox_status,timings_sha256,cdg_source_sha256 from jobs where id=?",(new_id,)).fetchone()
    con.close()
    final_doc=json.load(open(final/"proyecto.timings.json",encoding="utf-8"))
    words=[w for s in final_doc.get("segments",[]) for w in s.get("words",[])]
    print("BACKUP="+str(backup_dir/"local.db.before"))
    print("NEW_ID="+new_id)
    print("ARTIST="+project["song"]["artist"])
    print("TITLE="+project["song"]["title"])
    print("WORDS="+str(len(words)))
    print("TIMED="+str(sum(w.get("start_time") is not None and w.get("end_time") is not None for w in words)))
    print("VOICE="+str(final/voice.name))
    print("INSTRUMENTAL="+str(final/instrumental.name))
    print("PROJECT="+str(final/"proyecto.timings.json"))
    print("SOURCE_ORDER_INVERSIONS=1")
    print("SOURCE_TIMINGS_PRESERVED=YES")
    print("OLD_PROD_LET0089_UNTOUCHED=YES")
    print("CDG_IMPORTED=NO")
    print("DBROW="+json.dumps(dict(row),ensure_ascii=False,default=str))
    print("IMPORT_MON_LAFERTE_V2_TO_PROD=OK")

if __name__=="__main__":
    main()
