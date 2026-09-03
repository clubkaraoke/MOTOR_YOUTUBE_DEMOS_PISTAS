#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
PANEL=ROOT/'panel.html'
VENDOR_QR=ROOT/'vendor'/'jsQR.js'
SRC_VENDOR_QR=Path('/tmp/jsQR.js')

def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit('ANCHOR_NOT_FOUND: '+label)
    return text.replace(old,new,1)

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
for p in (SERVER,PANEL):
    if not p.is_file():
        raise SystemExit('MISSING: '+str(p))
    shutil.copy2(p,p.with_name(p.name+'.bak_qr_uvr_'+stamp))

if not SRC_VENDOR_QR.is_file():
    raise SystemExit('MISSING: /tmp/jsQR.js')
VENDOR_QR.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(SRC_VENDOR_QR,VENDOR_QR)

server=SERVER.read_text(encoding='utf-8')
if "/api/ai/qr-import" not in server:
    server=replace_once(
        server,
        "from pathlib import Path\nfrom flask import Flask, request, send_file, jsonify, abort, redirect, Response\n",
        "from pathlib import Path\nfrom urllib.parse import urlparse, unquote\nfrom flask import Flask, request, send_file, jsonify, abort, redirect, Response\n",
        'server imports'
    )
    anchor="@app.post('/api/instrumentals/auto-match')\ndef auto_match_instrumental_api():"
    block=r'''
def _uvr_qr_filename(response, source_url, target):
    """Obtiene un nombre útil sin confiar en rutas arbitrarias del QR."""
    cd=str(response.headers.get('Content-Disposition') or '')
    name=''
    m=re.search(r"""filename\*\s*=\s*UTF-8''([^;]+)""",cd,re.I)
    if m:
        try: name=unquote(m.group(1).strip().strip('"'))
        except Exception: name=''
    if not name:
        m=re.search(r'filename\s*=\s*"([^"]+)"',cd,re.I) or re.search(r'filename\s*=\s*([^;]+)',cd,re.I)
        if m: name=m.group(1).strip().strip('"')
    name=Path(str(name or '')).name
    parsed=urlparse(str(response.url or source_url))
    url_path=unquote(parsed.path or '')
    if not name or Path(name).suffix.lower() not in ('.mp3','.wav'):
        candidate=Path(url_path).name
        if Path(candidate).suffix.lower() in ('.mp3','.wav'):
            name=candidate
    wanted='.mp3' if target=='voice' else '.wav'
    if Path(name).suffix.lower()!=wanted:
        last=Path(url_path).name.lower()
        ctype=str(response.headers.get('Content-Type') or '').lower()
        format_ok=(wanted=='.mp3' and (last=='mp3' or 'audio/mpeg' in ctype or 'audio/mp3' in ctype)) or \
                  (wanted=='.wav' and (last=='wav' or 'audio/wav' in ctype or 'audio/x-wav' in ctype or 'wave' in ctype))
        if not format_ok:
            raise ValueError('El QR no apunta al formato esperado: '+('MP3 para VOZ.' if target=='voice' else 'WAV para INSTRUMENTAL.'))
        name=('UVR Voz.mp3' if target=='voice' else 'UVR Instrumental.wav')
    return safe_name(name)

@app.post('/api/ai/qr-import')
def ai_qr_import():
    """IA TEST: proxy seguro UVR QR -> navegador. No publica en Drive/Dropbox."""
    if not TEST_MODE:
        return jsonify(ok=False,error='Importación QR disponible sólo en IA TEST.'),403
    d=request.get_json(silent=True) or {}
    try:
        session(d.get('token'),'ADMIN')
        target=str(d.get('target') or '').strip().lower()
        if target not in ('voice','instrumental'):
            raise ValueError('Destino QR inválido.')
        source_url=str(d.get('url') or '').strip()
        parsed=urlparse(source_url)
        host=(parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme!='https' or host not in ('uvronline.app','www.uvronline.app'):
            raise ValueError('El QR debe pertenecer a UVR Online (uvronline.app).')
        try:
            upstream=requests.get(
                source_url,stream=True,allow_redirects=True,
                headers={'User-Agent':'DJGABO-CDG-IA-TEST/1.0'},
                timeout=(20,240)
            )
        except requests.RequestException as e:
            raise ValueError('No pude descargar el audio del QR de UVR: '+str(e)) from e
        if upstream.status_code>=400:
            upstream.close()
            raise ValueError('UVR respondió HTTP '+str(upstream.status_code)+'. Genera un QR nuevo y vuelve a intentar.')
        max_bytes=350*1024*1024
        try: declared=int(upstream.headers.get('Content-Length') or 0)
        except Exception: declared=0
        if declared>max_bytes:
            upstream.close()
            raise ValueError('El audio del QR supera el límite local de 350 MB.')
        filename=_uvr_qr_filename(upstream,source_url,target)
        mime='audio/mpeg' if target=='voice' else 'audio/wav'
        filename_b64=base64.b64encode(filename.encode('utf-8')).decode('ascii')
        def generate():
            total=0
            try:
                for chunk in upstream.iter_content(chunk_size=1024*1024):
                    if not chunk: continue
                    total+=len(chunk)
                    if total>max_bytes:
                        raise RuntimeError('El audio del QR superó 350 MB durante la descarga.')
                    yield chunk
            finally:
                upstream.close()
        headers={
            'Content-Disposition':"attachment; filename*=UTF-8''"+requests.utils.quote(filename),
            'X-DJGABO-Filename-B64':filename_b64,
            'Cache-Control':'no-store',
        }
        if declared>0: headers['Content-Length']=str(declared)
        return Response(generate(),status=200,mimetype=mime,headers=headers)
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('IA TEST QR import')
        return jsonify(ok=False,error='No se pudo importar el QR de UVR: '+str(e)),500


'''
    server=replace_once(server,anchor,block+anchor,'server qr endpoint')
    SERVER.write_text(server,encoding='utf-8')

