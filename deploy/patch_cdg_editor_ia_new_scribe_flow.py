#!/usr/bin/env python3
from pathlib import Path

ROOT=Path('/opt/djgabo-cdg-ia-test')
server=ROOT/'server.py'
panel=ROOT/'panel.html'
s=server.read_text(encoding='utf-8')

marker="@app.post('/api/jobs/create')\ndef create_job_upload():"
if "@app.post('/api/ai/create-job')" not in s:
    block=r'''
def _ai_project_from_words(artist,title,voice_name,duration,lyrics,ai_words,source_mode):
    clean=[w for w in (ai_words or []) if str(w.get('text') or '').strip()]
    segments=[]; wi=0; si=0
    def add(tokens):
        nonlocal wi,si
        words=[]
        for item in tokens:
            txt=str(item.get('master_text') or item.get('text') or '').strip()
            if not txt: continue
            a=item.get('start'); b=item.get('end')
            words.append({
                'id':f'w{wi:04d}','text':txt,
                'start_time':round(float(a),6) if a is not None else None,
                'end_time':round(float(b),6) if b is not None else None,
                'locked':False,'spoken':False,'vocal_role':None,
                'ai_confidence':float(item.get('confidence') or 0),
                'ai_status':str(item.get('qa_status') or ''),
                'scribe_text':item.get('scribe_text'),
                'ai_match_type':str(item.get('match_type') or ''),
            }); wi+=1
        if words:
            segments.append({'id':f's{si:04d}','kind':'lyric','text':' '.join(x['text'] for x in words),'words':words}); si+=1

    if lyrics.strip():
        pos=0
        for raw in lyrics.replace('\r','').split('\n'):
            line=raw.strip()
            if not line:
                if segments and segments[-1].get('kind')!='break':
                    segments.append({'id':f's{si:04d}','kind':'break','text':'','words':[]}); si+=1
                continue
            batch=[]
            for master_text in line.split():
                item=dict(clean[pos]) if pos<len(clean) else {'text':master_text,'start':None,'end':None,'confidence':0,'qa_status':'red','match_type':'missing'}
                item['master_text']=master_text; batch.append(item); pos+=1
            add(batch)
    else:
        for start in range(0,len(clean),6): add(clean[start:start+6])

    while segments and segments[-1].get('kind')=='break': segments.pop()
    return {
      'version':1,
      'song':{'artist':artist,'title':title,'audio_file':voice_name,'audio_sha1':'','duration':float(duration or 0)},
      'calibration_ms':0,'segments':segments,
      'ai':{'engine':'elevenlabs-scribe-v2','source_mode':source_mode,'generated_at':now()}
    }

@app.post('/api/ai/create-job')
def ai_create_job():
    if not TEST_MODE: return jsonify(ok=False,error='Ruta sólo disponible en IA TEST.'),403
    token=request.form.get('session_token','')
    tmp_folder=None
    try:
        session(token,'ADMIN')
        voice=request.files.get('voice'); inst=request.files.get('instrumental')
        lyrics=str(request.form.get('lyrics') or '').strip()
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

        with db() as c: jid=next_id(c)
        final_folder=JOBS/jid; tmp_folder=JOBS/f'.{jid}.ia-uploading'
        shutil.rmtree(tmp_folder,ignore_errors=True); tmp_folder.mkdir(parents=True,exist_ok=True)
        voice_name=safe_name(f'{artist} - {title} (Voz).mp3')
        inst_name=safe_name(Path(inst.filename).name)
        voice_path=tmp_folder/voice_name; inst_path=tmp_folder/inst_name
        voice.save(voice_path); inst.save(inst_path)
        if voice_path.stat().st_size<=0 or inst_path.stat().st_size<=0: raise ValueError('Uno de los audios llegó vacío.')

        started=time.monotonic()
        with voice_path.open('rb') as fh:
            rr=requests.post('http://127.0.0.1:8097/api/elevenlabs/transcribe',
                files={'audio':(voice_name,fh,'audio/mpeg')},
                data={'lyrics':lyrics,'language_code':'spa'},timeout=(30,1200))
        if not rr.ok:
            try: detail=rr.json().get('detail') or rr.text[:800]
            except Exception: detail=rr.text[:800]
            raise ValueError('Scribe v2 no pudo sincronizar: '+str(detail))
        payload=rr.json(); ai_words=payload.get('words') or []
        source_mode=str(payload.get('source_mode') or ('compare_master' if lyrics else 'scribe_only'))
        if not ai_words: raise ValueError('Scribe v2 no devolvió palabras con tiempos.')
        final_lyrics=lyrics or str((payload.get('scribe') or {}).get('text') or '').strip()
        if not final_lyrics: final_lyrics=' '.join(str(w.get('text') or '').strip() for w in ai_words).strip()

        project=_ai_project_from_words(artist,title,voice_name,duration,final_lyrics,ai_words,source_mode)
        (tmp_folder/'trabajo.json').write_text(json.dumps({'idTrabajo':jid,'artista':artist,'titulo':title,'voz':voice_name,'instrumental':inst_name,'modo':'IA_TEST_LOCAL','creado':now()},ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp_folder/'letra_moises.txt').write_text(final_lyrics,encoding='utf-8')
        if final_folder.exists(): shutil.rmtree(final_folder,ignore_errors=True)
        tmp_folder.rename(final_folder)
        with db() as c:
            t=now()
            c.execute("""INSERT INTO jobs(
              id,artist,title,status,created,updated,voice_filename,voice_original_filename,
              voice_drive_status,instrumental_filename,lyrics_moises,lyrics_corrected,duration,size_bytes,
              project_json,dropbox_status,timings_drive_status,sheet_master_status,origin
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (jid,artist,title,EST_C,t,t,voice_name,Path(voice.filename).name,'TEST_LOCAL',inst_name,
             final_lyrics,final_lyrics,duration,(final_folder/voice_name).stat().st_size,
             json.dumps(project,ensure_ascii=False),'TEST_LOCAL','TEST_LOCAL','TEST_LOCAL','IA_TEST'))
            log(c,jid,'SCRIBE V2 · CREAR Y SINCRONIZAR',source_mode)
        flagged=sum(1 for w in ai_words if str(w.get('qa_status') or '')!='green')
        return jsonify(ok=True,idTrabajo=jid,source_mode=source_mode,
          lyrics_source='MAESTRA' if lyrics else 'SCRIBE',
          elapsed_s=round(time.monotonic()-started,3),words=len(ai_words),flagged=flagged,
          metrics=payload.get('metrics') or {})
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e:
        if tmp_folder: shutil.rmtree(tmp_folder,ignore_errors=True)
        return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        if tmp_folder: shutil.rmtree(tmp_folder,ignore_errors=True)
        app.logger.exception('ai create job')
        return jsonify(ok=False,error='No se pudo crear/sincronizar con IA: '+str(e)),500

'''
    if marker not in s: raise SystemExit('No encontré punto de inserción server.py')
    s=s.replace(marker,block+marker)

