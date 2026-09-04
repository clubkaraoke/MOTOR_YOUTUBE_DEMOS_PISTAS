#!/usr/bin/env python3
from pathlib import Path
import re, sys

MARK="DJGABO_FINAL_FIVE_V1"

def replace_once(text, old, new, label):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia, encontre {n}")
    return text.replace(old,new,1)

def patch_server(path):
    p=Path(path); t=p.read_text(encoding="utf-8")
    if "DJGABO_MASTER_FINGERPRINT_V1" in t:
        print("SERVER_ALREADY=YES"); return

    t=replace_once(
        t,
        "import base64, json, os, re, secrets, shutil, sqlite3, subprocess, sys, tempfile, time, zipfile, unicodedata, difflib, threading, math",
        "import base64, json, os, re, secrets, shutil, sqlite3, subprocess, sys, tempfile, time, zipfile, unicodedata, difflib, threading, math, hashlib",
        "server import hashlib"
    )

    anchor="""          'render_error':"TEXT DEFAULT ''",
          'voice_original_filename':"TEXT DEFAULT ''","""
    repl="""          'render_error':"TEXT DEFAULT ''",
          # DJGABO_MASTER_FINGERPRINT_V1
          'timings_sha256':"TEXT DEFAULT ''",
          'cdg_source_sha256':"TEXT DEFAULT ''",
          'voice_original_filename':"TEXT DEFAULT ''","""
    t=replace_once(t,anchor,repl,"server db fields")

    anchor="init_db()\n\ndef recover_interrupted_renders():"
    repl=r'''init_db()

# DJGABO_MASTER_FINGERPRINT_V1
def _project_sha256_obj(project):
    """Huella semantica del JSON maestro; no depende de espacios/indentacion."""
    raw=json.dumps(project,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

def _project_sha256_bytes(raw):
    try:
        obj=json.loads(bytes(raw).decode('utf-8'))
    except Exception as e:
        raise ValueError('El JSON maestro no es valido: '+str(e)) from e
    if not isinstance(obj,dict):
        raise ValueError('El JSON maestro debe ser un objeto.')
    return _project_sha256_obj(obj)

def _backfill_master_fingerprints():
    """Marca el maestro actual de trabajos existentes sin adivinar el origen de CDG antiguos."""
    try:
        with db() as c:
            rows=c.execute("SELECT id,timings_sha256 FROM jobs WHERE timings_sha256='' OR timings_sha256 IS NULL").fetchall()
            for r in rows:
                path=_timings_local_path(r['id']) if '_timings_local_path' in globals() else JOBS/str(r['id'])/'proyecto.timings.json'
                if not path.is_file():
                    continue
                try:
                    sha=_project_sha256_bytes(path.read_bytes())
                    c.execute("UPDATE jobs SET timings_sha256=? WHERE id=?",(sha,r['id']))
                except Exception:
                    continue
    except Exception as e:
        app.logger.warning('backfill huellas JSON: %s',e)

_backfill_master_fingerprints()

def recover_interrupted_renders():'''
    t=replace_once(t,anchor,repl,"server fingerprint helpers")

    old=r'''@app.post('/api/jobs/<jid>/project')
def save_project(jid):
    d=request.get_json() or {}; session(d.get('token')); proj=d.get('project')
    if not isinstance(proj,dict): return jsonify(error='Proyecto inválido'),400
    raw=json.dumps(proj,ensure_ascii=False,indent=2).encode('utf-8')
    with db() as c:
        r=jobrow(c,jid); lyrics='\n'.join(seg.get('text','') if seg.get('kind')!='break' else '' for seg in proj.get('segments',[]))
        if _sheet_managed(r): drive_bridge_call('autosave',{'id':jid,'lyrics':lyrics,'actor':'Valeria' if SESSIONS.get(d.get('token'))=='CORRECTORA' else 'Augusto'})
        c.execute('UPDATE jobs SET project_json=?,lyrics_corrected=?,updated=? WHERE id=?',(json.dumps(proj,ensure_ascii=False),lyrics,now(),jid))
    schedule_timings_backup(jid,raw)
    return jsonify(ok=True)'''
    new=r'''@app.post('/api/jobs/<jid>/project')
def save_project(jid):
    d=request.get_json() or {}; session(d.get('token')); proj=d.get('project')
    if not isinstance(proj,dict): return jsonify(error='Proyecto inválido'),400
    raw=json.dumps(proj,ensure_ascii=False,indent=2).encode('utf-8')
    master_sha=_project_sha256_obj(proj)
    # DJGABO_JSON_MASTER_SINGLE_SOURCE_V1
    # El mismo maestro que usa el Preview queda persistido atomico en OVH.
    try:
        local_path=_timings_local_path(jid); local_path.parent.mkdir(parents=True,exist_ok=True)
        tmp=local_path.with_suffix('.tmp'); tmp.write_bytes(raw); tmp.replace(local_path)
    except Exception as e:
        return jsonify(error='No se pudo conservar el JSON maestro en OVH: '+str(e)),500
    with db() as c:
        r=jobrow(c,jid); lyrics='\n'.join(seg.get('text','') if seg.get('kind')!='break' else '' for seg in proj.get('segments',[]))
        if _sheet_managed(r): drive_bridge_call('autosave',{'id':jid,'lyrics':lyrics,'actor':'Valeria' if SESSIONS.get(d.get('token'))=='CORRECTORA' else 'Augusto'})
        c.execute('UPDATE jobs SET project_json=?,lyrics_corrected=?,timings_sha256=?,updated=? WHERE id=?',(json.dumps(proj,ensure_ascii=False),lyrics,master_sha,now(),jid))
    schedule_timings_backup(jid,raw)
    return jsonify(ok=True,master_revision=master_sha[:12])'''
    t=replace_once(t,old,new,"server save_project")

    old="def _render_worker(task_id,jid,token,timings_bytes,opts):\n    try:"
    new="def _render_worker(task_id,jid,token,timings_bytes,opts,source_sha=''):\n    try:\n        source_sha=str(source_sha or _project_sha256_bytes(timings_bytes))"
    t=replace_once(t,old,new,"server render worker signature")

    old=r'''            if not data: raise ValueError('El renderer terminó pero no produjo el archivo .CDG.')
            master_stem=_provisional_master_stem(job); cdg_name=safe_name(master_stem)+'.cdg'; _cdg_cache_put(jid,data,cdg_name)
            with db() as c:
                current=jobrow(c,jid); next_status=EST_OK if _job_lyrics_ready(current) else current['status']
                c.execute("UPDATE jobs SET status=?,cdg_local_filename=?,canonical_name=CASE WHEN canonical_name='' THEN ? ELSE canonical_name END,render_status=?,render_progress=?,render_error=?,updated=? WHERE id=?",(next_status,cdg_name,master_stem,'CDG_LISTO',94,'',now(),jid)); log(c,jid,'CREAR CDG ONLINE','CDG_LISTO')'''
    new=r'''            if not data: raise ValueError('El renderer terminó pero no produjo el archivo .CDG.')
            if len(data)<24 or (len(data)%24)!=0:
                raise ValueError('El renderer produjo un CDG físicamente inválido (tamaño no múltiplo de 24 bytes).')
            # DJGABO_PREVIEW_CDG_MIRROR_GUARD_V1
            # Nunca publicar un render de una revisión que dejó de ser el maestro mientras se generaba.
            with db() as c:
                current=jobrow(c,jid)
                current_sha=str(current['timings_sha256'] or '') if 'timings_sha256' in current.keys() else ''
            if current_sha and current_sha!=source_sha:
                raise ValueError('El proyecto cambió mientras se generaba el CDG. El render anterior no se publicará; pulsa Crear / actualizar CDG final otra vez.')
            master_stem=_provisional_master_stem(job); cdg_name=safe_name(master_stem)+'.cdg'; _cdg_cache_put(jid,data,cdg_name)
            with db() as c:
                current=jobrow(c,jid); next_status=EST_OK if _job_lyrics_ready(current) else current['status']
                c.execute("UPDATE jobs SET status=?,cdg_local_filename=?,canonical_name=CASE WHEN canonical_name='' THEN ? ELSE canonical_name END,render_status=?,render_progress=?,render_error=?,cdg_source_sha256=?,updated=? WHERE id=?",(next_status,cdg_name,master_stem,'CDG_LISTO',94,'',source_sha,now(),jid)); log(c,jid,'CREAR CDG ONLINE','CDG_LISTO')'''
    t=replace_once(t,old,new,"server post-render guard")

    old=r'''    timings_bytes=request.files['timings'].read()
    if not timings_bytes: return jsonify(ok=False,error='El proyecto de timings llegó vacío.'),400
    try:
        local_path=_timings_local_path(jid); local_path.parent.mkdir(parents=True,exist_ok=True)
        tmp=local_path.with_suffix('.tmp'); tmp.write_bytes(timings_bytes); tmp.replace(local_path)
    except Exception as e: return jsonify(ok=False,error='No se pudo conservar el JSON de timings en OVH: '+str(e)),500
    schedule_timings_backup(jid,timings_bytes)'''
    new=r'''    timings_bytes=request.files['timings'].read()
    if not timings_bytes: return jsonify(ok=False,error='El proyecto de timings llegó vacío.'),400
    try:
        source_sha=_project_sha256_bytes(timings_bytes)
    except Exception as e:
        return jsonify(ok=False,error=str(e)),400
    try:
        local_path=_timings_local_path(jid); local_path.parent.mkdir(parents=True,exist_ok=True)
        tmp=local_path.with_suffix('.tmp'); tmp.write_bytes(timings_bytes); tmp.replace(local_path)
        with db() as c:
            c.execute('UPDATE jobs SET timings_sha256=?,updated=? WHERE id=?',(source_sha,now(),jid))
    except Exception as e: return jsonify(ok=False,error='No se pudo conservar el JSON de timings en OVH: '+str(e)),500
    schedule_timings_backup(jid,timings_bytes)'''
    t=replace_once(t,old,new,"server render start hash")

    old="threading.Thread(target=_render_worker,args=(task_id,jid,token,timings_bytes,opts),daemon=True,name='render-'+jid).start()"
    new="threading.Thread(target=_render_worker,args=(task_id,jid,token,timings_bytes,opts,source_sha),daemon=True,name='render-'+jid).start()"
    t=replace_once(t,old,new,"server worker args")

    p.write_text(t,encoding="utf-8")
    print("SERVER_PATCH=OK")

