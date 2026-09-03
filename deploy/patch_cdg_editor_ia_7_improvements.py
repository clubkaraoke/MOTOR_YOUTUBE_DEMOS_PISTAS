#!/usr/bin/env python3
from pathlib import Path

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
PANEL=ROOT/'panel.html'
EDITOR=ROOT/'editor_v1'/'index.html'
NORMALIZE=ROOT/'renderer'/'normalize.py'

def must_replace(text, old, new, label, count=1):
    if old not in text:
        raise SystemExit('PATCH FAIL '+label)
    return text.replace(old,new,count)

# ---------------- server.py ----------------
s=SERVER.read_text(encoding='utf-8')
s=s.replace(
"import base64, json, os, re, secrets, shutil, sqlite3, subprocess, sys, tempfile, time, zipfile, unicodedata, difflib, threading",
"import base64, json, os, re, secrets, shutil, sqlite3, subprocess, sys, tempfile, time, zipfile, unicodedata, difflib, threading, math"
)
if "from array import array" not in s:
    s=s.replace("from contextlib import contextmanager","from contextlib import contextmanager\nfrom array import array")
if "_AI_TASKS={}" not in s:
    s=s.replace(
        "_RENDER_TASKS={}\n_RENDER_LOCK=threading.RLock()",
        "_RENDER_TASKS={}\n_RENDER_LOCK=threading.RLock()\n_AI_TASKS={}\n_AI_TASK_LOCK=threading.RLock()\n_AI_RESERVED_IDS=set()"
    )