server.write_text(s,encoding='utf-8')

h=panel.read_text(encoding='utf-8')
h=h.replace('Letra preliminar (Moisés)','Letra maestra · OPCIONAL')
h=h.replace('Pega aquí la letra generada por Moisés…','Opcional. Si la dejas vacía, Scribe v2 generará letra + timings.')
h=h.replace('<label>Destino Dropbox actual <span style="color:var(--teal);font-size:10px">· WAV ahora · CDG al finalizar</span></label>',
            '<label style="display:none">Destino Dropbox actual <span style="color:var(--teal);font-size:10px">· WAV ahora · CDG al finalizar</span></label>')
h=h.replace('<div class="dropbox-dest-card">','<div class="dropbox-dest-card" style="display:none">',1)
h=h.replace('<div id="dropboxNuevaStatus" class="dropbox-status">Comprobando Dropbox…</div>',
            '<div id="dropboxNuevaStatus" class="dropbox-status" style="display:none">Dropbox desactivado en IA TEST</div>')
h=h.replace('<button class="btn btn-primary" id="btnEnviarNueva">Enviar a corrección</button>',
            '<button class="btn btn-primary" id="btnEnviarNueva">✨ Crear y sincronizar con IA</button>')

start=h.find("document.getElementById('btnEnviarNueva').addEventListener")
end=h.find("/* =========================================================\n   EDITOR",start)
if start<0 or end<0: raise SystemExit('No encontré handler Nueva canción')
handler=r"""document.getElementById('btnEnviarNueva').addEventListener('click', async function(){
  const artista=document.getElementById('inArtista').value.trim();
  const titulo=document.getElementById('inTitulo').value.trim();
  const letra=document.getElementById('inLetra').value.trim();
  if(!artista)return toast('Escribe el nombre del artista.','error');
  if(!titulo)return toast('Escribe el título de la canción.','error');
  if(!archivoSeleccionado||!archivoSeleccionado.file)return toast('Selecciona la voz MP3.','error');
  if(!instrumentalSeleccionado||!instrumentalSeleccionado.file)return toast('Selecciona el instrumental WAV.','error');
  if(!/\.mp3$/i.test(archivoSeleccionado.file.name))return toast('La VOZ debe ser MP3.','error');
  if(!/\.wav$/i.test(instrumentalSeleccionado.file.name))return toast('El INSTRUMENTAL debe ser WAV.','error');
  const btn=document.getElementById('btnEnviarNueva'),prog=document.getElementById('uploadProgress');
  btn.disabled=true;btn.textContent='Sincronizando…';prog.style.display='block';prog.style.color='';
  prog.textContent=letra?'Scribe v2 está alineando contra la letra maestra…':'Scribe v2 está generando letra + timings…';
  try{
    const fd=new FormData();
    fd.append('session_token',SESSION_TOKEN);fd.append('artist',artista);fd.append('title',titulo);
    fd.append('lyrics',letra);fd.append('voice_duration',String(archivoSeleccionado.duracion||0));
    fd.append('voice',archivoSeleccionado.file,archivoSeleccionado.file.name);
    fd.append('instrumental',instrumentalSeleccionado.file,instrumentalSeleccionado.file.name);
    const r=await fetch('/cdg-editor-ia/api/ai/create-job',{method:'POST',body:fd});
    let d={};try{d=await r.json()}catch(_){}
    if(!r.ok||d.ok===false)throw new Error(d.error||('Error IA '+r.status));
    prog.textContent='✓ IA lista · '+d.words+' palabras · '+d.flagged+' para revisar · '+d.elapsed_s+' s';
    toast('Scribe v2 terminó. Abriendo sincronización…');await cargarLista();
    document.getElementById('modalNueva').classList.remove('open');setTimeout(()=>abrirEditor(d.idTrabajo),300);
  }catch(e){
    const msg=e&&e.message?e.message:String(e);prog.textContent='ERROR: '+msg;prog.style.color='var(--danger)';toast(msg,'error');
  }finally{btn.disabled=false;btn.textContent='✨ Crear y sincronizar con IA';}
});

"""
h=h[:start]+handler+h[end:]
panel.write_text(h,encoding='utf-8')
print('PATCH_NEW_SCRIBE_FLOW=OK')
