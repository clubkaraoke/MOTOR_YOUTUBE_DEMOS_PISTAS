#!/usr/bin/env python3
from pathlib import Path
import argparse, ast, re, shutil, json, os

def fail(msg):
    raise SystemExit("MIGRATION_FAIL: "+msg)

def extract_function_map(source):
    tree=ast.parse(source)
    lines=source.splitlines(True)
    out={}
    nodes={}
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            start=node.lineno
            if node.decorator_list:
                start=min([d.lineno for d in node.decorator_list]+[start])
            out[node.name]=''.join(lines[start-1:node.end_lineno])
            nodes[node.name]=node
    return out,nodes

def referenced_top_funcs(name,nodes,available,seen=None):
    seen=set() if seen is None else seen
    if name in seen or name not in nodes:
        return seen
    seen.add(name)
    node=nodes[name]
    for n in ast.walk(node):
        if isinstance(n,ast.Name) and n.id in available and n.id not in seen:
            referenced_top_funcs(n.id,nodes,available,seen)
    return seen

def insert_before(text, anchor, block, label):
    if block.strip() in text:
        return text
    p=text.find(anchor)
    if p<0: fail("anchor "+label)
    return text[:p]+block+"\n\n"+text[p:]

def replace_once(text, old, new, label):
    if old not in text:
        if new in text:
            return text
        fail("replace "+label)
    return text.replace(old,new,1)

def insert_after_id_before_next_label(html, element_id, snippet):
    if snippet.strip() in html:
        return html
    p=html.find('id="'+element_id+'"')
    if p<0: fail("panel id "+element_id)
    q=html.find("\n    <label",p)
    if q<0: fail("next label after "+element_id)
    return html[:q]+"\n"+snippet+html[q:]