if "def _detect_untranscribed_voice(" not in s:
    marker="def _ai_segment_scribe_words(items):"
    helpers=r'''
def _ai_task_set(task_id, **fields):
    with _AI_TASK_LOCK:
        task=_AI_TASKS.setdefault(str(task_id),{
            'id':str(task_id),'status':'queued','progress':0,'stage':'Preparando…',
            'created':time.time(),'updated':time.time()
        })
        task.update(fields); task['updated']=time.time()
        if len(_AI_TASKS)>40:
            old=sorted(_AI_TASKS.items(),key=lambda kv:kv[1].get('updated',0))[:-30]
            for key,_ in old: _AI_TASKS.pop(key,None)
        return dict(task)

def _ai_task_public(task_id):
    with _AI_TASK_LOCK:
        task=dict(_AI_TASKS.get(str(task_id)) or {})
    if not task: return None
    return {k:v for k,v in task.items() if k not in ('voice_path','inst_path','tmp_folder')}

def _detect_untranscribed_voice(audio_path, words, duration=0.0):
    """QA conservador para acapella: energía vocal sin ninguna palabra de Scribe."""
    try:
        proc=subprocess.run([
            'ffmpeg','-v','error','-i',str(audio_path),'-ac','1','-ar','8000',
            '-f','s16le','-acodec','pcm_s16le','pipe:1'
        ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180,check=True)
        pcm=array('h'); pcm.frombytes(proc.stdout)
        if sys.byteorder!='little': pcm.byteswap()
        if not pcm: return []
    except Exception as e:
        app.logger.warning('QA voz no transcrita: no pude decodificar %s: %s',audio_path,e)
        return []

    sr=8000; frame_n=int(sr*.20)
    dbs=[]
    for i in range(0,len(pcm)-frame_n+1,frame_n):
        fr=pcm[i:i+frame_n]
        if not fr: continue
        rms=math.sqrt(sum(float(x)*float(x) for x in fr)/len(fr))/32768.0
        dbs.append(20.0*math.log10(max(rms,1e-7)))
    if not dbs: return []

    sorted_db=sorted(dbs)
    noise=sorted_db[max(0,min(len(sorted_db)-1,int(len(sorted_db)*.20)))]
    threshold=max(-52.0,min(-30.0,noise+11.0))

    spans=[]
    for w in words or []:
        try:
            a=float(w.get('start')); b=float(w.get('end'))
        except Exception:
            continue
        spans.append((max(0.0,a-.28),max(a,b)+.32))
    spans.sort()

    def covered(t):
        lo=0; hi=len(spans)
        while lo<hi:
            mid=(lo+hi)//2
            if spans[mid][1] < t: lo=mid+1
            else: hi=mid
        return lo<len(spans) and spans[lo][0] <= t <= spans[lo][1]

    active=[]
    for idx,dbv in enumerate(dbs):
        a=idx*.20; b=a+.20; mid=(a+b)/2
        if dbv>=threshold and not covered(mid):
            active.append((a,b,dbv))
    if not active: return []

    merged=[]
    for a,b,dbv in active:
        if not merged or a-merged[-1]['end']>.65:
            merged.append({'start':a,'end':b,'active':.20,'peak_db':dbv,'sum_db':dbv})
        else:
            m=merged[-1]; m['end']=b; m['active']+=.20
            m['peak_db']=max(m['peak_db'],dbv); m['sum_db']+=dbv

    out=[]; total_dur=float(duration or (len(pcm)/sr))
    for m in merged:
        dur=m['end']-m['start']
        if dur<1.20 or m['active']<.80: continue
        score=min(1.0,(m['active']/max(.2,dur))*.65 + max(0.0,(m['peak_db']-threshold)/18.0)*.35)
        if score<.28: continue
        out.append({
            'start':round(max(0.0,m['start']),3),
            'end':round(min(total_dur,m['end']),3),
            'duration':round(min(total_dur,m['end'])-max(0.0,m['start']),3),
            'kind':'untranscribed_voice','score':round(score,3),
            'peak_db':round(m['peak_db'],1),'threshold_db':round(threshold,1),
        })
    return out[:24]

def _finalize_ai_job(jid,artist,title,voice_name,voice_original,inst_name,duration,final_folder,tmp_folder,project,final_lyrics):
    meta={'idTrabajo':jid,'artista':artist,'titulo':title,'voz':voice_name,'instrumental':inst_name,
          'modo':'IA_TEST_LOCAL','creado':now()}
    (tmp_folder/'trabajo.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    (tmp_folder/'letra_moises.txt').write_text(final_lyrics,encoding='utf-8')
    if final_folder.exists(): shutil.rmtree(final_folder,ignore_errors=True)
    tmp_folder.rename(final_folder)
    with db() as c:
        t=now()
        c.execute('''INSERT INTO jobs(
          id,artist,title,status,created,updated,voice_filename,voice_original_filename,
          voice_drive_status,instrumental_filename,lyrics_moises,lyrics_corrected,duration,size_bytes,
          project_json,dropbox_status,timings_drive_status,sheet_master_status,origin
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (jid,artist,title,EST_C,t,t,voice_name,Path(voice_original).name,'TEST_LOCAL',inst_name,
         final_lyrics,final_lyrics,duration,(final_folder/voice_name).stat().st_size,
         json.dumps(project,ensure_ascii=False),'TEST_LOCAL','TEST_LOCAL','TEST_LOCAL','IA_TEST'))
        log(c,jid,'SCRIBE V2 · CREAR Y SINCRONIZAR','scribe_only')

