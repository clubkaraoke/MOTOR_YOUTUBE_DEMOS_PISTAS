#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
PANEL=ROOT/'panel.html'
if not PANEL.is_file():
    raise SystemExit('MISSING: '+str(PANEL))

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
shutil.copy2(PANEL,PANEL.with_name(PANEL.name+'.bak_qr_blob_'+stamp))
panel=PANEL.read_text(encoding='utf-8')

start=panel.find("function esperarPuenteUvr(")
end_marker="document.getElementById('btnQrInstrumental').addEventListener('click',()=>importarQrUvr('instrumental'));"
end0=panel.find(end_marker,start)
apply_direct = start>=0 and end0>=0
if not apply_direct and "function descargarAudioQrEnMemoria(" not in panel:
    raise SystemExit('ANCHOR_NOT_FOUND: UVR bridge block')

direct=r'''function nombreArchivoUvr(response,url,target){
  let nombre='';
  try{
    const cd=response.headers.get('content-disposition')||'';
    let m=cd.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
    if(m){try{nombre=decodeURIComponent(m[1].trim().replace(/^["']|["']$/g,''));}catch(_){}}
    if(!nombre){
      m=cd.match(/filename\s*=\s*"([^"]+)"/i)||cd.match(/filename\s*=\s*([^;]+)/i);
      if(m)nombre=m[1].trim().replace(/^["']|["']$/g,'');
    }
  }catch(_){}
  nombre=String(nombre||'').split(/[\\/]/).pop().replace(/[<>:"|?*\x00-\x1f]+/g,' ').replace(/\s+/g,' ').trim();
  const ext=target==='voice'?'.mp3':'.wav';
  if(!nombre || !nombre.toLowerCase().endsWith(ext)){
    nombre=target==='voice'?'UVR Voz.mp3':'UVR Instrumental.wav';
  }
  return nombre;
}
async function descargarAudioQrEnMemoria(url,target){
  qrEstado(target,'QR leído ✓ · cargando audio en memoria…','working');
  const response=await fetch(url,{method:'GET',cache:'no-store',redirect:'follow'});
  if(!response.ok)throw new Error('UVR respondió HTTP '+response.status+'.');
  const ctype=String(response.headers.get('content-type')||'').toLowerCase();
  if(ctype.includes('text/html'))throw new Error('UVR devolvió una página en vez del audio.');
  const blob=await response.blob();
  if(!blob.size)throw new Error('UVR devolvió un audio vacío.');
  if(blob.size>350*1024*1024)throw new Error('El audio supera el límite de 350 MB.');
  const nombre=nombreArchivoUvr(response,url,target);
  const mime=target==='voice'?'audio/mpeg':'audio/wav';
  return new File([blob],nombre,{type:blob.type||mime,lastModified:Date.now()});
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

    const file=await descargarAudioQrEnMemoria(url,target);
    if(target==='voice'){
      procesarArchivoAudio(file);
      qrEstado(target,'✓ Voz MP3 cargada en memoria: '+file.name,'ok');
    }else{
      procesarArchivoInstrumental(file,{permitirNombreManual:true,origen:'UVR_QR_MEMORIA'});
      qrEstado(target,'✓ Instrumental WAV cargado en memoria: '+file.name,'ok');
    }
  }catch(e){
    const msg=e&&e.message?e.message:String(e);
    qrEstado(target,'ERROR: '+msg,'error');
    toast(msg,'error');
  }finally{
    if(btn)btn.disabled=false;
  }
}
document.getElementById('btnQrVoz').addEventListener('click',()=>importarQrUvr('voice'));
document.getElementById('btnQrInstrumental').addEventListener('click',()=>importarQrUvr('instrumental'));'''

if apply_direct:
    end=end0+len(end_marker)
    panel=panel[:start]+direct+panel[end:]
else:
    print('QR_BLOB_PATCH=ALREADY_APPLIED')

# Brave puede no exponer BarcodeDetector. El QR de UVR puede llegar como una
# imagen mínima (por ejemplo 37x37, prácticamente 1 px por módulo). jsQR
# necesita una zona silenciosa y más píxeles por módulo para decodificarlo
# de forma consistente. Mantenemos el flujo existente y sólo reforzamos el
# fallback del lector.
reader_start=panel.find("async function decodificarQrConJsQr(blob){")
reader_end=panel.find("async function decodificarQrBlob(blob){",reader_start)
if reader_start<0 or reader_end<0:
    raise SystemExit('ANCHOR_NOT_FOUND: QR decoder')

robust_reader=r'''async function decodificarQrConJsQr(blob){
  // QR_UPSCALE_FALLBACK_V2
  const jsQRfn=await cargarJsQrCompat();
  const bmp=await createImageBitmap(blob);
  try{
    function probarCanvas(canvas){
      const ctx=canvas.getContext('2d',{willReadFrequently:true});
      const img=ctx.getImageData(0,0,canvas.width,canvas.height);
      const code=jsQRfn(img.data,img.width,img.height,{inversionAttempts:'attemptBoth'});
      const valor=code&&String(code.data||'').trim();
      return valor||'';
    }

    // Primer intento: imagen original, igual que antes.
    {
      const canvas=document.createElement('canvas');
      canvas.width=bmp.width;canvas.height=bmp.height;
      const ctx=canvas.getContext('2d',{willReadFrequently:true});
      ctx.drawImage(bmp,0,0);
      const valor=probarCanvas(canvas);
      if(valor)return valor;
    }

    // Fallback robusto para QR diminutos copiados desde UVR.
    // Escalado nearest-neighbor + quiet zone blanca de 4 módulos aprox.
    for(const escala of [4,8,12,16]){
      const borde=4*escala;
      const canvas=document.createElement('canvas');
      canvas.width=bmp.width*escala+borde*2;
      canvas.height=bmp.height*escala+borde*2;
      const ctx=canvas.getContext('2d',{willReadFrequently:true});
      ctx.imageSmoothingEnabled=false;
      ctx.fillStyle='#fff';
      ctx.fillRect(0,0,canvas.width,canvas.height);
      ctx.drawImage(bmp,borde,borde,bmp.width*escala,bmp.height*escala);
      const valor=probarCanvas(canvas);
      if(valor)return valor;
    }

    throw new Error('No pude leer el QR de la imagen copiada. Intenta copiar el QR nuevamente desde UVR.');
  }finally{try{bmp.close&&bmp.close();}catch(_){}}
}
'''

if "QR_UPSCALE_FALLBACK_V2" not in panel[reader_start:reader_end]:
    panel=panel[:reader_start]+robust_reader+panel[reader_end:]

PANEL.write_text(panel,encoding='utf-8')
print('QR_BLOB_PATCH=OK')
print('BLOB_FN=',"function descargarAudioQrEnMemoria(" in panel)
print('OLD_BRIDGE=',"function esperarPuenteUvr(" in panel)
print('QR_UPSCALE_FALLBACK_V2=',"QR_UPSCALE_FALLBACK_V2" in panel)
