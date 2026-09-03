#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

F=Path('/opt/djgabo-cdg-ia-test/editor_v1/index.html')
if not F.is_file():
    raise SystemExit('MISSING:'+str(F))

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
shutil.copy2(F,F.with_name(F.name+'.bak_diag_json_'+stamp))
s=F.read_text(encoding='utf-8')

def rep(old,new,label):
    global s
    if new in s:
        print(label+'=ALREADY_PATCHED')
        return
    if old not in s:
        raise SystemExit('PATCH_FAIL:'+label)
    s=s.replace(old,new,1)

rep(
'''<button class="hbtn" id="btnAiBlock" title="Alinear sólo la selección con ElevenLabs Forced Alignment">✨ IA BLOQUE</button><span style="font-size:10px;color:var(--dimmer);margin-left:6px">Manual = SPACE · IA BLOQUE = sólo la selección</span>''',
'''<button class="hbtn" id="btnAiBlock" title="Alinear sólo la selección con ElevenLabs Forced Alignment">✨ IA BLOQUE</button><button class="hbtn" id="btnDiagJson" title="Abrir diagnóstico JSON de timings, voz sin texto e instrumentales">📋 DIAGNÓSTICO</button><span style="font-size:10px;color:var(--dimmer);margin-left:6px">Manual = SPACE · IA BLOQUE = sólo la selección</span>''',
'button'
)

rep(
'''#btnAiBlock.busy{opacity:.65;pointer-events:none}
</style>''',
'''#btnAiBlock.busy{opacity:.65;pointer-events:none}
#btnDiagJson{border-color:rgba(79,209,197,.45);color:#8de7df;background:rgba(79,209,197,.07)}
#btnDiagJson:hover{border-color:#4fd1c5;color:#fff;background:rgba(79,209,197,.14)}
#diagJsonModal{position:fixed;inset:0;z-index:99998;background:rgba(0,0,0,.78);display:flex;align-items:center;justify-content:center;padding:22px}
#diagJsonModal[hidden]{display:none!important}
#diagJsonCard{width:min(920px,96vw);height:min(82vh,760px);background:#171922;border:1px solid #3b4251;border-radius:10px;display:flex;flex-direction:column;box-shadow:0 22px 70px rgba(0,0,0,.55)}
#diagJsonHead{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid #2e3340}
#diagJsonHead b{font:700 13px var(--mono);color:#d7f9f6}
#diagJsonBody{flex:1;min-height:0;padding:12px}
#diagJsonText{width:100%;height:100%;resize:none;background:#0f1117;color:#d9e1ea;border:1px solid #303746;border-radius:7px;padding:12px;font:11px/1.45 var(--mono);white-space:pre;overflow:auto}
#diagJsonFoot{display:flex;gap:8px;justify-content:flex-end;padding:10px 14px;border-top:1px solid #2e3340}
#diagJsonFoot button{border:1px solid #3b4251;border-radius:6px;padding:8px 12px;background:#232733;color:#f1efea;font-weight:700;cursor:pointer}
#diagJsonFoot button:hover{border-color:#4fd1c5}
</style>''',
'styles'
)

rep(
'''<div id="workspace">''',
'''<div id="diagJsonModal" hidden>
  <div id="diagJsonCard">
    <div id="diagJsonHead"><b>DIAGNÓSTICO JSON · CLON IA TEST</b><button id="btnDiagClose" type="button">✕</button></div>
    <div id="diagJsonBody"><textarea id="diagJsonText" spellcheck="false"></textarea></div>
    <div id="diagJsonFoot"><button id="btnDiagCopy" type="button">Copiar JSON</button><button id="btnDiagDownload" type="button">Descargar JSON</button><button id="btnDiagClose2" type="button">Cerrar</button></div>
  </div>
</div>

<div id="workspace">''',
'modal'
)