server=SERVER.read_text(encoding='utf-8')
if "@app.get('/vendor/jsQR.js')" not in server:
    route_anchor="@app.post('/api/instrumentals/auto-match')\ndef auto_match_instrumental_api():"
    if route_anchor not in server:
        raise SystemExit('ANCHOR_NOT_FOUND: vendor qr route')
    vendor_route="""@app.get('/vendor/jsQR.js')
def vendor_jsqr():
    \"\"\"Lector QR integrado al IA TEST; evita depender de BarcodeDetector del navegador.\"\"\"
    p=Path(__file__).resolve().parent/'vendor'/'jsQR.js'
    if not p.is_file():
        abort(404)
    return send_file(str(p),mimetype='application/javascript',conditional=True)

"""
    server=server.replace(route_anchor,vendor_route+route_anchor,1)
    SERVER.write_text(server,encoding='utf-8')

panel=PANEL.read_text(encoding='utf-8')
if 'id="btnQrVoz"' not in panel:
    panel=replace_once(
        panel,
        '    <div id="audioInfo"></div>\n    <div id="warnBox"></div>\n',
        '    <div id="audioInfo"></div>\n    <div class="qr-import-row"><button class="btn btn-sm qr-import-btn" type="button" id="btnQrVoz">▣ Pegar QR UVR</button><span id="qrVozEstado">Copia el QR de VOZ (MP3) en UVR y pulsa aquí.</span></div>\n    <div id="warnBox"></div>\n',
        'panel qr voice'
    )
    panel=replace_once(
        panel,
        '    <div id="instrumentalInfo"></div>\n    <div id="masterInfo" class="master-note"></div>\n',
        '    <div id="instrumentalInfo"></div>\n    <div class="qr-import-row"><button class="btn btn-sm qr-import-btn" type="button" id="btnQrInstrumental">▣ Pegar QR UVR</button><span id="qrInstrumentalEstado">Copia el QR del INSTRUMENTAL (WAV) en UVR y pulsa aquí.</span></div>\n    <div id="masterInfo" class="master-note"></div>\n',
        'panel qr instrumental'
    )
    panel=replace_once(
        panel,
        ".ia-progress-meta{font-size:10.5px;color:var(--text-3)}#iaProgressTime{font-family:var(--mono);white-space:nowrap}\n",
        ".ia-progress-meta{font-size:10.5px;color:var(--text-3)}#iaProgressTime{font-family:var(--mono);white-space:nowrap}\n.qr-import-row{display:flex;align-items:center;gap:9px;margin:7px 0 2px;min-height:30px}.qr-import-row span{font-size:10.5px;color:var(--text-3);line-height:1.25}.qr-import-btn{border-color:rgba(139,92,246,.45)!important;background:rgba(139,92,246,.08)!important;color:#c4b5fd!important;white-space:nowrap}.qr-import-btn:hover{background:rgba(139,92,246,.16)!important}.qr-import-row.qr-working span{color:var(--teal)}.qr-import-row.qr-ok span{color:#7ee7c4}.qr-import-row.qr-error span{color:var(--danger)}\n",
        'panel qr css'
    )
    panel=replace_once(
        panel,
        "  document.getElementById('audioInfo').innerHTML = '';\n  document.getElementById('instrumentalInfo').innerHTML = '';\n",
        "  document.getElementById('audioInfo').innerHTML = '';\n  document.getElementById('instrumentalInfo').innerHTML = '';\n  const qv=document.getElementById('qrVozEstado');if(qv)qv.textContent='Copia el QR de VOZ (MP3) en UVR y pulsa aquí.';\n  const qi=document.getElementById('qrInstrumentalEstado');if(qi)qi.textContent='Copia el QR del INSTRUMENTAL (WAV) en UVR y pulsa aquí.';\n  document.querySelectorAll('.qr-import-row').forEach(x=>x.classList.remove('qr-working','qr-ok','qr-error'));\n",
        'panel qr reset'
    )

    old=r'''function procesarArchivoInstrumental(file){
  if(!file || !/\.wav$/i.test(file.name||'')){
    instrumentalSeleccionado=null;
    document.getElementById('instrumentalInfo').innerHTML='<div class="warn-box">El INSTRUMENTAL debe ser un archivo WAV.</div>';
    document.getElementById('masterInfo').textContent='';
    return;
  }
  const ident=identidadDesdeInstrumental(file.name);
  if(!ident){
    instrumentalSeleccionado=null;
    document.getElementById('instrumentalInfo').innerHTML='<div class="warn-box">El instrumental debe llamarse: ARTISTA - TÍTULO ...</div>';
    document.getElementById('masterInfo').textContent='';
    return;
  }
  // El instrumental manda SIEMPRE, incluso si la voz tiene un nombre desordenado.
  document.getElementById('inArtista').value=ident.artista;
  document.getElementById('inTitulo').value=ident.titulo;
  revisarDuplicado();
  leerDuracionArchivo(file,function(duracion){
    // Guardamos el File real. NO FileReader, NO Base64, incluso si el WAV pesa cientos de MB.
    instrumentalSeleccionado={file:file,nombreAudio:file.name,mimeType:file.type||'audio/wav',duracion:duracion||0,tamanoBytes:file.size};
    document.getElementById('instrumentalInfo').innerHTML='<div class="file-ok">✓ '+file.name+' · '+formatBytes(file.size)+' · '+formatDuracion(duracion)+'</div>';
    document.getElementById('masterInfo').innerHTML='Nombre maestro detectado: <b>'+ident.artista+' — '+ident.titulo+'</b><br>La voz se guardará automáticamente como <b>'+ident.artista+' - '+ident.titulo+' (Voz).mp3</b>.';
    document.getElementById('dropzoneInstrumental').classList.add('master-ready');
  });
}'''
    new=r'''function procesarArchivoInstrumental(file,opciones){
  opciones=opciones||{};
  if(!file || !/\.wav$/i.test(file.name||'')){
    instrumentalSeleccionado=null;
    document.getElementById('instrumentalInfo').innerHTML='<div class="warn-box">El INSTRUMENTAL debe ser un archivo WAV.</div>';
    document.getElementById('masterInfo').textContent='';
    return;
  }
  const ident=identidadDesdeInstrumental(file.name);
  if(!ident && !opciones.permitirNombreManual){
    instrumentalSeleccionado=null;
    document.getElementById('instrumentalInfo').innerHTML='<div class="warn-box">El instrumental debe llamarse: ARTISTA - TÍTULO ...</div>';
    document.getElementById('masterInfo').textContent='';
    return;
  }
  if(ident){
    document.getElementById('inArtista').value=ident.artista;
    document.getElementById('inTitulo').value=ident.titulo;
    revisarDuplicado();
  }
  leerDuracionArchivo(file,function(duracion){
    instrumentalSeleccionado={
      file:file,nombreAudio:file.name,mimeType:file.type||'audio/wav',
      duracion:duracion||0,tamanoBytes:file.size,
      origen:opciones.origen||'ARCHIVO',
      requiereNombreMaestroManual:!ident
    };
    document.getElementById('instrumentalInfo').innerHTML='<div class="file-ok">✓ '+file.name+' · '+formatBytes(file.size)+' · '+formatDuracion(duracion)+'</div>';
    if(ident){
      document.getElementById('masterInfo').innerHTML='Nombre maestro detectado: <b>'+ident.artista+' — '+ident.titulo+'</b><br>La voz se guardará automáticamente como <b>'+ident.artista+' - '+ident.titulo+' (Voz).mp3</b>.';
      document.getElementById('dropzoneInstrumental').classList.add('master-ready');
    }else{
      document.getElementById('masterInfo').innerHTML='<b>QR UVR recibido.</b> Este WAV no trae “ARTISTA - TÍTULO”. Completa <b>Artista</b> y <b>Título</b> arriba; esos datos serán el NOMBRE MAESTRO al crear el trabajo.';
      document.getElementById('dropzoneInstrumental').classList.remove('master-ready');
    }
  });
}'''
    panel=replace_once(panel,old,new,'panel instrument qr-aware')

    helper_anchor='''/**
 * Detecta el patrón "Artista - Título" en el nombre del archivo de audio y llena'''
    helpers=r'''function qrEstado(target,mensaje,estado){
  const id=target==='voice'?'qrVozEstado':'qrInstrumentalEstado';
  const el=document.getElementById(id);if(!el)return;
  el.textContent=mensaje||'';
  const row=el.closest('.qr-import-row');
  if(row){row.classList.remove('qr-working','qr-ok','qr-error');if(estado)row.classList.add('qr-'+estado);}
}
async function qrImagenDesdeClipboard(){
  if(!navigator.clipboard||typeof navigator.clipboard.read!=='function'){
    throw new Error('Este navegador no permite leer imágenes del portapapeles. Abre el panel por HTTPS y habilita el permiso del portapapeles.');
  }
  const items=await navigator.clipboard.read();
  for(const item of items){
    const tipo=(item.types||[]).find(t=>String(t).startsWith('image/'));
    if(tipo)return await item.getType(tipo);
  }
  throw new Error('No encuentro una imagen en el portapapeles. En UVR usa “Enviar a otro dispositivo” y copia el QR.');
}
let _jsQrCompatPromise=null;
function cargarJsQrCompat(){
  if(typeof window.jsQR==='function')return Promise.resolve(window.jsQR);
  if(_jsQrCompatPromise)return _jsQrCompatPromise;
  const fuentes=[
    '/cdg-editor-ia/vendor/jsQR.js',
    'https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js'
  ];
  _jsQrCompatPromise=new Promise((resolve,reject)=>{
    let i=0;
    const intentar=()=>{
      if(i>=fuentes.length)return reject(new Error('No pude cargar el lector QR compatible. Revisa la conexión a Internet y vuelve a intentar.'));
      const s=document.createElement('script');
      s.src=fuentes[i++];s.async=true;
      s.onload=()=>typeof window.jsQR==='function'?resolve(window.jsQR):intentar();
      s.onerror=()=>{try{s.remove();}catch(_){}intentar();};
      document.head.appendChild(s);
    };
    intentar();
  });
  return _jsQrCompatPromise;
}
async function decodificarQrConJsQr(blob){
  const jsQRfn=await cargarJsQrCompat();
  const bmp=await createImageBitmap(blob);
  try{
    const canvas=document.createElement('canvas');
    canvas.width=bmp.width;canvas.height=bmp.height;
    const ctx=canvas.getContext('2d',{willReadFrequently:true});
    ctx.drawImage(bmp,0,0);
    const img=ctx.getImageData(0,0,canvas.width,canvas.height);
    const code=jsQRfn(img.data,img.width,img.height,{inversionAttempts:'attemptBoth'});
    const valor=code&&String(code.data||'').trim();
    if(!valor)throw new Error('No pude leer el QR de la imagen copiada. Intenta copiar el QR nuevamente desde UVR.');
    return valor;
  }finally{try{bmp.close&&bmp.close();}catch(_){}}
}
async function decodificarQrBlob(blob){
  if(typeof BarcodeDetector!=='undefined'){
    try{
      let formatos=[];
      try{formatos=await BarcodeDetector.getSupportedFormats();}catch(_){}
      if(!formatos.length||formatos.includes('qr_code')){
        const detector=new BarcodeDetector({formats:['qr_code']});
        const bmp=await createImageBitmap(blob);
        try{
          const codigos=await detector.detect(bmp);
          const valor=codigos&&codigos[0]&&String(codigos[0].rawValue||'').trim();
          if(valor)return valor;
        }finally{try{bmp.close&&bmp.close();}catch(_){}}
      }
    }catch(_){}
  }
  return await decodificarQrConJsQr(blob);
}
function utf8DesdeBase64(b64){
  try{
    const bin=atob(b64||'');const bytes=new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }catch(_){return '';}
}
function descargarAudioQr(url,target){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.open('POST','/cdg-editor-ia/api/ai/qr-import',true);
    xhr.responseType='blob';
    xhr.setRequestHeader('Content-Type','application/json');
    xhr.onprogress=(e)=>{
      const mb=(e.loaded/1048576).toFixed(1);
      const total=e.lengthComputable?' / '+(e.total/1048576).toFixed(1)+' MB':' MB';
      qrEstado(target,'Descargando audio desde UVR… '+mb+total,'working');
    };
    xhr.onerror=()=>reject(new Error('Se cortó la descarga del audio desde UVR.'));
    xhr.onload=async()=>{
      if(xhr.status<200||xhr.status>=300){
        let msg='UVR no pudo importar el audio.';
        try{const raw=await (xhr.response||new Blob()).text();const d=JSON.parse(raw||'{}');msg=d.error||msg;}catch(_){}
        return reject(new Error(msg));
      }
      const name=utf8DesdeBase64(xhr.getResponseHeader('X-DJGABO-Filename-B64')||'') ||
        (target==='voice'?'UVR Voz.mp3':'UVR Instrumental.wav');
      resolve({blob:xhr.response,name:name});
    };
    xhr.send(JSON.stringify({token:SESSION_TOKEN,url:url,target:target}));
  });
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
    qrEstado(target,'QR leído ✓ · trayendo audio…','working');
    const r=await descargarAudioQr(url,target);
    const mime=target==='voice'?'audio/mpeg':'audio/wav';
    const file=new File([r.blob],r.name,{type:r.blob.type||mime,lastModified:Date.now()});
    if(target==='voice'){
      procesarArchivoAudio(file);
      qrEstado(target,'✓ Voz MP3 recibida desde UVR: '+file.name,'ok');
    }else{
      procesarArchivoInstrumental(file,{permitirNombreManual:true,origen:'UVR_QR'});
      qrEstado(target,'✓ Instrumental WAV recibido desde UVR: '+file.name,'ok');
    }
  }catch(e){
    qrEstado(target,'ERROR: '+(e&&e.message?e.message:String(e)),'error');
    toast(e&&e.message?e.message:String(e),'error');
  }finally{if(btn)btn.disabled=false;}
}
document.getElementById('btnQrVoz').addEventListener('click',()=>importarQrUvr('voice'));
document.getElementById('btnQrInstrumental').addEventListener('click',()=>importarQrUvr('instrumental'));

'''
    panel=replace_once(panel,helper_anchor,helpers+helper_anchor,'panel qr helpers')

    panel=replace_once(
        panel,
        "    fd.append('voice',archivoSeleccionado.file,archivoSeleccionado.file.name);\n    fd.append('instrumental',instrumentalSeleccionado.file,instrumentalSeleccionado.file.name);\n",
        "    fd.append('voice',archivoSeleccionado.file,archivoSeleccionado.file.name);\n    let nombreInstrumentalEnviar=instrumentalSeleccionado.file.name;\n    if(instrumentalSeleccionado.requiereNombreMaestroManual){\n      nombreInstrumentalEnviar=(artista+' - '+titulo).replace(/[\\\\/:*?\\\"<>|]+/g,' ').replace(/\\s+/g,' ').trim()+'.wav';\n    }\n    fd.append('instrumental',instrumentalSeleccionado.file,nombreInstrumentalEnviar);\n",
        'panel manual master submit'
    )
    PANEL.write_text(panel,encoding='utf-8')