def patch_server(root, test_root):
    server=root/'server.py'
    test_server=test_root/'server.py'
    s=server.read_text(encoding='utf-8')
    ts=test_server.read_text(encoding='utf-8')

    # imports required by the tested IA helpers.
    if "import math" not in s and ", math" not in s:
        # production currently uses a long import line ending in threading.
        if "threading" in s.split("\n",8)[0:8].__str__():
            pass
        s=re.sub(r'^(import [^\n]*threading)(\s*)$',r'\1, math\2',s,count=1,flags=re.M)
    if "from array import array" not in s:
        anchor="from contextlib import contextmanager"
        if anchor not in s: fail("server import contextmanager")
        s=s.replace(anchor,anchor+"\nfrom array import array",1)

    # IA task globals. Persistencia de sesión/Dropbox/Drive NO se toca.
    if "_AI_TASKS={}" not in s:
        anchor="_RENDER_TASKS={}"
        p=s.find(anchor)
        if p<0: fail("render task globals")
        line_end=s.find("\n",p)
        add="\n_AI_TASKS={}\n_AI_TASK_LOCK=threading.RLock()\n"
        s=s[:line_end+1]+add+s[line_end+1:]

    # Bring only pure/tested helpers from clone, never its TEST job finalizer.
    tmap,tnodes=extract_function_map(ts)
    smap,snodes=extract_function_map(s)
    core_targets=[
        '_ai_task_set','_ai_task_public','_detect_untranscribed_voice',
        '_ai_visual_units','_ai_balance_phrase','_ai_segment_scribe_words',
        '_ai_project_from_words','_project_lyrics'
    ]
    helpers=[]
    for name in core_targets:
        if name not in tmap: fail("clone helper "+name)
        if name not in smap: helpers.append(tmap[name])
    if helpers:
        s=insert_before(s,"@app.post('/api/jobs/create')","\n\n".join(helpers),"AI helpers before create")

    # Port IA BLOQUE exact dependency graph from the tested clone.
    smap,snodes=extract_function_map(s)
    if 'ai_align_block' not in smap:
        needed=referenced_top_funcs('ai_align_block',tnodes,set(tmap))
        ordered=[]
        # preserve clone source order
        for node in ast.parse(ts).body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in needed and node.name not in smap:
                ordered.append(node.name)
        blocks=[]
        for name in ordered:
            code=tmap[name]
            if name=='ai_align_block':
                code=re.sub(
                    r"\n\s*if not TEST_MODE:\n\s*return jsonify\(ok=False,error='Alineación IA de bloque disponible sólo en el clon TEST\.'\),403",
                    "",code,count=1
                )
            blocks.append(code)
        if not blocks or 'def ai_align_block' not in "\n".join(blocks):
            fail("AI block extraction")
        s=insert_before(s,"def _render_set(","\n\n".join(blocks),"AI block before render")

    # Production AI sync on an EXISTING job. This preserves original Dropbox/Drive creation.
    if "def _ai_sync_existing_task(" not in s:
        prod_ai=r'''
def _ai_sync_http_with_progress(task_id,audio_path,voice_name,lyrics,duration):
    holder={}
    def do_call():
        try:
            with Path(audio_path).open('rb') as fh:
                holder['response']=requests.post(
                    'http://127.0.0.1:8097/api/elevenlabs/transcribe',
                    files={'audio':(voice_name,fh,'audio/mpeg')},
                    data={'lyrics':lyrics or '','language_code':'spa'},
                    timeout=(30,1200)
                )
        except Exception as exc:
            holder['error']=exc
    th=threading.Thread(target=do_call,daemon=True,name='scribe-http-'+str(task_id)[:8])
    th.start()
    started=time.monotonic()
    expected=max(18.0,min(150.0,10.0+max(0.0,float(duration or 0))*0.16))
    while th.is_alive():
        elapsed=time.monotonic()-started
        frac=min(.94,elapsed/max(1.0,expected))
        pct=55+int(frac*22)
        eta=max(0,int(round(expected-elapsed)))
        _ai_task_set(task_id,status='running',progress=pct,
                     stage='ElevenLabs Scribe v2 · sincronizando…',
                     eta_seconds=eta,elapsed_seconds=round(elapsed,1),
                     estimate=True)
        th.join(timeout=1.0)
    if holder.get('error'):
        raise holder['error']
    rr=holder.get('response')
    if rr is None:
        raise ValueError('ElevenLabs no devolvió respuesta.')
    if not rr.ok:
        try: detail=rr.json().get('detail') or rr.text[:800]
        except Exception: detail=rr.text[:800]
        raise ValueError('Scribe v2 no pudo sincronizar: '+str(detail))
    return rr

def _ai_local_voice_for_job(jid,job,tmp_dir):
    if (job.get('origin') or '')=='HISTORICO_DRIVE':
        info=drive_audio_info(jid)
        suffix=Path(str(info.get('name') or '')).suffix or '.mp3'
        dst=Path(tmp_dir)/('voice'+suffix)
        with dst.open('wb') as fh:
            for part in drive_audio_iter(jid):
                fh.write(part)
        return dst,Path(str(info.get('name') or 'audio.mp3')).name
    name=str(job.get('voice_filename') or '').strip()
    if not name: raise ValueError('El trabajo no tiene pista de voz.')
    src=JOBS/jid/name
    if not src.is_file(): raise ValueError('No encuentro la pista de voz del trabajo.')
    return src,name

def _ai_sync_existing_task(task_id,jid,use_existing_lyrics=True):
    try:
        with db() as c: job=dict(jobrow(c,jid))
        master_lyrics=str(job.get('lyrics_corrected') or job.get('lyrics_moises') or '').strip() if use_existing_lyrics else ''
        _ai_task_set(task_id,status='running',progress=53,stage='Preparando voz para ElevenLabs…',
                     idTrabajo=jid,eta_seconds=None,estimate=True)
        with tempfile.TemporaryDirectory(prefix='karaoke_full_ai_') as td0:
            voice_path,voice_name=_ai_local_voice_for_job(jid,job,td0)
            duration=float(job.get('duration') or 0)
            rr=_ai_sync_http_with_progress(task_id,voice_path,voice_name,master_lyrics,duration)
            _ai_task_set(task_id,status='running',progress=80,stage='Recibiendo letra y timings…',
                         eta_seconds=8,estimate=True)
            payload=rr.json(); ai_words=payload.get('words') or []
            if not ai_words: raise ValueError('Scribe v2 no devolvió palabras con tiempos.')
            source_mode='compare_master' if master_lyrics else 'scribe_only'
            _ai_task_set(task_id,progress=86,stage='Organizando líneas y estrofas…',eta_seconds=5,estimate=True)
            project=_ai_project_from_words(
                str(job.get('artist') or ''),str(job.get('title') or ''),str(job.get('voice_filename') or voice_name),
                duration,master_lyrics if source_mode=='compare_master' else '',ai_words,source_mode,jid=jid
            )
            old_project={}
            try: old_project=json.loads(job.get('project_json') or '{}')
            except Exception: old_project={}
            if isinstance(old_project,dict) and old_project.get('cdg_settings'):
                project['cdg_settings']=old_project['cdg_settings']
            final_lyrics=master_lyrics if source_mode=='compare_master' else _project_lyrics(project)
            if not final_lyrics:
                final_lyrics=str((payload.get('scribe') or {}).get('text') or '').strip()
            if not final_lyrics: raise ValueError('Scribe v2 no devolvió letra.')
            _ai_task_set(task_id,progress=92,stage='Revisando voz sin texto…',eta_seconds=3,estimate=True)
            gaps=_detect_untranscribed_voice(voice_path,ai_words,duration)
            ai=project.setdefault('ai',{})
            ai['voice_gaps']=gaps
            ai['scribe_word_count']=len(ai_words)
            ai['coverage_check']='audio_energy_vs_scribe'
            diffs=sum(1 for w in ai_words if str(w.get('qa_status') or '').lower() not in ('','green') or str(w.get('match_type') or '').lower() in ('missing','substitution','mismatch'))
            flagged=sum(1 for w in ai_words if str(w.get('qa_status') or '').lower() not in ('','green'))
            ai['lyrics_diff_count']=diffs
            raw=json.dumps(project,ensure_ascii=False,indent=2).encode('utf-8')
            _ai_task_set(task_id,progress=97,stage='Guardando proyecto y respaldos…',eta_seconds=2,estimate=True)
            with db() as c:
                c.execute(
                    "UPDATE jobs SET project_json=?,lyrics_corrected=?,lyrics_moises=CASE WHEN COALESCE(lyrics_moises,'')='' THEN ? ELSE lyrics_moises END,status=?,updated=? WHERE id=?",
                    (json.dumps(project,ensure_ascii=False),final_lyrics,final_lyrics,EST_C,now(),jid)
                )
                log(c,jid,'ELEVENLABS · SINCRONIZAR LETRA COMPLETA',
                    source_mode+' · '+str(len(ai_words))+' palabras · diferencias='+str(diffs))
            try:
                folder=JOBS/jid
                if folder.is_dir():
                    (folder/'letra_moises.txt').write_text(final_lyrics,encoding='utf-8')
            except Exception as e:
                app.logger.warning('No pude actualizar letra local %s: %s',jid,e)
            try: schedule_timings_backup(jid,raw)
            except Exception as e: app.logger.warning('Backup timings IA pendiente %s: %s',jid,e)
            try: master_sync(jid,'Sincronización completa con ElevenLabs Scribe v2')
            except Exception as e: app.logger.warning('Sheet maestro IA pendiente %s: %s',jid,e)
            _ai_task_set(task_id,status='done',progress=100,stage='Sincronización completada',
                         eta_seconds=0,estimate=False,
                         result={'idTrabajo':jid,'words':len(ai_words),'flagged':flagged,
                                 'diff_count':diffs,'voice_gaps':len(gaps),'source_mode':source_mode})
    except Exception as e:
        app.logger.exception('AI full sync %s',jid)
        _ai_task_set(task_id,status='error',progress=100,stage='Error al sincronizar',
                     eta_seconds=None,estimate=False,error=str(e))

@app.post('/api/jobs/<jid>/ai-sync/start')
def ai_sync_existing_start(jid):
    d=request.get_json(silent=True) or {}
    try:
        session(d.get('token'))
        with db() as c: job=dict(jobrow(c,jid))
        use_existing=bool(d.get('use_existing_lyrics',True))
        with _AI_TASK_LOCK:
            for tid,t in _AI_TASKS.items():
                if str(t.get('idTrabajo') or '')==str(jid) and t.get('status') in ('queued','running'):
                    return jsonify(ok=True,task_id=tid,idTrabajo=jid,reused=True),202
        task_id=secrets.token_urlsafe(12)
        _ai_task_set(task_id,status='queued',progress=52,stage='En cola para ElevenLabs…',
                     idTrabajo=jid,eta_seconds=None,estimate=True)
        threading.Thread(target=_ai_sync_existing_task,
                         args=(task_id,str(jid),use_existing),
                         daemon=True,name='ai-full-'+str(jid)).start()
        return jsonify(ok=True,task_id=task_id,idTrabajo=jid),202
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except Exception as e:
        app.logger.exception('AI full start %s',jid)
        return jsonify(ok=False,error='No se pudo iniciar la sincronización: '+str(e)),500

@app.get('/api/ai/tasks/<task_id>')
def ai_task_status_production(task_id):
    token=request.args.get('session_token','')
    try: session(token)
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    task=_ai_task_public(task_id)
    if not task: return jsonify(ok=False,error='Tarea IA no encontrada.'),404
    return jsonify(ok=True,task=task)

@app.get('/api/vendor/jsQR.js')
def vendor_jsqr_production():
    p=ROOT/'vendor'/'jsQR.js'
    if not p.is_file(): abort(404)
    return send_file(str(p),mimetype='application/javascript',conditional=True)
'''
        s=insert_before(s,"@app.post('/api/jobs/create')",prod_ai,"production full AI route")

    # Keep original create/dropbox/drive flow; only allow Scribe to supply lyrics later
    # and accept explicit identity from the panel when UVR gives a generic filename.
    s=s.replace("        if not letra: raise ValueError('Falta la letra de Moisés.')\n","")
    old="        artist,title=master_identity(source_inst_name); voice_duration=float(request.form.get('voice_duration') or 0)"
    new="""        req_artist=str(request.form.get('artist') or '').strip(); req_title=str(request.form.get('title') or '').strip()
        if req_artist and req_title: artist,title=req_artist,req_title
        else: artist,title=master_identity(source_inst_name)
        voice_duration=float(request.form.get('voice_duration') or 0)"""
    if old in s: s=s.replace(old,new,1)
    elif "req_artist=str(request.form.get('artist')" not in s: fail("create identity override")

    # CSP: direct UVR fetch from the production browser.
    csp_old="connect-src 'self' https://content.dropboxapi.com;"
    csp_new="connect-src 'self' https://content.dropboxapi.com https://uvronline.app https://*.uvronline.app;"
    if csp_new not in s:
        if csp_old not in s: fail("production CSP connect-src")
        s=s.replace(csp_old,csp_new)

    server.write_text(s,encoding='utf-8')