def patch_routes(path):
    p=Path(path); t=p.read_text(encoding="utf-8")
    if "DJGABO_CDG_STALE_META_V1" in t:
        print("ROUTES_ALREADY=YES"); return
    old=r'''        has_cdg=bool(preview_local or cdg_data or legacy_local or _dropbox_target(job))
        has_wav=_has_wav(job,jid)'''
    new=r'''        has_cdg=bool(preview_local or cdg_data or legacy_local or _dropbox_target(job))
        # DJGABO_CDG_STALE_META_V1
        master_sha=str(job.get("timings_sha256") or "")
        cdg_sha=str(job.get("cdg_source_sha256") or "")
        cdg_current=bool(has_cdg and master_sha and cdg_sha and master_sha==cdg_sha)
        cdg_stale=bool(has_cdg and not cdg_current)
        has_wav=_has_wav(job,jid)'''
    t=replace_once(t,old,new,"routes stale calc")
    old=r'''            ok=True,job_id=str(jid),has_cdg=has_cdg,has_wav=has_wav,
            wav_downloadable=wav_downloadable,'''
    new=r'''            ok=True,job_id=str(jid),has_cdg=has_cdg,has_wav=has_wav,
            cdg_current=cdg_current,cdg_stale=cdg_stale,
            wav_downloadable=wav_downloadable,'''
    t=replace_once(t,old,new,"routes stale meta")
    p.write_text(t,encoding="utf-8")
    print("ROUTES_PATCH=OK")