def _run_ai_create_task(task_id,jid,artist,title,voice_name,voice_original,inst_name,duration,tmp_folder,final_folder):
    try:
        voice_path=tmp_folder/voice_name
        _ai_task_set(task_id,status='running',progress=42,stage='Enviando voz a ElevenLabs Scribe v2…')
        started=time.monotonic()
        with voice_path.open('rb') as fh:
            rr=requests.post(
                'http://127.0.0.1:8097/api/elevenlabs/transcribe',
                files={'audio':(voice_name,fh,'audio/mpeg')},
                data={'lyrics':'','language_code':'spa'},timeout=(30,1200)
            )
        if not rr.ok:
            try: detail=rr.json().get('detail') or rr.text[:800]
            except Exception: detail=rr.text[:800]
            raise ValueError('Scribe v2 no pudo sincronizar: '+str(detail))

        _ai_task_set(task_id,progress=72,stage='Recibiendo transcripción y timings…')
        payload=rr.json(); ai_words=payload.get('words') or []
        if not ai_words: raise ValueError('Scribe v2 no devolvió palabras con tiempos.')

        _ai_task_set(task_id,progress=82,stage='Organizando letra en líneas y estrofas…')
        project=_ai_project_from_words(artist,title,voice_name,duration,'',ai_words,'scribe_only',jid=jid)
        final_lyrics=_project_lyrics(project) or str((payload.get('scribe') or {}).get('text') or '').strip()

        _ai_task_set(task_id,progress=90,stage='Buscando posibles vocalizaciones omitidas…')
        gaps=_detect_untranscribed_voice(voice_path,ai_words,duration)
        project.setdefault('ai',{})['voice_gaps']=gaps
        project['ai']['scribe_word_count']=len(ai_words)
        project['ai']['coverage_check']='audio_energy_vs_scribe'

        _ai_task_set(task_id,progress=96,stage='Guardando proyecto de prueba…')
        _finalize_ai_job(jid,artist,title,voice_name,voice_original,inst_name,duration,final_folder,tmp_folder,project,final_lyrics)
        flagged=sum(1 for w in ai_words if str(w.get('qa_status') or '')!='green')
        _ai_task_set(task_id,status='done',progress=100,stage='Listo · abriendo editor',
            result={'idTrabajo':jid,'words':len(ai_words),'flagged':flagged,'voice_gaps':len(gaps),
                    'elapsed_s':round(time.monotonic()-started,3),'source_mode':'scribe_only'})
    except Exception as e:
        app.logger.exception('AI async create job')
        try: shutil.rmtree(tmp_folder,ignore_errors=True)
        except Exception: pass
        _ai_task_set(task_id,status='error',progress=100,stage='Error',error=str(e))
    finally:
        with _AI_TASK_LOCK: _AI_RESERVED_IDS.discard(str(jid))

@app.post('/api/ai/create-job/start')
def ai_create_job_start():
    if not TEST_MODE: return jsonify(ok=False,error='Ruta sólo disponible en IA TEST.'),403
    token=request.form.get('session_token','')
    try:
        session(token,'ADMIN')
        voice=request.files.get('voice'); inst=request.files.get('instrumental')
        artist=str(request.form.get('artist') or '').strip()
        title=str(request.form.get('title') or '').strip()
        duration=float(request.form.get('voice_duration') or 0)
        if not voice or not voice.filename: raise ValueError('Selecciona la VOZ MP3.')
        if not inst or not inst.filename: raise ValueError('Selecciona el INSTRUMENTAL WAV.')
        if Path(voice.filename).suffix.lower()!='.mp3': raise ValueError('La VOZ debe ser MP3.')
        if Path(inst.filename).suffix.lower()!='.wav': raise ValueError('El INSTRUMENTAL debe ser WAV.')
        if not artist or not title:
            try: artist,title=master_identity(inst.filename)
            except Exception: raise ValueError('Completa artista y título.')

        with _AI_TASK_LOCK:
            with db() as c: jid=next_id(c)
            while str(jid) in _AI_RESERVED_IDS:
                n=int(re.sub(r'\D','',jid) or 0)+1; jid=f'LET-{n:04d}'
            _AI_RESERVED_IDS.add(str(jid))

        final_folder=JOBS/jid; tmp_folder=JOBS/f'.{jid}.ia-uploading'
        shutil.rmtree(tmp_folder,ignore_errors=True); tmp_folder.mkdir(parents=True,exist_ok=True)
        voice_name=safe_name(f'{artist} - {title} (Voz).mp3')
        inst_name=safe_name(Path(inst.filename).name)
        voice_path=tmp_folder/voice_name; inst_path=tmp_folder/inst_name
        voice.save(voice_path); inst.save(inst_path)
        if voice_path.stat().st_size<=0 or inst_path.stat().st_size<=0:
            raise ValueError('Uno de los audios llegó vacío.')

        task_id=secrets.token_urlsafe(12)
        _ai_task_set(task_id,status='running',progress=36,stage='Audio recibido · preparando Scribe v2…',idTrabajo=jid)
        threading.Thread(target=_run_ai_create_task,
            args=(task_id,jid,artist,title,voice_name,voice.filename,inst_name,duration,tmp_folder,final_folder),
            daemon=True).start()
        return jsonify(ok=True,task_id=task_id,idTrabajo=jid),202
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('AI start job')
        return jsonify(ok=False,error='No se pudo iniciar Scribe v2: '+str(e)),500