def patch_panel(root,test_root):
    panel=root/'panel.html'
    test_panel=test_root/'panel.html'
    h=panel.read_text(encoding='utf-8')
    th=test_panel.read_text(encoding='utf-8')

    # Bring the tested instrumental+QR implementation only, not IA TEST Dropbox behavior.
    p0=h.find("function procesarArchivoInstrumental(file")
    p1=h.find("/**\n * Detecta el patrón",p0)
    t0=th.find("function procesarArchivoInstrumental(file,opciones)")
    t1=th.find("/**\n * Detecta el patrón",t0)
    if min(p0,p1,t0,t1)<0: fail("panel QR function anchors")
    qr_block=th[t0:t1].replace('/cdg-editor-ia/vendor/jsQR.js','/api/vendor/jsQR.js').replace('/cdg-editor-ia/','/')
    h=h[:p0]+qr_block+h[p1:]

    # Styles for QR + real progress.
    if ".qr-import-row{" not in h:
        css=r'''
.qr-import-row{display:flex;align-items:center;gap:9px;margin:7px 0 2px;min-height:30px}
.qr-import-row span{font-size:10.5px;color:var(--text-3);line-height:1.25}
.qr-import-btn{border-color:rgba(139,92,246,.45)!important;background:rgba(139,92,246,.08)!important;color:#c4b5fd!important;white-space:nowrap}
.qr-import-btn:hover{background:rgba(139,92,246,.16)!important}
.qr-import-row.qr-working span{color:var(--teal)}.qr-import-row.qr-ok span{color:#7ee7c4}.qr-import-row.qr-error span{color:var(--danger)}
.ia-source-note{margin:10px 0 8px;padding:9px 11px;border:1px solid rgba(139,92,246,.35);background:rgba(139,92,246,.08);border-radius:8px;display:flex;flex-direction:column;gap:3px;font-size:11px}
.ia-source-note b{color:#c4b5fd}.ia-source-note span{color:var(--text-3)}
.ia-progress{margin-top:12px;padding:11px 12px;border:1px solid var(--line);background:rgba(0,0,0,.18);border-radius:8px}
.ia-progress-head,.ia-progress-meta{display:flex;justify-content:space-between;align-items:center;gap:10px}
.ia-progress-head{font-size:12px;color:var(--text-2)}.ia-progress-head b{color:var(--teal);font:700 11px var(--mono)}
.ia-progress-track{height:7px;margin:8px 0 6px;background:var(--bg-base);border:1px solid var(--line);border-radius:999px;overflow:hidden}
.ia-progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#8b5cf6,#2dd4bf);transition:width .22s ease}
.ia-progress-meta{font-size:10.5px;color:var(--text-3)}#iaProgressTime{font-family:var(--mono);white-space:nowrap}
'''
        h=h.replace("</style>",css+"\n</style>",1)

    qr_voice='    <div class="qr-import-row"><button class="btn btn-sm qr-import-btn" type="button" id="btnQrVoz">▣ Pegar QR UVR</button><span id="qrVozEstado">Copia el QR de VOZ (MP3) en UVR y pulsa aquí.</span></div>'
    qr_inst='    <div class="qr-import-row"><button class="btn btn-sm qr-import-btn" type="button" id="btnQrInstrumental">▣ Pegar QR UVR</button><span id="qrInstrumentalEstado">Copia el QR del INSTRUMENTAL (WAV) en UVR y pulsa aquí.</span></div>\n    <div class="ia-source-note"><b>✨ ElevenLabs Scribe v2</b><span>Sincroniza la canción completa. Si pegas una letra maestra, la conserva y marca diferencias para revisión.</span></div>'
    if 'id="btnQrVoz"' not in h: h=insert_after_id_before_next_label(h,'dropzone',qr_voice)
    if 'id="btnQrInstrumental"' not in h: h=insert_after_id_before_next_label(h,'dropzoneInstrumental',qr_inst)

    h=h.replace('Letra preliminar (Moisés)','Letra maestra · OPCIONAL')
    h=h.replace('Pega aquí la letra generada por Moisés…','Opcional. Si ya tienes letra, se usará como referencia sin reemplazarla.')
    h=h.replace('>Enviar a corrección</button>','>✨ Crear y sincronizar con IA</button>',1)

    progress_html='''<div id="uploadProgress" class="ia-progress" style="display:none">
      <div class="ia-progress-head"><span id="iaProgressStage">Preparando…</span><b id="iaProgressPct">0%</b></div>
      <div class="ia-progress-track"><div id="iaProgressFill" class="ia-progress-fill"></div></div>
      <div class="ia-progress-meta"><span id="iaProgressDetail">Esperando…</span><span id="iaProgressTime">0.0 s</span></div>
    </div>'''
    h=re.sub(r'<div id="uploadProgress"[^>]*>.*?</div>',progress_html,h,count=1,flags=re.S)

    # The old reset destroyed progress child nodes; this was why the clone could look frozen.
    h=h.replace(
        "const up=document.getElementById('uploadProgress'); if(up){up.style.display='none';up.textContent='';}",
        "const up=document.getElementById('uploadProgress'); if(up){up.style.display='none';} const st=document.getElementById('iaProgressStage');if(st){st.style.color='';st.textContent='Preparando…';} const pf=document.getElementById('iaProgressFill');if(pf)pf.style.width='0%'; const pp=document.getElementById('iaProgressPct');if(pp)pp.textContent='0%'; const pd=document.getElementById('iaProgressDetail');if(pd)pd.textContent='Esperando…'; const pt=document.getElementById('iaProgressTime');if(pt)pt.textContent='0.0 s';"
    )

    hs=h.find("document.getElementById('btnEnviarNueva').addEventListener")
    he=h.find("/* =========================================================\n   EDITOR",hs)
    if hs<0 or he<0: fail("panel new song handler anchors")
    handler=r'''function setIaProgress(pct,stage,detail){
  const box=document.getElementById('uploadProgress'),fill=document.getElementById('iaProgressFill'),
        pctEl=document.getElementById('iaProgressPct'),stageEl=document.getElementById('iaProgressStage'),
        detailEl=document.getElementById('iaProgressDetail');
  if(box)box.style.display='block';
  const p=Math.max(0,Math.min(100,Number(pct)||0));
  if(fill)fill.style.width=p+'%';if(pctEl)pctEl.textContent=Math.round(p)+'%';
  if(stageEl){stageEl.style.color='';stageEl.textContent=stage||'Procesando…';}
  if(detailEl)detailEl.textContent=detail||'';
}
async function pollProductionIaTask(taskId){
  for(let i=0;i<2400;i++){
    const r=await fetch('/api/ai/tasks/'+encodeURIComponent(taskId)+'?session_token='+encodeURIComponent(SESSION_TOKEN),{cache:'no-store'});
    let d={};try{d=await r.json()}catch(_){}
    if(!r.ok||d.ok===false)throw new Error(d.error||('Error consultando IA '+r.status));
    const t=d.task||{};
    let detail='';
    if(t.status==='running'||t.status==='queued'){
      if(t.eta_seconds!==null&&t.eta_seconds!==undefined){
        detail=(Number(t.eta_seconds)>0?'Faltan ~'+Math.round(Number(t.eta_seconds))+' s':'Terminando…')+(t.estimate?' · estimado':'');
      }else detail='Procesando en servidor…';
    }else if(t.status==='done')detail='Completado';
    setIaProgress(t.progress??52,t.stage||'Procesando con Scribe v2…',detail);
    if(t.status==='done')return t.result||{};
    if(t.status==='error')throw new Error(t.error||'Scribe v2 falló.');
    await new Promise(res=>setTimeout(res,500));
  }
  throw new Error('La tarea IA tardó demasiado.');
}
document.getElementById('btnEnviarNueva').addEventListener('click', async function(){
  const artista=document.getElementById('inArtista').value.trim();
  const titulo=document.getElementById('inTitulo').value.trim();
  const letra=document.getElementById('inLetra').value.trim();
  if(!artista)return toast('Escribe el nombre del artista.','error');
  if(!titulo)return toast('Escribe el título de la canción.','error');
  if(!archivoSeleccionado||!archivoSeleccionado.file)return toast('Selecciona la voz MP3.','error');
  if(!instrumentalSeleccionado||!instrumentalSeleccionado.file)return toast('Selecciona el instrumental WAV.','error');
  if(!/\.mp3$/i.test(archivoSeleccionado.file.name))return toast('La VOZ debe ser MP3.','error');
  if(!/\.wav$/i.test(instrumentalSeleccionado.file.name))return toast('El INSTRUMENTAL debe ser WAV.','error');
  if(!DROPBOX_STATUS.connected)return toast('Conecta Dropbox una sola vez antes de crear el trabajo.','error');
  if(!DBX_SELECTED_FOLDER||!DBX_SELECTED_FOLDER.id)return toast('Elige la carpeta final de Dropbox.','error');

  const btn=document.getElementById('btnEnviarNueva'),timeEl=document.getElementById('iaProgressTime');
  const started=performance.now();let createdJobId='';
  const clockTimer=setInterval(()=>{if(timeEl)timeEl.textContent=((performance.now()-started)/1000).toFixed(1)+' s';},100);
  btn.disabled=true;btn.textContent='Sincronizando…';
  setIaProgress(1,'Preparando archivos…','Manteniendo Dropbox + Drive del panel original');
  try{
    let nombreInstrumental=instrumentalSeleccionado.file.name;
    if(instrumentalSeleccionado.requiereNombreMaestroManual){
      const limpiar=s=>String(s||'').replace(/[\\/:*?"<>|]+/g,' ').replace(/\s+/g,' ').trim();
      nombreInstrumental=limpiar(artista)+' - '+limpiar(titulo)+'.wav';
    }

    setIaProgress(3,'Preparando Dropbox…','Instrumental → carpeta original seleccionada');
    const prepR=await fetch('/api/instrumentals/prepare-new-direct',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      token:SESSION_TOKEN,filename:nombreInstrumental,size:instrumentalSeleccionado.file.size,
      folder_id:DBX_SELECTED_FOLDER.id,folder_display:DBX_SELECTED_FOLDER.path_display||''
    })});
    let prep={};try{prep=await prepR.json()}catch(_){}
    if(!prepR.ok||prep.ok===false)throw new Error(prep.error||'No se pudo preparar Dropbox.');

    await xhrDropboxDirecto(prep.upload_url,instrumentalSeleccionado.file,(loaded,total)=>{
      const frac=loaded/Math.max(1,total),pct=3+frac*22;
      setIaProgress(pct,'Subiendo instrumental a Dropbox…',(loaded/1048576).toFixed(1)+' / '+(total/1048576).toFixed(1)+' MB');
      btn.textContent='Dropbox '+Math.round(frac*100)+'%';
    },()=>setIaProgress(25,'✓ Instrumental en Dropbox','Preparando voz MP3…'));

    const fd=new FormData();
    fd.append('session_token',SESSION_TOKEN);fd.append('artist',artista);fd.append('title',titulo);
    fd.append('voice',archivoSeleccionado.file,archivoSeleccionado.file.name);
    fd.append('instrumental_direct','1');fd.append('instrumental_name',prep.filename||nombreInstrumental);
    fd.append('instrumental_size',String(instrumentalSeleccionado.file.size));
    fd.append('lyrics',letra);fd.append('voice_duration',String(archivoSeleccionado.duracion||0));
    fd.append('dropbox_folder_id',DBX_SELECTED_FOLDER.id);
    fd.append('dropbox_folder_display_path',DBX_SELECTED_FOLDER.path_display||'');
    fd.append('dropbox_folder_path_lower',DBX_SELECTED_FOLDER.path_lower||'');

    const res=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST','/api/jobs/create',true);
      xhr.upload.onprogress=e=>{if(e.lengthComputable){
        const frac=e.loaded/Math.max(1,e.total),pct=26+frac*18;
        setIaProgress(pct,'Subiendo voz MP3…',(e.loaded/1048576).toFixed(1)+' / '+(e.total/1048576).toFixed(1)+' MB');
        btn.textContent='Voz '+Math.round(frac*100)+'%';
      }};
      xhr.upload.onload=()=>setIaProgress(45,'Voz recibida en OVH','Guardando acapella en Google Drive…');
      xhr.onload=()=>{let d={};try{d=JSON.parse(xhr.responseText||'{}')}catch(_){}
        if(xhr.status>=200&&xhr.status<300&&d.ok!==false)resolve(d);else reject(new Error(d.error||('Error del servidor '+xhr.status)));};
      xhr.onerror=()=>reject(new Error('Se cortó la subida del MP3. El WAV ya está en Dropbox; puedes reintentar.'));
      xhr.send(fd);
    });
    createdJobId=res.idTrabajo;
    setIaProgress(52,res.vozDriveEstado==='OK'?'✓ Voz guardada en Drive':'⚠ Voz Drive pendiente','Iniciando ElevenLabs Scribe v2…');

    const sr=await fetch('/api/jobs/'+encodeURIComponent(createdJobId)+'/ai-sync/start',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:SESSION_TOKEN,use_existing_lyrics:!!letra})
    });
    let sd={};try{sd=await sr.json()}catch(_){}
    if(!sr.ok||sd.ok===false)throw new Error(sd.error||'No se pudo iniciar ElevenLabs.');
    const d=await pollProductionIaTask(sd.task_id);
    const diff=Number(d.diff_count||0),gaps=Number(d.voice_gaps||0);
    setIaProgress(100,'✓ Sincronización completada',
      d.words+' palabras'+(diff?' · '+diff+' diferencia(s) de letra para revisar':'')+(gaps?' · '+gaps+' aviso(s) de voz':''));
    if(res.dropboxFolderId)aplicarDestinoDropbox({id:res.dropboxFolderId,path_display:res.dropboxDisplayPath||res.dropboxPath||'',path_lower:res.dropboxPath||'',name:String(res.dropboxDisplayPath||res.dropboxPath||'').split('/').filter(Boolean).pop()});
    toast('Trabajo '+createdJobId+' creado · Dropbox + Drive + ElevenLabs OK');
    await cargarLista();
    setTimeout(()=>{document.getElementById('modalNueva').classList.remove('open');abrirEditor(createdJobId);},450);
  }catch(e){
    const msg=e&&e.message?e.message:String(e);
    setIaProgress(100,'ERROR',createdJobId?'El trabajo '+createdJobId+' quedó guardado. '+msg:msg);
    const stage=document.getElementById('iaProgressStage');if(stage)stage.style.color='var(--danger)';
    toast(createdJobId?'Trabajo '+createdJobId+' guardado · IA pendiente: '+msg:msg,'error');
  }finally{
    clearInterval(clockTimer);if(timeEl)timeEl.textContent=((performance.now()-started)/1000).toFixed(1)+' s';
    btn.disabled=false;btn.textContent='✨ Crear y sincronizar con IA';
  }
});

'''
    h=h[:hs]+handler+h[he:]

    if '/cdg-editor-ia' in h:
        # Production panel should never call the test prefix.
        h=h.replace('/cdg-editor-ia/','/').replace('/cdg-editor-ia','')

    panel.write_text(h,encoding='utf-8')

