#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
PANEL=ROOT/'panel.html'

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
for p in (SERVER,PANEL):
    if not p.is_file():
        raise SystemExit('MISSING: '+str(p))
    shutil.copy2(p,p.with_name(p.name+'.bak_uvr_bridge_'+stamp))

server=SERVER.read_text(encoding='utf-8')
panel=PANEL.read_text(encoding='utf-8')

# ---------- SERVER ----------
if "UVR_BRIDGE_DIR=DATA/'uvr_bridge'" not in server:
    old="VOICE_CACHE_DIR=Path(os.getenv('DJGABO_VOICE_CACHE_DIR') or (DATA/'voice_cache')).expanduser().resolve()\nfor p in (DATA,DB.parent,JOBS,OUTPUT,PENDING_CDG_DIR,PENDING_WAV_DIR,VOICE_CACHE_DIR): p.mkdir(parents=True,exist_ok=True)"
    new="VOICE_CACHE_DIR=Path(os.getenv('DJGABO_VOICE_CACHE_DIR') or (DATA/'voice_cache')).expanduser().resolve()\nUVR_BRIDGE_DIR=DATA/'uvr_bridge'\nfor p in (DATA,DB.parent,JOBS,OUTPUT,PENDING_CDG_DIR,PENDING_WAV_DIR,VOICE_CACHE_DIR,UVR_BRIDGE_DIR): p.mkdir(parents=True,exist_ok=True)"
    if old not in server:
        raise SystemExit('ANCHOR_NOT_FOUND: bridge dir')
    server=server.replace(old,new,1)

bridge_block=r'''def _uvr_bridge_duration(path):
    try:
        cp=subprocess.run([
            'ffprobe','-v','error','-show_entries','format=duration',
            '-of','default=noprint_wrappers=1:nokey=1',str(path)
        ],capture_output=True,text=True,timeout=20)
        if cp.returncode==0:
            return max(0.0,float((cp.stdout or '0').strip() or 0))
    except Exception:
        pass
    return 0.0

def _uvr_bridge_paths(token):
    key=re.sub(r'[^A-Za-z0-9_-]+','',str(token or ''))
    if not key or key!=str(token or '') or len(key)>80:
        raise ValueError('Token del puente inválido.')
    return UVR_BRIDGE_DIR/(key+'.bin'),UVR_BRIDGE_DIR/(key+'.json')

def _uvr_bridge_cleanup(max_age=7200):
    cutoff=time.time()-max_age
    try:
        for meta in UVR_BRIDGE_DIR.glob('*.json'):
            try:
                d=json.loads(meta.read_text(encoding='utf-8'))
                if float(d.get('created') or 0)<cutoff:
                    payload=meta.with_suffix('.bin')
                    payload.unlink(missing_ok=True); meta.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass

def _uvr_bridge_meta(token, target=None):
    payload,meta=_uvr_bridge_paths(token)
    if not payload.is_file() or not meta.is_file():
        raise ValueError('El audio temporal del puente ya no existe. Pega el QR nuevamente.')
    try: d=json.loads(meta.read_text(encoding='utf-8'))
    except Exception: raise ValueError('Metadatos del puente dañados.')
    if time.time()-float(d.get('created') or 0)>7200:
        payload.unlink(missing_ok=True); meta.unlink(missing_ok=True)
        raise ValueError('El audio temporal del puente expiró. Pega el QR nuevamente.')
    if target and str(d.get('target') or '')!=target:
        raise ValueError('El audio del puente no corresponde al destino solicitado.')
    if int(d.get('size') or -1)!=payload.stat().st_size:
        raise ValueError('El audio temporal del puente quedó incompleto.')
    return d,payload,meta

def _uvr_bridge_consume(token,target,dst):
    d,payload,meta=_uvr_bridge_meta(token,target)
    dst=Path(dst); dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(payload,dst)
    payload.unlink(missing_ok=True); meta.unlink(missing_ok=True)
    return d

@app.post('/api/ai/bridge-stage')
def ai_bridge_stage():
    """Recibe desde la extensión Brave un audio UVR ya descargado por el navegador."""
    if not TEST_MODE:
        return jsonify(ok=False,error='Puente UVR disponible sólo en IA TEST.'),403
    try:
        session(request.form.get('session_token',''),'ADMIN')
        target=str(request.form.get('target') or '').strip().lower()
        if target not in ('voice','instrumental'):
            raise ValueError('Destino del puente inválido.')
        f=request.files.get('audio')
        if not f or not f.filename:
            raise ValueError('El puente no envió ningún audio.')
        wanted='.mp3' if target=='voice' else '.wav'
        name=safe_name(Path(f.filename).name)
        if Path(name).suffix.lower()!=wanted:
            raise ValueError('El puente debe enviar '+('MP3 para VOZ.' if target=='voice' else 'WAV para INSTRUMENTAL.'))
        _uvr_bridge_cleanup()
        token=secrets.token_urlsafe(18)
        payload,meta=_uvr_bridge_paths(token)
        tmp=payload.with_suffix('.tmp')
        f.save(tmp)
        size=tmp.stat().st_size if tmp.exists() else 0
        if size<=0:
            tmp.unlink(missing_ok=True)
            raise ValueError('El audio recibido por el puente está vacío.')
        if size>350*1024*1024:
            tmp.unlink(missing_ok=True)
            raise ValueError('El audio recibido supera 350 MB.')
        tmp.replace(payload)
        mime='audio/mpeg' if target=='voice' else 'audio/wav'
        duration=_uvr_bridge_duration(payload)
        meta.write_text(json.dumps({
            'target':target,'name':name,'size':size,'mime':mime,'duration':duration,'created':time.time()
        },ensure_ascii=False),encoding='utf-8')
        return jsonify(ok=True,bridge_token=token,name=name,size=size,mime=mime,duration=duration)
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('IA bridge stage')
        return jsonify(ok=False,error='No se pudo guardar el audio del puente: '+str(e)),500


'''
if "@app.post('/api/ai/bridge-stage')" not in server:
    anchor="def _uvr_qr_filename(response, source_url, target):"
    if anchor not in server:
        raise SystemExit('ANCHOR_NOT_FOUND: qr helper')
    server=server.replace(anchor,bridge_block+anchor,1)