# Upgrade for installations that already had the QR buttons but still used BarcodeDetector-only logic.
old_decoder=r'''async function decodificarQrBlob(blob){
  if(typeof BarcodeDetector==='undefined'){
    throw new Error('El lector QR del navegador no está disponible en este equipo. Prueba con Chrome actualizado.');
  }
  let formatos=[];
  try{formatos=await BarcodeDetector.getSupportedFormats();}catch(_){}
  if(formatos.length&&!formatos.includes('qr_code'))throw new Error('Este navegador no admite QR en BarcodeDetector.');
  const detector=new BarcodeDetector({formats:['qr_code']});
  const bmp=await createImageBitmap(blob);
  try{
    const codigos=await detector.detect(bmp);
    const valor=codigos&&codigos[0]&&String(codigos[0].rawValue||'').trim();
    if(!valor)throw new Error('No pude leer el QR de la imagen copiada.');
    return valor;
  }finally{try{bmp.close&&bmp.close();}catch(_){}}
}'''
new_decoder=r'''let _jsQrCompatPromise=null;
function cargarJsQrCompat(){
  if(typeof window.jsQR==='function')return Promise.resolve(window.jsQR);
  if(_jsQrCompatPromise)return _jsQrCompatPromise;
  const fuentes=[
    '/cdg-editor-ia/vendor/jsQR.js',
    'https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js'
  ];
  _jsQrCompatPromise=new Promise((resolve,reject)=>{
    let i=0;
    const intentar=()=>{
      if(i>=fuentes.length)return reject(new Error('No pude cargar el lector QR compatible. Revisa la conexión a Internet y vuelve a intentar.'));
      const s=document.createElement('script');
      s.src=fuentes[i++];s.async=true;
      s.onload=()=>typeof window.jsQR==='function'?resolve(window.jsQR):intentar();
      s.onerror=()=>{try{s.remove();}catch(_){}intentar();};
      document.head.appendChild(s);
    };
    intentar();
  });
  return _jsQrCompatPromise;
}
async function decodificarQrConJsQr(blob){
  const jsQRfn=await cargarJsQrCompat();
  const bmp=await createImageBitmap(blob);
  try{
    const canvas=document.createElement('canvas');
    canvas.width=bmp.width;canvas.height=bmp.height;
    const ctx=canvas.getContext('2d',{willReadFrequently:true});
    ctx.drawImage(bmp,0,0);
    const img=ctx.getImageData(0,0,canvas.width,canvas.height);
    const code=jsQRfn(img.data,img.width,img.height,{inversionAttempts:'attemptBoth'});
    const valor=code&&String(code.data||'').trim();
    if(!valor)throw new Error('No pude leer el QR de la imagen copiada. Intenta copiar el QR nuevamente desde UVR.');
    return valor;
  }finally{try{bmp.close&&bmp.close();}catch(_){}}
}
async function decodificarQrBlob(blob){
  if(typeof BarcodeDetector!=='undefined'){
    try{
      let formatos=[];
      try{formatos=await BarcodeDetector.getSupportedFormats();}catch(_){}
      if(!formatos.length||formatos.includes('qr_code')){
        const detector=new BarcodeDetector({formats:['qr_code']});
        const bmp=await createImageBitmap(blob);
        try{
          const codigos=await detector.detect(bmp);
          const valor=codigos&&codigos[0]&&String(codigos[0].rawValue||'').trim();
          if(valor)return valor;
        }finally{try{bmp.close&&bmp.close();}catch(_){}}
      }
    }catch(_){}
  }
  return await decodificarQrConJsQr(blob);
}'''

panel=PANEL.read_text(encoding='utf-8')
changed=False
if old_decoder in panel:
    panel=panel.replace(old_decoder,new_decoder,1)
    changed=True
old_clip="throw new Error('Este navegador no permite leer imágenes del portapapeles. Usa Chrome actualizado y abre el panel por HTTPS.');"
new_clip="throw new Error('Este navegador no permite leer imágenes del portapapeles. Abre el panel por HTTPS y habilita el permiso del portapapeles.');"
if old_clip in panel:
    panel=panel.replace(old_clip,new_clip,1)
    changed=True
if changed:
    PANEL.write_text(panel,encoding='utf-8')

print('QR_UVR_PATCH=OK')
print('SERVER_MARKER',"/api/ai/qr-import" in SERVER.read_text(encoding='utf-8'))
print('PANEL_MARKER','id="btnQrVoz"' in PANEL.read_text(encoding='utf-8'))