def patch_editor(root,test_root):
    editor=root/'editor_v1'/'index.html'
    te=(test_root/'editor_v1'/'index.html').read_text(encoding='utf-8')
    e=te.replace('/cdg-editor-ia/','/').replace('/cdg-editor-ia','')
    e=e.replace('DIAGNÓSTICO JSON · CLON IA TEST','DIAGNÓSTICO JSON')
    e=e.replace('CLON IA TEST','PANEL ORIGINAL')

    # Full-project IA for legacy/existing-lyrics projects.
    ai_block_btn='<button class="hbtn" id="btnAiBlock" title="Alinear sólo la selección con ElevenLabs Forced Alignment">✨ IA BLOQUE</button>'
    full_btn='<button class="hbtn" id="btnAiFull" title="Sincronizar toda la letra existente con ElevenLabs Scribe v2">✨ IA TODA LA LETRA</button>'
    if 'id="btnAiFull"' not in e:
        if ai_block_btn not in e: fail("editor AI block button")
        e=e.replace(ai_block_btn,ai_block_btn+full_btn,1)

    if 'id="syncIncompleteNotice"' not in e:
        if '<div id="aiQaBar" class="aiQaBar" hidden></div>' not in e: fail("editor QA bar")
        e=e.replace('<div id="aiQaBar" class="aiQaBar" hidden></div>',
                    '<div id="aiQaBar" class="aiQaBar" hidden></div>\n      <div id="syncIncompleteNotice" class="syncIncompleteNotice" hidden></div>',1)

    if '.syncIncompleteNotice{' not in e:
        css=r'''
.syncIncompleteNotice{margin:6px 0 7px;padding:7px 9px;border:1px solid rgba(232,93,93,.48);background:rgba(232,93,93,.09);border-radius:7px;font:700 10px/1.4 var(--mono);color:#f3a49d}
.syncIncompleteNotice[hidden]{display:none}
#btnAiFull{border-color:rgba(79,209,197,.5);color:#8de7df;background:rgba(79,209,197,.07)}
#btnAiFull:hover{border-color:#4fd1c5;color:#fff;background:rgba(79,209,197,.14)}
#btnAiFull.busy{opacity:.6;pointer-events:none}
'''
        e=e.replace('</style>',css+'\n</style>',1)

    old_counter='''function paintCounter(){
  const n = timedCount(), tot = S.words.length, gaps=S.doc?.ai?.voice_gaps||[];
  $("#counter").innerHTML = "<b>"+n+"</b>/"+tot+" · "+(tot?Math.round(n/tot*100):0)+"%"+(gaps.length?' · <span style="color:#F2A900">⚠ '+gaps.length+'</span>':'');
  paintAiQa();
}'''
    new_counter='''function paintSyncIncompleteNotice(){
  const el=$("#syncIncompleteNotice");if(!el)return;
  const pending=S.words.filter(w=>w.start_time===null);
  if(!pending.length){el.hidden=true;el.textContent="";return;}
  const total=S.words.length,done=total-pending.length;
  const sample=pending.slice(0,6).map(w=>w.text).join(" · ");
  el.hidden=false;
  el.textContent="⚠ Sincronización incompleta: "+done+"/"+total+" · barrido blanco desactivado · pendiente"+(pending.length===1?": ":"s: ")+sample+(pending.length>6?"…":"");
}
function paintCounter(){
  const n = timedCount(), tot = S.words.length, gaps=S.doc?.ai?.voice_gaps||[];
  $("#counter").innerHTML = "<b>"+n+"</b>/"+tot+" · "+(tot?Math.round(n/tot*100):0)+"%"+(gaps.length?' · <span style="color:#F2A900">⚠ '+gaps.length+'</span>':'');
  paintAiQa();paintSyncIncompleteNotice();
}'''
    if old_counter in e:
        e=e.replace(old_counter,new_counter,1)
    elif 'function paintSyncIncompleteNotice(){' not in e:
        fail("editor counter exact")

    if "async function aiAlignWholeProject()" not in e:
        marker="async function aiAlignSelectedBlock(){"
        p=e.find(marker)
        if p<0: fail("editor aiAlignSelectedBlock")
        full_fn=r'''async function aiAlignWholeProject(){
  const btn=$("#btnAiFull");
  const params=new URLSearchParams(location.search),jid=params.get("job")||"",token=params.get("token")||"";
  if(!jid||!token){toast("No encuentro el trabajo o la sesión.");return;}
  if(!confirm("ElevenLabs sincronizará TODA la letra existente. La letra se conserva; sólo se regeneran timings y avisos IA. ¿Continuar?"))return;
  if(btn){btn.classList.add("busy");btn.textContent="✨ IA 0%";}
  try{
    setStatus("Preparando sincronización completa con ElevenLabs…","work");
    const r=await fetch('/api/jobs/'+encodeURIComponent(jid)+'/ai-sync/start',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:token,use_existing_lyrics:true})
    });
    let d={};try{d=await r.json()}catch(_){}
    if(!r.ok||d.ok===false)throw new Error(d.error||"No se pudo iniciar ElevenLabs.");
    for(let i=0;i<2400;i++){
      const q=await fetch('/api/ai/tasks/'+encodeURIComponent(d.task_id)+'?session_token='+encodeURIComponent(token),{cache:'no-store'});
      let z={};try{z=await q.json()}catch(_){}
      if(!q.ok||z.ok===false)throw new Error(z.error||"Error consultando IA.");
      const t=z.task||{},pct=Math.round(Number(t.progress)||0);
      if(btn)btn.textContent="✨ IA "+pct+"%";
      let msg=(t.stage||"Sincronizando…");
      if(t.eta_seconds!==null&&t.eta_seconds!==undefined&&Number(t.eta_seconds)>0)msg+=" · ~"+Math.round(Number(t.eta_seconds))+" s";
      setStatus(msg,"work");
      if(t.status==="done"){
        const rr=t.result||{};
        setStatus("Sincronización completa · "+(rr.words||0)+" palabras","good");
        toast("ElevenLabs terminó · recargando proyecto");
        setTimeout(()=>location.reload(),650);
        return;
      }
      if(t.status==="error")throw new Error(t.error||"ElevenLabs falló.");
      await new Promise(res=>setTimeout(res,500));
    }
    throw new Error("La sincronización tardó demasiado.");
  }catch(err){
    setStatus("Error IA completa","bad");toast(err&&err.message?err.message:String(err));
  }finally{
    if(btn){btn.classList.remove("busy");btn.textContent="✨ IA TODA LA LETRA";}
  }
}

'''
        e=e[:p]+full_fn+e[p:]

    if 'btnAiFull").onclick' not in e:
        anchor='installRoleDelegation();'
        if anchor not in e: fail("editor installRoleDelegation")
        e=e.replace(anchor,anchor+'\nif($("#btnAiFull")) $("#btnAiFull").onclick=aiAlignWholeProject;',1)

    editor.write_text(e,encoding='utf-8')

    # Renderer changes are pure rendering/preview rules already validated in clone.
    shutil.copy2(test_root/'renderer'/'normalize.py',root/'renderer'/'normalize.py')
    shutil.copy2(test_root/'renderer'/'style.json',root/'renderer'/'style.json')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',required=True)
    ap.add_argument('--test-root',required=True)
    ap.add_argument('--vendor-source',required=True)
    args=ap.parse_args()
    root=Path(args.root); test_root=Path(args.test_root); vendor=Path(args.vendor_source)
    for p in [root/'server.py',root/'panel.html',root/'editor_v1'/'index.html',
              root/'renderer'/'normalize.py',root/'renderer'/'style.json',
              test_root/'server.py',test_root/'panel.html',test_root/'editor_v1'/'index.html',
              test_root/'renderer'/'normalize.py',test_root/'renderer'/'style.json',vendor]:
        if not p.is_file(): fail("missing "+str(p))
    (root/'vendor').mkdir(parents=True,exist_ok=True)
    shutil.copy2(vendor,root/'vendor'/'jsQR.js')
    patch_server(root,test_root)
    patch_panel(root,test_root)
    patch_editor(root,test_root)

    # Static safety checks.
    server=(root/'server.py').read_text(encoding='utf-8')
    panel=(root/'panel.html').read_text(encoding='utf-8')
    editor=(root/'editor_v1'/'index.html').read_text(encoding='utf-8')
    required_server=[
      "@app.post('/api/jobs/create')","backup_voice_to_drive(jid)","dropbox_confirm_uploaded_file",
      "@app.post('/api/render/start')","@app.post('/api/jobs/<jid>/ai-sync/start')",
      "@app.get('/api/ai/tasks/<task_id>')","@app.post('/api/jobs/<jid>/ai-align-block')",
      "https://*.uvronline.app"
    ]
    for x in required_server:
        if x not in server: fail("missing server marker "+x)
    for x in ['btnQrVoz','btnQrInstrumental','QR_UPSCALE_FALLBACK_V2','pollProductionIaTask','Manteniendo Dropbox + Drive']:
        if x not in panel: fail("missing panel marker "+x)
    if 'Dropbox desactivado en IA TEST' in panel: fail("test Dropbox disable leaked into production")
    if '/cdg-editor-ia' in panel or '/cdg-editor-ia' in editor: fail("test URL prefix leaked")
    for x in ['playHi','voice_gaps','btnAiFull','syncIncompleteNotice','IA BLOQUE']:
        if x not in editor: fail("missing editor marker "+x)
    print("MIGRATION_PATCH=OK")
    print("ROOT="+str(root))
    print("PANEL_QR=OK")
    print("ORIGINAL_DROPBOX_DRIVE=KEPT")
    print("ELEVENLABS_FULL_SYNC=OK")
    print("LEGACY_EXISTING_LYRICS_SYNC=OK")
    print("PROGRESS_WITH_ETA=OK")
    print("EDITOR_CLONE_FEATURES=OK")
    print("RENDERER_CLONE_RULES=OK")

if __name__=='__main__':
    main()
