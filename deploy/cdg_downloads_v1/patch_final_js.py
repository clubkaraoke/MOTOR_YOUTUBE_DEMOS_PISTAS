#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER="DJGABO_CDG_DOWNLOADS_UI_V1"

def one(t,old,new,label):
    n=t.count(old)
    if n!=1: raise RuntimeError(f"{label}: esperaba 1, encontre {n}")
    return t.replace(old,new,1)

def main():
    if len(sys.argv)!=2: raise SystemExit("uso: patch_final_js.py cdg-final-preview.js")
    p=Path(sys.argv[1]);t=p.read_text(encoding="utf-8")
    if MARKER in t:
        print("JS_ALREADY=YES");return

    t=one(t,
'''  const P={
    active:false, loadedJob:'', decoder:new CDGDecoder(), data:null, processed:0,
    voice:new Audio(), raf:0, loading:false
  };''',
'''  const P={
    active:false, loadedJob:'', decoder:new CDGDecoder(), data:null, processed:0,
    voice:new Audio(), raf:0, loading:false, meta:null
  };''',"state")

    a='''      .cdgfNote{font:9px var(--mono,monospace);color:#778395;line-height:1.35}'''
    b=a+'''
      /* DJGABO_CDG_DOWNLOADS_UI_V1 */
      .cdgfDownloads{border:1px solid #2d3440;background:#11151d;border-radius:8px;padding:9px;display:flex;flex-direction:column;gap:8px}
      .cdgfDownloadButtons{display:grid;grid-template-columns:1.45fr .75fr 1fr;gap:6px}
      .cdgfDownloadButtons .cdgfBtn{padding:7px 6px;white-space:nowrap}
      .cdgfDownloadButtons .cdgfBtn:disabled{opacity:.38;cursor:not-allowed}
      #cdgDownloadStatus{font:9px/1.35 var(--mono,monospace);color:#778395}
      @media(max-width:520px){.cdgfDownloadButtons{grid-template-columns:1fr}.cdgfDownloadButtons .cdgfBtn{width:100%}}'''
    t=one(t,a,b,"css")

    a='''      <button class="cdgfBtn" id="cdgFinalRender" type="button">↻ Crear / actualizar CDG final</button>'''
    b=a+'''
      <div class="cdgfDownloads">
        <div class="cdgfMixTitle">Descargas</div>
        <div class="cdgfDownloadButtons">
          <button class="cdgfBtn" id="cdgDownloadZip" type="button" disabled>⬇ ZIP · CDG + WAV</button>
          <button class="cdgfBtn" id="cdgDownloadCdg" type="button" disabled>⬇ CDG</button>
          <button class="cdgfBtn" id="cdgDownloadWav" type="button" disabled>⬇ WAV instrumental</button>
        </div>
        <div id="cdgDownloadStatus">Se habilitan según los archivos disponibles del trabajo.</div>
      </div>'''
    t=one(t,a,b,"panel")

    t=one(t,"    q('cdgFinalRender').onclick=async()=>{",
'''    q('cdgDownloadZip').onclick=()=>downloadAsset('zip');
    q('cdgDownloadCdg').onclick=()=>downloadAsset('cdg');
    q('cdgDownloadWav').onclick=()=>downloadAsset('wav');
    q('cdgFinalRender').onclick=async()=>{''',"handlers")

    helpers=r'''  function updateDownloads(meta){
    P.meta=meta||null;
    const hasCdg=!!meta?.has_cdg,hasWav=!!meta?.has_wav;
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
  }
  function downloadAsset(kind){
    const meta=P.meta||{};
    const path=kind==='zip'?meta.download_zip_url:kind==='wav'?meta.download_wav_url:meta.download_cdg_url;
    if(!path){setStatus('Ese archivo todavía no está disponible para descargar.','warn');return;}
    const a=document.createElement('a');a.href=tokenUrl(path);a.style.display='none';document.body.appendChild(a);a.click();setTimeout(()=>a.remove(),1000);
  }

'''
    t=one(t,"  async function loadFinal(force=false){",helpers+"  async function loadFinal(force=false){","helpers")

    t=one(t,
'''      const meta=await mr.json(); if(!mr.ok||meta.ok===false)throw new Error(meta.error||'No pude leer el estado del render.');
      if(!meta.has_cdg){''',
'''      const meta=await mr.json(); if(!mr.ok||meta.ok===false)throw new Error(meta.error||'No pude leer el estado del render.');
      updateDownloads(meta);
      if(!meta.has_cdg){''',"meta")

    t=one(t,
'''    }catch(e){
      P.data=null;clearCanvas();setStatus((e&&e.message)||String(e),'bad');
    }finally{P.loading=false;}''',
'''    }catch(e){
      P.data=null;P.meta=null;updateDownloads(null);clearCanvas();setStatus((e&&e.message)||String(e),'bad');
    }finally{P.loading=false;}''',"error state")

    p.write_text(t,encoding="utf-8")
    print("JS_PATCH=OK")

if __name__=="__main__":main()