new_create=r'''@app.post('/api/ai/create-job/start')
def ai_create_job_start():
    if not TEST_MODE: return jsonify(ok=False,error='Ruta sólo disponible en IA TEST.'),403
    token=request.form.get('session_token','')
    try:
        session(token,'ADMIN')
        voice=request.files.get('voice'); inst=request.files.get('instrumental')
        voice_bridge=str(request.form.get('voice_bridge_token') or '').strip()
        inst_bridge=str(request.form.get('instrumental_bridge_token') or '').strip()
        inst_bridge_name=safe_name(Path(str(request.form.get('instrumental_bridge_name') or '')).name) if request.form.get('instrumental_bridge_name') else ''
        artist=str(request.form.get('artist') or '').strip()
        title=str(request.form.get('title') or '').strip()
        duration=float(request.form.get('voice_duration') or 0)

        voice_meta=None; inst_meta=None
        if voice_bridge: voice_meta,_,_=_uvr_bridge_meta(voice_bridge,'voice')
        if inst_bridge: inst_meta,_,_=_uvr_bridge_meta(inst_bridge,'instrumental')
        if duration<=0 and voice_meta:
            duration=float(voice_meta.get('duration') or 0)
        voice_original=(str(voice_meta.get('name') or '') if voice_meta else (voice.filename if voice else ''))
        inst_original=(inst_bridge_name if (inst_meta and inst_bridge_name) else (str(inst_meta.get('name') or '') if inst_meta else (inst.filename if inst else '')))

        if not voice_original: raise ValueError('Selecciona la VOZ MP3.')
        if not inst_original: raise ValueError('Selecciona el INSTRUMENTAL WAV.')
        if Path(voice_original).suffix.lower()!='.mp3': raise ValueError('La VOZ debe ser MP3.')
        if Path(inst_original).suffix.lower()!='.wav': raise ValueError('El INSTRUMENTAL debe ser WAV.')
        if not artist or not title:
            try: artist,title=master_identity(inst_original)
            except Exception: raise ValueError('Completa artista y título.')

        with _AI_TASK_LOCK:
            with db() as c: jid=next_id(c)
            while str(jid) in _AI_RESERVED_IDS:
                n=int(re.sub(r'\D','',jid) or 0)+1; jid=f'LET-{n:04d}'
            _AI_RESERVED_IDS.add(str(jid))

        final_folder=JOBS/jid; tmp_folder=JOBS/f'.{jid}.ia-uploading'
        shutil.rmtree(tmp_folder,ignore_errors=True); tmp_folder.mkdir(parents=True,exist_ok=True)
        voice_name=safe_name(f'{artist} - {title} (Voz).mp3')
        inst_name=safe_name(Path(inst_original).name)
        voice_path=tmp_folder/voice_name; inst_path=tmp_folder/inst_name
        if voice_bridge: _uvr_bridge_consume(voice_bridge,'voice',voice_path)
        else: voice.save(voice_path)
        if inst_bridge: _uvr_bridge_consume(inst_bridge,'instrumental',inst_path)
        else: inst.save(inst_path)
        if voice_path.stat().st_size<=0 or inst_path.stat().st_size<=0:
            raise ValueError('Uno de los audios llegó vacío.')

        task_id=secrets.token_urlsafe(12)
        _ai_task_set(task_id,status='running',progress=36,stage='Audio recibido · preparando Scribe v2…',
                     idTrabajo=jid)
        threading.Thread(
            target=_run_ai_create_task,
            args=(task_id,jid,artist,title,voice_name,voice_original,inst_name,duration,tmp_folder,final_folder),
            daemon=True
        ).start()
        return jsonify(ok=True,task_id=task_id,idTrabajo=jid),202
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('AI start job')
        return jsonify(ok=False,error='No se pudo iniciar Scribe v2: '+str(e)),500

'''
start=server.find("@app.post('/api/ai/create-job/start')")
end=server.find("@app.get('/api/ai/tasks/<task_id>')",start)
if start<0 or end<0:
    raise SystemExit('ANCHOR_NOT_FOUND: create job start')