@app.get('/api/ai/tasks/<task_id>')
def ai_task_status(task_id):
    if not TEST_MODE: return jsonify(ok=False,error='Ruta sólo disponible en IA TEST.'),403
    token=request.args.get('session_token','')
    try: session(token,'ADMIN')
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    task=_ai_task_public(task_id)
    if not task: return jsonify(ok=False,error='Tarea IA no encontrada.'),404
    return jsonify(ok=True,task=task)

'''
    if marker not in s: raise SystemExit('PATCH FAIL server marker')
    s=s.replace(marker,helpers+marker)

s=s.replace("data={'lyrics':lyrics,'language_code':'spa'},","data={'lyrics':'','language_code':'spa'},")
s=s.replace("source_mode=str(payload.get('source_mode') or ('compare_master' if lyrics else 'scribe_only'))","source_mode='scribe_only'")
s=s.replace(
"""        seed_lyrics=lyrics if source_mode=='compare_master' else ''
        project=_ai_project_from_words(artist,title,voice_name,duration,seed_lyrics,ai_words,source_mode,jid=jid)
        final_lyrics=lyrics if source_mode=='compare_master' else _project_lyrics(project)""",
"""        project=_ai_project_from_words(artist,title,voice_name,duration,'',ai_words,'scribe_only',jid=jid)
        final_lyrics=_project_lyrics(project)"""
)
if "project.setdefault('ai',{})['voice_gaps']=_detect_untranscribed_voice" not in s:
    s=s.replace(
"""        if not final_lyrics:
            final_lyrics=str((payload.get('scribe') or {}).get('text') or '').strip()
        meta={'idTrabajo':jid""",
"""        if not final_lyrics:
            final_lyrics=str((payload.get('scribe') or {}).get('text') or '').strip()
        project.setdefault('ai',{})['voice_gaps']=_detect_untranscribed_voice(voice_path,ai_words,duration)
        project['ai']['scribe_word_count']=len(ai_words)
        meta={'idTrabajo':jid"""
    )
SERVER.write_text(s,encoding='utf-8')

# ---------------- panel.html ----------------
h=PANEL.read_text(encoding='utf-8')
h=h.replace(
"""    <label>Letra maestra · OPCIONAL</label>
    <textarea id="inLetra" rows="6" placeholder="Opcional. Si la dejas vacía, Scribe v2 generará letra + timings."></textarea>""",
"""    <div class="ia-source-note"><b>✨ ElevenLabs Scribe v2</b><span>Generará la letra completa + timings automáticamente. No necesitas pegar una letra.</span></div>
    <textarea id="inLetra" rows="1" style="display:none"></textarea>"""
)
h=h.replace(
"""    <div id="uploadProgress" style="display:none;margin-top:10px;color:var(--teal);font-size:12px"></div>""",
"""    <div id="uploadProgress" class="ia-progress" style="display:none">
      <div class="ia-progress-head"><span id="iaProgressStage">Preparando…</span><b id="iaProgressPct">0%</b></div>
      <div class="ia-progress-track"><div id="iaProgressFill" class="ia-progress-fill"></div></div>
      <div class="ia-progress-meta"><span id="iaProgressDetail">Esperando…</span><span id="iaProgressTime">0.0 s</span></div>
    </div>"""
)
if ".ia-progress{" not in h:
    h=h.replace("</style>",r'''