def patch_preview_js(path):
    p=Path(path); t=p.read_text(encoding="utf-8")
    if "DJGABO_CDG_STALE_UI_V1" in t:
        print("PREVIEW_JS_ALREADY=YES"); return

    old="const hasCdg=!!meta?.has_cdg,hasWav=!!meta?.has_wav;"
    new="const hasCdg=!!meta?.has_cdg && meta?.cdg_current!==false,hasWav=!!meta?.has_wav; // DJGABO_CDG_STALE_UI_V1"
    t=replace_once(t,old,new,"preview downloads current only")

    old=r'''      const meta=await mr.json(); if(!mr.ok||meta.ok===false)throw new Error(meta.error||'No pude leer el estado del render.');
      updateDownloads(meta);
      if(!meta.has_cdg){'''
    new=r'''      const meta=await mr.json(); if(!mr.ok||meta.ok===false)throw new Error(meta.error||'No pude leer el estado del render.');
      P.meta=meta;
      updateDownloads(meta);
      if(!meta.has_cdg){'''
    t=replace_once(t,old,new,"preview keep meta")

    old="setStatus('CDG final real cargado · Voz lista para comprobar sincronización.','ok');"
    new=r'''// DJGABO_CDG_STALE_UI_V1
      if(meta.cdg_stale){
        const failed=String(meta.render_status||'').toUpperCase()==='ERROR';
        setStatus(failed
          ? '⚠ CDG DESACTUALIZADO · el último render falló. El archivo mostrado corresponde a una versión anterior del Preview actual.'
          : '⚠ CDG DESACTUALIZADO · corresponde a una versión anterior del proyecto. Pulsa «Crear / actualizar CDG final».','warn');
      }else{
        setStatus('CDG final real cargado · corresponde al JSON maestro actual.','ok');
      }'''
    t=replace_once(t,old,new,"preview stale status")

    p.write_text(t,encoding="utf-8")
    print("PREVIEW_JS_PATCH=OK")