server=server[:start]+new_create+server[end:]

# ---------- PANEL ----------
bridge_js=r'''function esperarPuenteUvr(timeoutMs){
  timeoutMs=timeoutMs||1200;
  return new Promise((resolve,reject)=>{
    let done=false;
    const timer=setTimeout(()=>{
      if(done)return;done=true;window.removeEventListener('message',onMsg);
      reject(new Error('No detecto KITKARAOKE UVR Bridge en Brave. Instala o habilita la extensión y recarga el panel.'));
    },timeoutMs);
    function onMsg(ev){
      if(ev.source!==window)return;
      const d=ev.data||{};
      if(d.source!=='DJGABO_UVR_BRIDGE'||d.type!=='UVR_BRIDGE_READY')return;
      if(done)return;done=true;clearTimeout(timer);window.removeEventListener('message',onMsg);
      resolve(d);
    }
    window.addEventListener('message',onMsg);
    window.postMessage({source:'DJGABO_PANEL',type:'UVR_BRIDGE_PING'},'*');
  });
}
function solicitarAudioQrPuente(url,target){
  return new Promise(async(resolve,reject)=>{
    try{await esperarPuenteUvr(1200);}catch(e){return reject(e);}
    const requestId='uvr-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
    let done=false;
    const timer=setTimeout(()=>{
      if(done)return;done=true;window.removeEventListener('message',onMsg);
      reject(new Error('El puente UVR tardó demasiado. Intenta nuevamente.'));
    },5*60*1000);
    function onMsg(ev){
      if(ev.source!==window)return;
      const d=ev.data||{};
      if(d.source!=='DJGABO_UVR_BRIDGE'||d.type!=='UVR_BRIDGE_RESULT'||d.requestId!==requestId)return;
      if(done)return;done=true;clearTimeout(timer);window.removeEventListener('message',onMsg);
      if(!d.ok)return reject(new Error(d.error||'El puente UVR falló.'));
      resolve(d);
    }
    window.addEventListener('message',onMsg);
    window.postMessage({
      source:'DJGABO_PANEL',type:'UVR_BRIDGE_FETCH',requestId:requestId,
      url:url,target:target,token:SESSION_TOKEN
    },'*');
  });
}
function aplicarAudioPuente(target,r){
  const name=String(r.name||'');
  const size=Number(r.size||0);
  const mime=String(r.mime||'');
  if(target==='voice'){
    if(!/\.mp3$/i.test(name))throw new Error('El puente no devolvió un MP3 para VOZ.');
    archivoSeleccionado={
      file:null,bridgeToken:String(r.bridge_token||''),nombreAudio:name,
      mimeType:mime||'audio/mpeg',duracion:Number(r.duration||0),tamanoBytes:size,origen:'UVR_BRIDGE'
    };
    document.getElementById('audioInfo').innerHTML='<div class="file-ok">✓ '+name+' · '+formatBytes(size)+' · desde UVR Bridge</div>';
  }else{
    if(!/\.wav$/i.test(name))throw new Error('El puente no devolvió un WAV para INSTRUMENTAL.');
    const ident=identidadDesdeInstrumental(name);
    if(ident){
      document.getElementById('inArtista').value=ident.artista;
      document.getElementById('inTitulo').value=ident.titulo;
      revisarDuplicado();
    }
    instrumentalSeleccionado={
      file:null,bridgeToken:String(r.bridge_token||''),nombreAudio:name,
      mimeType:mime||'audio/wav',duracion:Number(r.duration||0),tamanoBytes:size,
      origen:'UVR_BRIDGE',requiereNombreMaestroManual:!ident
    };
    document.getElementById('instrumentalInfo').innerHTML='<div class="file-ok">✓ '+name+' · '+formatBytes(size)+' · desde UVR Bridge</div>';
    if(ident){
      document.getElementById('masterInfo').innerHTML='Nombre maestro detectado: <b>'+ident.artista+' — '+ident.titulo+'</b><br>La voz se guardará automáticamente como <b>'+ident.artista+' - '+ident.titulo+' (Voz).mp3</b>.';
      document.getElementById('dropzoneInstrumental').classList.add('master-ready');
    }else{
      document.getElementById('masterInfo').innerHTML='<b>QR UVR recibido.</b> Completa <b>Artista</b> y <b>Título</b> arriba; esos datos serán el NOMBRE MAESTRO.';
      document.getElementById('dropzoneInstrumental').classList.remove('master-ready');
    }
  }
}
async function importarQrUvr(target){
  const btn=document.getElementById(target==='voice'?'btnQrVoz':'btnQrInstrumental');
  if(btn)btn.disabled=true;
  try{
    qrEstado(target,'Leyendo QR del portapapeles…','working');
    const imagen=await qrImagenDesdeClipboard();
    const url=await decodificarQrBlob(imagen);
    let u;try{u=new URL(url);}catch(_){throw new Error('El QR no contiene una URL válida.');}
    if(!/(^|\.)uvronline\.app$/i.test(u.hostname))throw new Error('El QR copiado no pertenece a UVR Online.');
    qrEstado(target,'QR leído ✓ · enviando al puente de Brave…','working');
    const r=await solicitarAudioQrPuente(url,target);
    aplicarAudioPuente(target,r);
    qrEstado(target,'✓ '+(target==='voice'?'Voz MP3':'Instrumental WAV')+' recibido desde UVR sin descarga manual','ok');
  }catch(e){
    qrEstado(target,'ERROR: '+(e&&e.message?e.message:String(e)),'error');
    toast(e&&e.message?e.message:String(e),'error');
  }finally{if(btn)btn.disabled=false;}
}
document.getElementById('btnQrVoz').addEventListener('click',()=>importarQrUvr('voice'));
document.getElementById('btnQrInstrumental').addEventListener('click',()=>importarQrUvr('instrumental'));'''

