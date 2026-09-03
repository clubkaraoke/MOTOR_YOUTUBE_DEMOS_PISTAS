#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARKER="DJGABO_POST_EXPORT_CHOICE_V1"

def replace_once(text:str, old:str, new:str, label:str)->str:
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontre {n}")
    return text.replace(old,new,1)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default="/opt/djgabo-cdg")
    args=ap.parse_args()
    p=Path(args.root)/"editor_v1"/"index.html"
    if not p.is_file():
        raise SystemExit(f"No existe {p}")
    text=p.read_text(encoding="utf-8")
    if MARKER in text:
        print("PATCH=ALREADY_PRESENT")
        return

    # Quitar la pastilla IA TEST / NO PRODUCCIÓN del panel original.
    text,n=re.subn(
        r'\n?<div id="CDG_EDITOR_IA_TEST_BADGE"[^>]*>IA TEST · NO PRODUCCIÓN</div>\n?',
        '\n',
        text,
        count=1,
    )
    if n!=1:
        raise RuntimeError(f"badge IA TEST: esperaba 1 coincidencia y encontre {n}")

    css=r'''
/* DJGABO_POST_EXPORT_CHOICE_V1 */
#postExportChoice{position:fixed;inset:0;z-index:100005;background:rgba(0,0,0,.76);display:flex;align-items:center;justify-content:center;padding:22px}
#postExportChoice[hidden]{display:none!important}
#postExportCard{width:min(560px,94vw);background:#171922;border:1px solid #3b4251;border-radius:12px;box-shadow:0 24px 80px rgba(0,0,0,.58);padding:18px}
#postExportCard h3{margin:0 0 7px;font:800 18px/1.2 Arial,sans-serif;color:#f4f5f7}
#postExportCard p{margin:0 0 16px;font:12px/1.5 var(--mono);color:#aeb6c5}
#postExportActions{display:grid;grid-template-columns:1fr 1fr;gap:10px}
#postExportActions button{min-height:62px;border-radius:9px;border:1px solid #3b4251;background:#232733;color:#f1efea;padding:10px 12px;font:800 12px/1.35 Arial,sans-serif;cursor:pointer;text-align:left}
#postExportActions button small{display:block;margin-top:4px;color:#9da7b7;font:10px/1.35 var(--mono);font-weight:500}
#postExportNext{border-color:rgba(79,209,197,.55)!important;background:rgba(79,209,197,.09)!important}
#postExportStay{border-color:rgba(242,169,0,.5)!important;background:rgba(242,169,0,.07)!important}
#postExportActions button:hover{filter:brightness(1.15)}
@media(max-width:620px){#postExportActions{grid-template-columns:1fr}}
'''
    text=replace_once(text,"</style>",css+"\n</style>","post export CSS")

    modal=r'''
<div id="postExportChoice" hidden>
  <div id="postExportCard" role="dialog" aria-modal="true" aria-labelledby="postExportTitle">
    <h3 id="postExportTitle">✅ CDG exportado correctamente</h3>
    <p>¿Qué quieres hacer ahora?</p>
    <div id="postExportActions">
      <button id="postExportNext" type="button">Seguir flujo
        <small>Abrir el siguiente trabajo pendiente para continuar editando.</small>
      </button>
      <button id="postExportStay" type="button">Quedarme en este trabajo
        <small>Seguir aquí para revisar Karaoke, CDG, diagnóstico y hacer pruebas.</small>
      </button>
    </div>
  </div>
</div>

'''
    text=replace_once(text,'<div id="workspace">',modal+'<div id="workspace">',"post export modal")

    helper=r'''
let POST_EXPORT_DONE=null;
function hidePostExportChoice(){
  const m=$("#postExportChoice"); if(m)m.hidden=true;
}
function showPostExportChoice(done){
  POST_EXPORT_DONE=done||{};
  const m=$("#postExportChoice"); if(!m)return;
  m.hidden=false;
  const next=$("#postExportNext");
  const nextId=POST_EXPORT_DONE?.next_job_id||"";
  if(next){
    next.innerHTML=nextId
      ? 'Seguir flujo<small>Abrir el siguiente trabajo pendiente para continuar editando.</small>'
      : 'Seguir flujo<small>Volver al panel y continuar con el siguiente trabajo disponible.</small>';
    setTimeout(()=>next.focus(),20);
  }
}
function continuePostExportFlow(){
  const done=POST_EXPORT_DONE||{};
  hidePostExportChoice();
  if(window.parent!==window){
    window.parent.postMessage({
      type:'panel:export-success',
      job_id:PANEL_JOB_ID,
      next_job_id:done.next_job_id||''
    },location.origin);
  }else{
    toast("Exportación terminada. Vuelve al panel para continuar.",2600);
  }
}
function stayOnCurrentPostExport(){
  hidePostExportChoice();
  toast("Te quedas en este trabajo para hacer pruebas.",2200);
}
'''
    text=replace_once(text,'/* --- crear CDG (servidor) --- */',helper+'\n/* --- crear CDG (servidor) --- */',"post export helpers")

    guarded="if(!window.DJGABO_CDG_PREVIEW_RENDERING && window.parent!==window) window.parent.postMessage({type:'panel:export-success',job_id:PANEL_JOB_ID,next_job_id:done.next_job_id||''},location.origin);"
    plain="if(window.parent!==window) window.parent.postMessage({type:'panel:export-success',job_id:PANEL_JOB_ID,next_job_id:done.next_job_id||''},location.origin);"
    if guarded in text:
        nav=guarded
    elif plain in text:
        nav=plain
    else:
        raise RuntimeError("No encontré el postMessage de export-success")

    old=f'''    setRenderProgress(100,'✅ Exportado con éxito, Valeria','CDG ✓ / Exportado ✓');
    const sub=$("#renderBusySub");if(sub)sub.textContent='Continúa con la siguiente sincronización. El sistema abrirá el siguiente trabajo automáticamente.';
    setStatus("CDG ✓ · Exportado ✓","good");toast("✅ Exportado con éxito, Valeria",3200);S.dirty=false;
    {nav}
    setTimeout(hideRenderBusy,1500);return;'''
    new='''    setRenderProgress(100,'✅ Exportado con éxito, Valeria','CDG ✓ / Exportado ✓');
    const sub=$("#renderBusySub");if(sub)sub.textContent='Exportación terminada.';
    setStatus("CDG ✓ · Exportado ✓","good");toast("✅ Exportado con éxito, Valeria",3200);S.dirty=false;
    // Si el render vino desde la pestaña CDG (actualizar preview final), no
    // interrumpimos con el modal. Si fue Exportar CDG normal, el usuario decide.
    if(window.DJGABO_CDG_PREVIEW_RENDERING){
      setTimeout(hideRenderBusy,700);return;
    }
    setTimeout(()=>{hideRenderBusy();showPostExportChoice(done);},700);return;'''
    text=replace_once(text,old,new,"export success flow")

    # Bind de las dos opciones después de existir el DOM.
    bind=r'''
$("#postExportNext").onclick=continuePostExportFlow;
$("#postExportStay").onclick=stayOnCurrentPostExport;
'''
    text=replace_once(text,'$("#btnFix").onclick = () => { $("#checks").hidden = true; };',bind+'$("#btnFix").onclick = () => { $("#checks").hidden = true; };',"post export button bindings")

    text=text.replace("</body>",f"<!-- {MARKER} -->\n</body>",1)
    p.write_text(text,encoding="utf-8")
    print("PATCH=OK")
    print("MARKER="+MARKER)

if __name__=="__main__":
    main()