def patch_editor(path):
    p=Path(path); t=p.read_text(encoding="utf-8")
    if "DJGABO_STICKY_AI_BOTTOM_V1" in t and "DJGABO_JSON_SINGLE_SNAPSHOT_V1" in t:
        print("EDITOR_ALREADY=YES"); return

    ai_block=r'''      <div id="aiToolsBar">
        <div class="aiPrimary">
          <button class="hbtn" id="btnAiBlock" title="Alinear solo la seleccion con ElevenLabs Forced Alignment">✨ IA BLOQUE</button>
          <button class="hbtn" id="btnAiFull" title="Sincronizar toda la letra existente con ElevenLabs Scribe v2">✨ IA TODA LA LETRA</button>
        </div>
        <div class="aiSecondary">
          <button id="btnResync" type="button">↻ RESINCRONIZAR SELECCIÓN</button>
          <button class="hbtn" id="btnDiagJson" title="Abrir diagnostico JSON de timings, voz sin texto e instrumentales">📋 DIAGNÓSTICO</button>
        </div>
      </div>
'''
    if t.count(ai_block)!=1: raise RuntimeError(f"editor ai block: {t.count(ai_block)}")
    t=t.replace(ai_block,"",1)
    target='      <div id="lyricsInner"></div>\n'
    moved=target+'''      <!-- DJGABO_STICKY_AI_BOTTOM_V1: herramientas siempre visibles al pie mientras se desplaza la letra -->
'''+ai_block
    t=replace_once(t,target,moved,"editor move ai bottom")

    old=r'''#aiToolsBar{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:7px 8px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.015)
}'''
    new=r'''/* DJGABO_STICKY_AI_BOTTOM_V1 */
#aiToolsBar{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  position:sticky;bottom:0;z-index:7;
  padding:7px 8px;border-top:1px solid var(--line);border-bottom:0;
  background:var(--bg-elevated);box-shadow:0 -8px 18px rgba(0,0,0,.28)
}'''
    t=replace_once(t,old,new,"editor sticky css")

    old=r'''  try{
    await panelSaveProject(buildExport());
    const fd=new FormData();fd.append("timings",new Blob([JSON.stringify(buildExport())],{type:"application/json"}),"proyecto.timings.json");fd.append("job_id",PANEL_JOB_ID);fd.append("session_token",PANEL_TOKEN);'''
    new=r'''  try{
    // DJGABO_JSON_SINGLE_SNAPSHOT_V1
    // Guardado y renderer reciben EXACTAMENTE el mismo snapshot del maestro.
    const master=buildExport();
    await panelSaveProject(master,true);
    const fd=new FormData();fd.append("timings",new Blob([JSON.stringify(master)],{type:"application/json"}),"proyecto.timings.json");fd.append("job_id",PANEL_JOB_ID);fd.append("session_token",PANEL_TOKEN);'''
    t=replace_once(t,old,new,"editor single snapshot")

    old=r'''async function panelSaveProject(payload){
  if(!PANEL_JOB_ID||!payload)return;
  try{
    await fetch('/api/jobs/'+encodeURIComponent(PANEL_JOB_ID)+'/project',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:PANEL_TOKEN,project:payload})
    });
  }catch(e){ console.warn('Autoguardado servidor',e); }
}'''
    new=r'''async function panelSaveProject(payload,strict=false){
  if(!PANEL_JOB_ID||!payload)return;
  try{
    const r=await fetch('/api/jobs/'+encodeURIComponent(PANEL_JOB_ID)+'/project',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:PANEL_TOKEN,project:payload})
    });
    if(!r.ok){
      let j={};try{j=await r.json()}catch(_){}
      throw new Error(j.error||('No se pudo guardar JSON maestro · HTTP '+r.status));
    }
    return await r.json().catch(()=>({ok:true}));
  }catch(e){
    if(strict)throw e;
    console.warn('Autoguardado servidor',e);
  }
}'''
    t=replace_once(t,old,new,"editor strict master save")

    p.write_text(t,encoding="utf-8")
    print("EDITOR_PATCH=OK")