if "function esperarPuenteUvr(" not in panel:
    a=panel.find("function descargarAudioQr(url,target){")
    b=panel.find("document.getElementById('btnQrInstrumental').addEventListener('click',()=>importarQrUvr('instrumental'));",a)
    if a<0 or b<0:
        raise SystemExit('ANCHOR_NOT_FOUND: old QR import')
    end=b+len("document.getElementById('btnQrInstrumental').addEventListener('click',()=>importarQrUvr('instrumental'));")
    panel=panel[:a]+bridge_js+panel[end:]

old_validate="""  if(!archivoSeleccionado||!archivoSeleccionado.file)return toast('Selecciona la voz MP3.','error');
  if(!instrumentalSeleccionado||!instrumentalSeleccionado.file)return toast('Selecciona el instrumental WAV.','error');
  if(!/\\.mp3$/i.test(archivoSeleccionado.file.name))return toast('La VOZ debe ser MP3.','error');
  if(!/\\.wav$/i.test(instrumentalSeleccionado.file.name))return toast('El INSTRUMENTAL debe ser WAV.','error');"""
new_validate="""  if(!archivoSeleccionado||(!archivoSeleccionado.file&&!archivoSeleccionado.bridgeToken))return toast('Selecciona la voz MP3.','error');
  if(!instrumentalSeleccionado||(!instrumentalSeleccionado.file&&!instrumentalSeleccionado.bridgeToken))return toast('Selecciona el instrumental WAV.','error');
  const nombreVoz=archivoSeleccionado.file?archivoSeleccionado.file.name:archivoSeleccionado.nombreAudio;
  const nombreInst=instrumentalSeleccionado.file?instrumentalSeleccionado.file.name:instrumentalSeleccionado.nombreAudio;
  if(!/\\.mp3$/i.test(nombreVoz||''))return toast('La VOZ debe ser MP3.','error');
  if(!/\\.wav$/i.test(nombreInst||''))return toast('El INSTRUMENTAL debe ser WAV.','error');"""