anchor='''function pvDrawInstrumental(state){'''
if 'function buildDiagnosticJsonPayload()' not in s:
    helper=r'''
function _diagLineNoForWord(w){
  let n=0;
  for(const seg of S.doc?.segments||[]){
    if(seg.kind==="break") continue;
    n++;
    if((seg.words||[]).includes(w)) return n;
  }
  return null;
}
function _diagInstrumentalDecisions(){
  const c=PV.cfg.instrumental;
  const sung=S.words.filter(w=>!w.spoken && w.start_time!==null).sort((a,b)=>a.start_time-b.start_time);
  const spoken=pvMergedSpokenIntervals();
  const out=[];
  for(let i=0;i<sung.length;i++){
    const next=sung[i],prev=i?sung[i-1]:null;
    const base=prev?(prev.end_time??prev.start_time):Math.max(0,S.cfg.introDuration+.25);
    const gap=Number(next.start_time)-Number(base);
    const overlaps=spoken.map(([a,b])=>[Math.max(a,base),Math.min(b,next.start_time)]).filter(([a,b])=>b>a);
    const hasSpoken=overlaps.length>0;
    const longSpoken=hasSpoken && gap>=(c.spokenMin??6);
    const regularGap=gap>=c.minGap && !hasSpoken;
    if(!(longSpoken||regularGap) && gap<1.5) continue;
    out.push({
      prev:{line:_diagLineNoForWord(prev),text:prev?.text||null,start:prev?.start_time??null,end:prev?.end_time??null},
      next:{line:_diagLineNoForWord(next),text:next?.text||null,start:next?.start_time??null,end:next?.end_time??null},
      gap_seconds:+gap.toFixed(3),
      has_spoken:hasSpoken,
      spoken_overlap_seconds:+overlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      rule:longSpoken?"HABLADO>=6s":regularGap?"PAUSA_REGULAR>=6s":"NO_INSTRUMENTAL",
      should_show_instrumental:!!(longSpoken||regularGap),
      lead_seconds:longSpoken?(c.spokenLead??4):c.lead
    });
  }
  return out;
}
function buildDiagnosticJsonPayload(){
  const words=(S.words||[]).map((w,i)=>({
    index:i,
    line:_diagLineNoForWord(w),
    id:w.id,
    text:w.text,
    start:w.start_time,
    end:w.end_time,
    duration:(w.start_time!=null&&w.end_time!=null)?+(Number(w.end_time)-Number(w.start_time)).toFixed(3):null,
    spoken:!!w.spoken,
    vocal_role:w.vocal_role||null,
    ai_status:w.ai_status||null,
    ai_match_type:w.ai_match_type||null,
    ai_loss:w.ai_loss??null,
    ai_end_extended:!!w.ai_end_extended,
    ai_end_extension_seconds:w.ai_end_extension_seconds??null
  }));
  return {
    diagnostic_version:"CDG_IA_TEST_DIAG_V1",
    generated_at:new Date().toISOString(),
    job_id:(typeof PANEL_JOB_ID!=="undefined"?PANEL_JOB_ID:""),
    song:S.doc?.song||null,
    current_time:S.audio?.currentTime??null,
    view:S.view||null,
    counts:{
      words:words.length,
      timed:words.filter(w=>w.start!=null).length,
      spoken:words.filter(w=>w.spoken).length,
      voice_gaps:(S.doc?.ai?.voice_gaps||[]).length
    },
    voice_gaps:S.doc?.ai?.voice_gaps||[],
    merged_spoken_intervals:pvMergedSpokenIntervals(),
    instrumental_config:PV.cfg.instrumental,
    instrumental_decisions:_diagInstrumentalDecisions(),
    ai_block_alignments:S.doc?.ai?.block_alignments||[],
    words
  };
}
function openDiagnosticJson(){
  const modal=$("#diagJsonModal"),ta=$("#diagJsonText");
  if(!modal||!ta)return;
  ta.value=JSON.stringify(buildDiagnosticJsonPayload(),null,2);
  modal.hidden=false;
  ta.scrollTop=0;
}
async function copyDiagnosticJson(){
  const ta=$("#diagJsonText"); if(!ta)return;
  try{await navigator.clipboard.writeText(ta.value);toast("✓ JSON de diagnóstico copiado",2200);}
  catch(_){ta.focus();ta.select();document.execCommand("copy");toast("✓ JSON copiado",2200);}
}
function downloadDiagnosticJson(){
  const ta=$("#diagJsonText"); if(!ta)return;
  const blob=new Blob([ta.value],{type:"application/json;charset=utf-8"});
  const a=document.createElement("a");
  const job=(typeof PANEL_JOB_ID!=="undefined"&&PANEL_JOB_ID)?PANEL_JOB_ID:"cdg-ia";
  a.href=URL.createObjectURL(blob);a.download=job+"-diagnostico.json";
  document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
'''
    idx=s.find(anchor)
    if idx<0: raise SystemExit('PATCH_FAIL:helper_anchor')
    s=s[:idx]+helper+s[idx:]

rep(
'''$("#btnAiBlock").onclick=()=>aiAlignSelectedBlock();''',
'''$("#btnAiBlock").onclick=()=>aiAlignSelectedBlock();
if($("#btnDiagJson")) $("#btnDiagJson").onclick=()=>openDiagnosticJson();
if($("#btnDiagCopy")) $("#btnDiagCopy").onclick=()=>copyDiagnosticJson();
if($("#btnDiagDownload")) $("#btnDiagDownload").onclick=()=>downloadDiagnosticJson();
if($("#btnDiagClose")) $("#btnDiagClose").onclick=()=>$("#diagJsonModal").hidden=true;
if($("#btnDiagClose2")) $("#btnDiagClose2").onclick=()=>$("#diagJsonModal").hidden=true;
if($("#diagJsonModal")) $("#diagJsonModal").addEventListener("click",e=>{if(e.target.id==="diagJsonModal")e.currentTarget.hidden=true;});''',
'handlers'
)

F.write_text(s,encoding='utf-8')
print('PATCH_DIAGNOSTIC_JSON_BUTTON=OK')