.ia-source-note{margin:12px 0 4px;padding:10px 12px;border:1px solid rgba(139,92,246,.35);background:rgba(139,92,246,.08);border-radius:8px;display:flex;flex-direction:column;gap:3px;font-size:11.5px;color:var(--text-2)}
.ia-source-note b{color:#bda6ff;font-size:12px}.ia-source-note span{color:var(--text-3)}
.ia-progress{margin-top:12px;padding:11px 12px;border:1px solid var(--line);background:rgba(0,0,0,.18);border-radius:8px}
.ia-progress-head,.ia-progress-meta{display:flex;justify-content:space-between;align-items:center;gap:10px}
.ia-progress-head{font-size:12px;color:var(--text-2)}.ia-progress-head b{color:var(--teal);font:700 11px var(--mono)}
.ia-progress-track{height:7px;margin:8px 0 6px;background:var(--bg-base);border:1px solid var(--line);border-radius:999px;overflow:hidden}
.ia-progress-fill{height:100%;width:0%;background:linear-gradient(90deg,#8b5cf6,#2dd4bf);transition:width .22s ease}
.ia-progress-meta{font-size:10.5px;color:var(--text-3)}#iaProgressTime{font-family:var(--mono);white-space:nowrap}
</style>''',1)

hs=h.find("document.getElementById('btnEnviarNueva').addEventListener")
he=h.find("/* =========================================================\n   EDITOR",hs)
if hs<0 or he<0: raise SystemExit('PATCH FAIL panel handler')
handler=r'''function setIaProgress(pct,stage,detail){
  const box=document.getElementById('uploadProgress'),fill=document.getElementById('iaProgressFill'),
        pctEl=document.getElementById('iaProgressPct'),stageEl=document.getElementById('iaProgressStage'),
        detailEl=document.getElementById('iaProgressDetail');
  if(box)box.style.display='block';
  const p=Math.max(0,Math.min(100,Number(pct)||0));
  if(fill)fill.style.width=p+'%';if(pctEl)pctEl.textContent=Math.round(p)+'%';
  if(stageEl)stageEl.textContent=stage||'Procesando…';if(detailEl)detailEl.textContent=detail||'';
}
async function pollIaTask(taskId){
  for(let i=0;i<2400;i++){
    const r=await fetch('/cdg-editor-ia/api/ai/tasks/'+encodeURIComponent(taskId)+'?session_token='+encodeURIComponent(SESSION_TOKEN),{cache:'no-store'});
    let d={};try{d=await r.json()}catch(_){}
    if(!r.ok||d.ok===false)throw new Error(d.error||('Error consultando IA '+r.status));
    const t=d.task||{};
    setIaProgress(t.progress??40,t.stage||'Procesando con Scribe v2…',
      t.status==='running'?'Proceso real del servidor':t.status==='done'?'Completado':'');
    if(t.status==='done')return t.result||{};
    if(t.status==='error')throw new Error(t.error||'Scribe v2 falló.');
    await new Promise(res=>setTimeout(res,450));
  }
  throw new Error('La tarea IA tardó demasiado.');
}
document.getElementById('btnEnviarNueva').addEventListener('click', async function(){
  const artista=document.getElementById('inArtista').value.trim(),titulo=document.getElementById('inTitulo').value.trim();
  if(!artista)return toast('Escribe el nombre del artista.','error');
  if(!titulo)return toast('Escribe el título de la canción.','error');
  if(!archivoSeleccionado||!archivoSeleccionado.file)return toast('Selecciona la voz MP3.','error');
  if(!instrumentalSeleccionado||!instrumentalSeleccionado.file)return toast('Selecciona el instrumental WAV.','error');
  if(!/\.mp3$/i.test(archivoSeleccionado.file.name))return toast('La VOZ debe ser MP3.','error');
  if(!/\.wav$/i.test(instrumentalSeleccionado.file.name))return toast('El INSTRUMENTAL debe ser WAV.','error');

  const btn=document.getElementById('btnEnviarNueva'),timeEl=document.getElementById('iaProgressTime');
  const started=performance.now();let clockTimer=setInterval(()=>{if(timeEl)timeEl.textContent=((performance.now()-started)/1000).toFixed(1)+' s';},100);
  btn.disabled=true;btn.textContent='Sincronizando…';
  setIaProgress(1,'Preparando archivos…','ElevenLabs será la única fuente de letra y timings');
  try{
    const fd=new FormData();
    fd.append('session_token',SESSION_TOKEN);fd.append('artist',artista);fd.append('title',titulo);
    fd.append('voice_duration',String(archivoSeleccionado.duracion||0));
    fd.append('voice',archivoSeleccionado.file,archivoSeleccionado.file.name);
    fd.append('instrumental',instrumentalSeleccionado.file,instrumentalSeleccionado.file.name);

    const startData=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST','/cdg-editor-ia/api/ai/create-job/start');
      xhr.upload.onprogress=(ev)=>{if(ev.lengthComputable){
        const up=Math.min(35,(ev.loaded/ev.total)*35);
        setIaProgress(up,'Subiendo audio al servidor…',(ev.loaded/1048576).toFixed(1)+' / '+(ev.total/1048576).toFixed(1)+' MB');
      }};
      xhr.upload.onload=()=>setIaProgress(36,'Audio recibido','Preparando Scribe v2…');
      xhr.onerror=()=>reject(new Error('Falló la subida de los audios.'));
      xhr.onload=()=>{let d={};try{d=JSON.parse(xhr.responseText||'{}')}catch(_){}
        if(xhr.status<200||xhr.status>=300||d.ok===false)return reject(new Error(d.error||('Error '+xhr.status)));resolve(d);};
      xhr.send(fd);
    });
    const d=await pollIaTask(startData.task_id),gaps=Number(d.voice_gaps||0);
    setIaProgress(100,'✓ IA lista',d.words+' palabras · '+(gaps?gaps+' posible(s) vocalización(es) para revisar':'sin huecos vocales detectados'));
    toast('Scribe v2 terminó. Abriendo sincronización…');await cargarLista();
    setTimeout(()=>{document.getElementById('modalNueva').classList.remove('open');abrirEditor(d.idTrabajo||startData.idTrabajo);},350);
  }catch(e){
    const msg=e&&e.message?e.message:String(e);setIaProgress(100,'ERROR',msg);
    const stage=document.getElementById('iaProgressStage');if(stage)stage.style.color='var(--danger)';toast(msg,'error');
  }finally{
    clearInterval(clockTimer);if(timeEl)timeEl.textContent=((performance.now()-started)/1000).toFixed(1)+' s';
    btn.disabled=false;btn.textContent='✨ Crear y sincronizar con IA';
  }
});

'''
h=h[:hs]+handler+h[he:]
PANEL.write_text(h,encoding='utf-8')

# ---------------- editor_v1/index.html ----------------
e=EDITOR.read_text(encoding='utf-8')
roles='''      <div id="vocalRoles" style="display:flex;gap:6px;align-items:center;padding:5px 0 7px;border-bottom:1px solid var(--line)"><button class="hbtn roleNone" id="btnRoleNone">SIN ROL <span class="roleKey">0</span></button><button class="hbtn roleMale" id="btnMale">HOMBRE <span class="roleKey">1</span></button><button class="hbtn roleFemale" id="btnFemale">MUJER <span class="roleKey">2</span></button><button class="hbtn roleDuet" id="btnDuet">DUO <span class="roleKey">3</span></button><button class="hbtn spoken" id="btnSpoken2">HABLADO <span class="roleKey">4</span></button><span style="font-size:10px;color:var(--dimmer);margin-left:6px">Selecciona (clic/Shift/Ctrl+arrastre/L#) → pulsa el rol</span></div>'''
if 'id="aiQaBar"' not in e:
    e=must_replace(e,roles,roles+'\n      <div id="aiQaBar" class="aiQaBar" hidden></div>','editor qa html')
if ".aiQaBar{" not in e:
    e=e.replace("</style>",r'''
.aiQaBar{margin:6px 0 7px;padding:7px 8px;border:1px solid rgba(242,169,0,.45);background:rgba(242,169,0,.08);border-radius:7px;font:10px/1.35 var(--mono);color:#f6cf73}
.aiQaTitle{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:5px;font-weight:700}
.aiQaList{display:flex;gap:5px;overflow-x:auto;padding-bottom:2px}.aiQaGap{flex:0 0 auto;border:1px solid rgba(242,169,0,.35);background:rgba(0,0,0,.18);color:#f4d17d;border-radius:5px;padding:4px 6px;font:9.5px var(--mono);cursor:pointer}
.aiQaGap:hover{border-color:#f2a900;color:#fff}
</style>''',1)

ps=e.find("function pvWrap(){"); pe=e.find("\n}\n\n/* El bloque de instrumental",ps)
if ps<0 or pe<0: raise SystemExit('PATCH FAIL pvWrap')
new_pv=r'''function pvWrap(){
  const lpp=PV.cfg.linesPerPage;
  const blocks=[];let block=[];
  const flushBlock=()=>{if(block.length){blocks.push(block);block=[];}};
  for(const seg of S.doc.segments){
    if(seg.kind==="break"){flushBlock();continue;}
    const renderWords=(seg.words||[]).filter(w=>!w.spoken);
    if(!renderWords.length)continue;
    if(renderWords.some(w=>w.start_time===null)){flushBlock();break;}
    let cur=[];
    for(const w of renderWords){
      const probe=cur.concat([w]),txt=probe.map(x=>pvText(x.text)).join(" ");
      if(cur.length&&advWidth(txt)>PV.WRAP){block.push(cur);cur=[w];}else cur=probe;
    }
    if(cur.length)block.push(cur);
  }
  flushBlock();
  const out=[];const padPage=()=>{while(out.length%lpp)out.push([]);};
  for(const b of blocks){
    if(out.length)padPage();
    for(let i=0;i<b.length;i+=lpp){
      if(i>0)padPage();
      const chunk=b.slice(i,i+lpp),top=Math.floor((lpp-chunk.length)/2),bottom=lpp-chunk.length-top;
      for(let j=0;j<top;j++)out.push([]);out.push(...chunk);for(let j=0;j<bottom;j++)out.push([]);
    }
  }
  while(out.length&&!out[out.length-1].length)out.pop();
  return out;
}'''
e=e[:ps]+new_pv+e[pe+2:]

if "/* QA IA: posible voz no transcrita */" not in e:
    e=e.replace("  /* marcas */",r'''  /* QA IA: posible voz no transcrita */
  const qaGaps=(S.doc?.ai?.voice_gaps||[]);
  for(const g of qaGaps){
    const a=Number(g.start),b=Number(g.end);
    if(!Number.isFinite(a)||!Number.isFinite(b)||b<S.view.t0||a>S.view.t0+S.view.dur)continue;
    const y1=Math.max(0,t2y(a)),y2=Math.min(CH,t2y(b));if(y2<=y1)continue;
    cx.fillStyle="rgba(242,169,0,.10)";cx.fillRect(x0,y1,wavW,Math.max(2,y2-y1));
    cx.strokeStyle="rgba(242,169,0,.75)";cx.lineWidth=1;cx.setLineDash([5,4]);
    cx.strokeRect(x0+.5,y1+.5,wavW-1,Math.max(2,y2-y1)-1);cx.setLineDash([]);
    if(S.view.dur<120){cx.font="700 9px "+mono;cx.fillStyle="#F2A900";cx.textAlign="right";cx.fillText("⚠ VOZ SIN TEXTO",CW-6,Math.max(10,y1+9));}
  }

  /* marcas */''',1)
if "function paintAiQa(){" not in e:
    e=e.replace("function paintCounter(){",r'''function fmtQaTime(t){
  t=Math.max(0,Number(t)||0);const m=Math.floor(t/60),sec=t-m*60;
  return m+":"+(sec<10?"0":"")+sec.toFixed(1);
}
function paintAiQa(){
  const bar=$("#aiQaBar");if(!bar)return;const gaps=S.doc?.ai?.voice_gaps||[];
  if(!gaps.length){bar.hidden=true;bar.innerHTML="";return;}
  bar.hidden=false;
  bar.innerHTML='<div class="aiQaTitle"><span>⚠ '+gaps.length+' posible'+(gaps.length===1?'':'s')+' vocalización'+(gaps.length===1?'':'es')+' omitida'+(gaps.length===1?'':'s')+' por Scribe</span><span>clic = escuchar</span></div>'
    +'<div class="aiQaList">'+gaps.map((g,i)=>'<button class="aiQaGap" data-gap="'+i+'">'+fmtQaTime(g.start)+'–'+fmtQaTime(g.end)+' · '+Number(g.duration||0).toFixed(1)+' s</button>').join('')+'</div>';
  bar.querySelectorAll(".aiQaGap").forEach(btn=>btn.onclick=()=>{
    const g=gaps[+btn.dataset.gap];if(!g)return;
    const a=Math.max(0,Number(g.start)||0),b=Math.max(a+.5,Number(g.end)||a+4);
    S.audio.currentTime=Math.max(0,a-.5);const dur=Math.max(8,Math.min(30,(b-a)+4));
    S.view={t0:Math.max(0,a-2),dur:Math.min(dur,Math.max(8,S.duration-Math.max(0,a-2)))};
    draw();pvDraw();paintClock();toast("Revisando posible voz no transcrita.");
  });
}
function paintCounter(){''',1)
e=e.replace(
'''function paintCounter(){
  const n = timedCount(), tot = S.words.length;
  $("#counter").innerHTML = "<b>"+n+"</b>/"+tot+" · "+(tot?Math.round(n/tot*100):0)+"%";
}''',
'''function paintCounter(){
  const n = timedCount(), tot = S.words.length, gaps=S.doc?.ai?.voice_gaps||[];
  $("#counter").innerHTML = "<b>"+n+"</b>/"+tot+" · "+(tot?Math.round(n/tot*100):0)+"%"+(gaps.length?' · <span style="color:#F2A900">⚠ '+gaps.length+'</span>':'');
  paintAiQa();
}'''
)
EDITOR.write_text(e,encoding='utf-8')

# ---------------- renderer/normalize.py ----------------
n=NORMALIZE.read_text(encoding='utf-8')
old='''        words = [w for w in seg.get("words", []) if not w.get("spoken")]
        if not words:
            # Un break organiza la letra, pero NO ocupa una fila física del CDG.
            # El tiempo decide cuándo aparece la siguiente frase; no su posición.
            continue'''
new='''        if seg.get("kind") == "break":
            if visual and visual[-1]:
                visual.append([])
            continue
        words = [w for w in seg.get("words", []) if not w.get("spoken")]
        if not words:
            continue'''
if old in n:n=n.replace(old,new,1)
if "def center_stanza_pages(" not in n:
    marker="\ndef wipe_spans("
    helper='''
def center_stanza_pages(visual: list[list[dict]], lines_per_page: int) -> list[list[dict]]:
    lpp=max(2,min(8,int(lines_per_page)))
    blocks=[]; block=[]
    for line in visual:
        if not line:
            if block:
                blocks.append(block); block=[]
            continue
        block.append(line)
    if block: blocks.append(block)
    out=[]
    for block in blocks:
        while out and len(out)%lpp: out.append([])
        for i in range(0,len(block),lpp):
            while out and len(out)%lpp: out.append([])
            chunk=block[i:i+lpp];top=(lpp-len(chunk))//2;bottom=lpp-len(chunk)-top
            out.extend([[] for _ in range(top)]);out.extend(chunk);out.extend([[] for _ in range(bottom)])
    while out and not out[-1]: out.pop()
    return out

'''
    if marker not in n: raise SystemExit('PATCH FAIL normalize marker')
    n=n.replace(marker,helper+marker,1)
n=n.replace(
'''    visual = wrap_lines(doc, font, upper)
    visual = build_instrumentals(visual, style, spoken_intervals)''',
'''    visual = wrap_lines(doc, font, upper)
    visual = center_stanza_pages(visual, style["lines_per_page"])
    visual = build_instrumentals(visual, style, spoken_intervals)''',
1)
NORMALIZE.write_text(n,encoding='utf-8')

print('PATCH_7_IMPROVEMENTS=OK')