if old_validate in panel:
    panel=panel.replace(old_validate,new_validate,1)

old_fd="""    fd.append('voice_duration',String(archivoSeleccionado.duracion||0));
    fd.append('voice',archivoSeleccionado.file,archivoSeleccionado.file.name);
    let nombreInstrumentalEnviar=instrumentalSeleccionado.file.name;
    if(instrumentalSeleccionado.requiereNombreMaestroManual){
      nombreInstrumentalEnviar=(artista+' - '+titulo).replace(/[\\\\/:*?\\"<>|]+/g,' ').replace(/\\s+/g,' ').trim()+'.wav';
    }
    fd.append('instrumental',instrumentalSeleccionado.file,nombreInstrumentalEnviar);"""
new_fd="""    fd.append('voice_duration',String(archivoSeleccionado.duracion||0));
    if(archivoSeleccionado.bridgeToken){
      fd.append('voice_bridge_token',archivoSeleccionado.bridgeToken);
    }else{
      fd.append('voice',archivoSeleccionado.file,archivoSeleccionado.file.name);
    }
    let nombreInstrumentalEnviar=instrumentalSeleccionado.file?instrumentalSeleccionado.file.name:instrumentalSeleccionado.nombreAudio;
    if(instrumentalSeleccionado.requiereNombreMaestroManual){
      nombreInstrumentalEnviar=(artista+' - '+titulo).replace(/[\\\\/:*?\\"<>|]+/g,' ').replace(/\\s+/g,' ').trim()+'.wav';
    }
    if(instrumentalSeleccionado.bridgeToken){
      fd.append('instrumental_bridge_token',instrumentalSeleccionado.bridgeToken);
      fd.append('instrumental_bridge_name',nombreInstrumentalEnviar);
    }else{
      fd.append('instrumental',instrumentalSeleccionado.file,nombreInstrumentalEnviar);
    }"""
if old_fd in panel:
    panel=panel.replace(old_fd,new_fd,1)

# Fallback por límites del bloque: el panel vivo puede tener pequeñas variaciones
# de espaciado/regex de versiones previas.
submit_anchor=panel.find("document.getElementById('btnEnviarNueva').addEventListener('click'")
if submit_anchor<0:
    raise SystemExit('ANCHOR_NOT_FOUND: submit nueva cancion')

if "const nombreVoz=archivoSeleccionado.file?" not in panel[submit_anchor:submit_anchor+9000]:
    va=panel.find("  if(!archivoSeleccionado",submit_anchor)
    vb=panel.find("\n\n  const btn=document.getElementById('btnEnviarNueva');",va)
    if va<0 or vb<0:
        raise SystemExit('ANCHOR_NOT_FOUND: submit validation bounds')
    panel=panel[:va]+new_validate+panel[vb:]

if "fd.append('voice_bridge_token'" not in panel[submit_anchor:submit_anchor+14000]:
    fa=panel.find("    fd.append('voice_duration'",submit_anchor)
    fb=panel.find("\n\n    const startData=await new Promise",fa)
    if fa<0 or fb<0:
        raise SystemExit('ANCHOR_NOT_FOUND: formdata bounds')
    panel=panel[:fa]+new_fd+panel[fb:]

SERVER.write_text(server,encoding='utf-8')
PANEL.write_text(panel,encoding='utf-8')

print('UVR_BRIDGE_PATCH=OK')
print('SERVER_STAGE=',"@app.post('/api/ai/bridge-stage')" in server)
print('SERVER_CREATE_BRIDGE=',"voice_bridge_token" in server and "instrumental_bridge_token" in server)
print('PANEL_BRIDGE=',"function esperarPuenteUvr(" in panel)
print('PANEL_BRIDGE_SUBMIT=',"voice_bridge_token" in panel and "instrumental_bridge_token" in panel)