def patch_normalizer(path):
    p=Path(path); t=p.read_text(encoding="utf-8")
    if "DJGABO_OVERLAP_LANES_V1" in t:
        print("NORMALIZER_ALREADY=YES"); return

    # Add optional lanes to Normalized dataclass immediately before warnings.
    old="    warnings: list[Warning_] = field(default_factory=list)"
    new="    # DJGABO_OVERLAP_LANES_V1: fallback físico por slot cuando líneas distintas se solapan en tiempo.\n    lyric_lanes: list[dict] = field(default_factory=list)\n    warnings: list[Warning_] = field(default_factory=list)"
    t=replace_once(t,old,new,"normalizer dataclass lanes")

    # Insert lane builder before packet budget checker.
    anchor="\ndef check_packet_budget(visual: list[list[dict]], font: ImageFont.FreeTypeFont,"
    helper=r'''
# DJGABO_OVERLAP_LANES_V1
def build_overlap_lyric_lanes(visual: list[list[dict]], style: dict, base_row: int,
                              line_draw_sync: list[int], line_erase_sync: list[int],
                              uppercase: bool) -> list[dict]:
    """Convierte cada slot físico en un lyric set independiente.

    Se usa SOLO cuando el stream global retrocede entre líneas/páginas.
    No mueve START/END. Nomad LINE_DELAYED procesa cada slot como una pista
    independiente, algo que cdgmaker soporta de forma nativa.
    """
    lpp=max(2,min(8,int(style.get("lines_per_page",6))))
    lanes=[]
    for slot in range(lpp):
        entries=[(li,line) for li,line in enumerate(visual) if line and (li % lpp)==slot]
        if not entries:
            continue
        lane_text=[]; lane_sync=[]; lane_modes=[]; lane_draw=[]; lane_erase=[]
        for ei,(li,line) in enumerate(entries):
            # Próxima entrada de ESTE MISMO slot. Solapes con otros slots son válidos.
            next_lane_start=None
            for _,nl in entries[ei+1:]:
                if nl:
                    next_lane_start=float(nl[0]["start_time"]); break
            parts=[]
            if line[0].get("_inst"):
                for w in line:
                    txt=w["text"].upper() if uppercase else w["text"]
                    parts.append(txt.replace(" ","_"))
                    lane_sync.append(int(round(float(w["start_time"])*100)))
                    lane_modes.append(4 if w.get("_label") else 0)
                body=" ".join(parts)
            else:
                chain=[]
                for wi,w in enumerate(line):
                    txt=(w["text"].upper() if uppercase else w["text"]).replace(" ","_")
                    chain.append(txt)
                    lane_sync.append(int(round(float(w["start_time"])*100)))
                    role=w.get("vocal_role")
                    mode=1 if role=="female" else 2 if role=="male" else 3 if role=="duet" else 0
                    lane_modes.append(mode)
                    end=w.get("end_time")
                    if wi+1<len(line):
                        next_start=float(line[wi+1]["start_time"])
                    else:
                        next_start=next_lane_start
                    if (end is not None and float(end)>float(w["start_time"]) and
                        (next_start is None or float(end)<=float(next_start))):
                        chain.append("_")
                        lane_sync.append(int(round(float(end)*100)))
                        lane_modes.append(mode)
                body="/".join(chain)
            if line[0].get("_label"):
                body="2|"+body
            elif line[0].get("_dotline"):
                body="3|"+body
            lane_text.append(body)
            lane_draw.append(int(line_draw_sync[li]) if li<len(line_draw_sync) else 0)
            lane_erase.append(int(line_erase_sync[li]) if li<len(line_erase_sync) else 0)

        for i in range(1,len(lane_sync)):
            if lane_sync[i] < lane_sync[i-1]:
                raise NormalizeError(
                    f"Solape no renderizable dentro del mismo slot {slot+1}: "
                    f"{lane_sync[i-1]} seguido de {lane_sync[i]}. "
                    "No se movieron START/END; revisa sólo esas palabras."
                )
        lanes.append({
            "slot":slot+1,
            "row":base_row + slot*int(style["line_tile_height"]),
            "text":"\n".join(lane_text),
            "sync":lane_sync,
            "syllable_modes":lane_modes,
            "line_draw":lane_draw,
            "line_erase":lane_erase,
        })
    return lanes

'''
    if anchor not in t: raise RuntimeError("normalizer check_packet anchor missing")
    t=t.replace(anchor,"\n"+helper+anchor,1)

    # Replace global invariant + row computation.
    old=r'''    # Invariante barata que atrapa cualquier bloque mal colocado: cdgmaker
    # empareja sílabas consecutivas, así que un sync fuera de orden produce
    # barridos de duración negativa y avisos incomprensibles.
    for i in range(1, len(sync)):
        if sync[i] < sync[i - 1]:
            raise NormalizeError(
                f"Puntos de sincronización desordenados en la posición {i}: "
                f"{sync[i-1]} seguido de {sync[i]}. Es un fallo del normalizador, "
                f"no del proyecto."
            )

    rows_used = style["lines_per_page"] * style["line_tile_height"]
    row = max(1, (CDG_ROWS - rows_used) // 2 + int(style.get("lyric_y_offset", 0)))
    row = max(1, min(CDG_ROWS - rows_used, row))'''
    new=r'''    # DJGABO_OVERLAP_LANES_V1
    # Un START menor al anterior entre líneas distintas puede ser un solape vocal
    # legítimo. No se corrigen timings: se separan físicamente por slot/lane.
    global_backwards=any(sync[i] < sync[i-1] for i in range(1,len(sync)))

    rows_used = style["lines_per_page"] * style["line_tile_height"]
    row = max(1, (CDG_ROWS - rows_used) // 2 + int(style.get("lyric_y_offset", 0)))
    row = max(1, min(CDG_ROWS - rows_used, row))
    lyric_lanes=[]
    if global_backwards:
        lyric_lanes=build_overlap_lyric_lanes(
            visual,style,row,line_draw_sync,line_erase_sync,upper
        )'''
    t=replace_once(t,old,new,"normalizer global invariant")

    old="        screen_clear_sync=screen_clear_sync,\n        warnings=warns,"
    new="        screen_clear_sync=screen_clear_sync,\n        lyric_lanes=lyric_lanes,\n        warnings=warns,"
    t=replace_once(t,old,new,"normalizer return lanes")

    # Replace single TOML lyrics block with conditional multi-lane output.
    old=r'''    L += [
        "[[lyrics]]",
        "singer = 1",
        f"row = {n.row}",
        f"line_tile_height = {n.line_tile_height}",
        f"lines_per_page = {n.lines_per_page}",
        "explicit_timeline = false",  # CDG final: Nomad LINE_DELAYED controla draw/erase
        f"line_draw = [{', '.join(str(x) for x in n.line_draw_sync)}]",
        f"line_erase = [{', '.join(str(x) for x in n.line_erase_sync)}]",
        f"sync = [{', '.join(str(s) for s in n.sync)}]",
        f"syllable_modes = [{', '.join(str(m) for m in n.syllable_modes)}]",
        # sin salto final: TOML conservaría una línea vacía de más y cdgmaker
        # la contaría como una línea de letra fantasma
        'text = """',
        n.text + '\\',
        '"""',
        "",
    ]'''
    new=r'''    # DJGABO_OVERLAP_LANES_V1
    # Camino normal queda idéntico. Sólo proyectos con solapes entre slots
    # usan varios [[lyrics]], todos bajo el mismo Nomad LINE_DELAYED.
    lyric_blocks=n.lyric_lanes or [{
        "row":n.row,"lines_per_page":n.lines_per_page,
        "line_draw":n.line_draw_sync,"line_erase":n.line_erase_sync,
        "sync":n.sync,"syllable_modes":n.syllable_modes,"text":n.text,
    }]
    for lane in lyric_blocks:
        L += [
            "[[lyrics]]",
            "singer = 1",
            f"row = {lane['row']}",
            f"line_tile_height = {n.line_tile_height}",
            f"lines_per_page = {lane.get('lines_per_page',1)}",
            "explicit_timeline = false",  # Nomad LINE_DELAYED conserva autoridad
            f"line_draw = [{', '.join(str(x) for x in lane.get('line_draw',[]))}]",
            f"line_erase = [{', '.join(str(x) for x in lane.get('line_erase',[]))}]",
            f"sync = [{', '.join(str(s) for s in lane['sync'])}]",
            f"syllable_modes = [{', '.join(str(m) for m in lane['syllable_modes'])}]",
            'text = """',
            lane['text'] + '\\',
            '"""',
            "",
        ]'''
    t=replace_once(t,old,new,"normalizer toml lanes")

    p.write_text(t,encoding="utf-8")
    print("NORMALIZER_PATCH=OK")

def main():
    if len(sys.argv)!=6:
        raise SystemExit("uso: patch_production.py EDITOR SERVER NORMALIZER ROUTES PREVIEW_JS")
    patch_editor(sys.argv[1])
    patch_server(sys.argv[2])
    patch_normalizer(sys.argv[3])
    patch_routes(sys.argv[4])
    patch_preview_js(sys.argv[5])
    print("PATCH_ALL=OK")

if __name__=="__main__":
    main()
