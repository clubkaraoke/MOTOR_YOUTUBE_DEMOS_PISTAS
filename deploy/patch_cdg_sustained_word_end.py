#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

F=Path('/opt/djgabo-cdg-ia-test/editor_v1/index.html')
if not F.is_file():
    raise SystemExit('MISSING:'+str(F))

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
shutil.copy2(F,F.with_name(F.name+'.bak_sustained_end_'+stamp))
s=F.read_text(encoding='utf-8')

old="""  S.capturePrevIndex = null;
}"""
new="""  S.capturePrevIndex = null;
  repairSustainedLineEndsFromVoiceGaps();
}"""
if new not in s:
    if old not in s: raise SystemExit('PATCH_FAIL:reindex_hook')
    s=s.replace(old,new,1)

anchor="""/* START y END son independientes. */
function refreshEnds(from){ const a=Math.max(0,(from|0)-1); for(let i=a;i<S.words.length;i++){ const w=S.words[i]; if(w.start_time===null) continue; if(w.end_time!==null && w.end_time<=w.start_time) w.end_time=w.start_time+.05; } }
"""
helper="""/* START y END son independientes. */
function refreshEnds(from){ const a=Math.max(0,(from|0)-1); for(let i=a;i<S.words.length;i++){ const w=S.words[i]; if(w.start_time===null) continue; if(w.end_time!==null && w.end_time<=w.start_time) w.end_time=w.start_time+.05; } }

/* R12 · final vocal sostenido.
   Si Scribe/QA detecta VOZ SIN TEXTO pegada al END de la ÚLTIMA palabra de una línea,
   primero intentamos absorberla como cola vocal de esa misma palabra.
   No cruza la siguiente palabra y no toca huecos separados/ambiguos. */
function repairSustainedLineEndsFromVoiceGaps(){
  const gaps=S.doc?.ai?.voice_gaps;
  if(!Array.isArray(gaps)||!gaps.length||!Array.isArray(S.words)||!S.words.length)return 0;
  let fixed=0;
  const used=new Set();

  for(let i=0;i<S.words.length;i++){
    const w=S.words[i], seg=w?._seg;
    if(!w||w.spoken||!seg||seg.kind==="break"||!Array.isArray(seg.words)||seg.words[seg.words.length-1]!==w)continue;
    const st=Number(w.start_time), en=Number(w.end_time);
    if(!Number.isFinite(st)||!Number.isFinite(en)||en<=st)continue;

    const nxt=S.words.slice(i+1).find(x=>x&&x.start_time!==null&&Number.isFinite(Number(x.start_time)));
    const nextStart=nxt?Number(nxt.start_time):Infinity;

    let best=-1,bestEnd=en;
    for(let gi=0;gi<gaps.length;gi++){
      if(used.has(gi))continue;
      const g=gaps[gi], a=Number(g?.start), b=Number(g?.end);
      if(!Number.isFinite(a)||!Number.isFinite(b)||b<=a)continue;

      // Debe comenzar prácticamente pegado al END actual: tolera pequeño error de detector.
      if(a<en-.28 || a>en+.38)continue;

      // Evita absorber una frase nueva o una intervención separada.
      const extension=b-en;
      if(extension<.08 || extension>3.8)continue;
      if(b>nextStart-.08)continue;

      // Si hay una pausa audible clara antes del gap, no asumimos que sea la misma vocal.
      if(a-en>.38)continue;

      if(b>bestEnd){best=gi;bestEnd=b;}
    }

    if(best>=0){
      w.end_time=Math.max(en,bestEnd);
      w.ai_end_extended=true;
      w.ai_end_extension_source="voice_gap_tail";
      w.ai_end_extension_seconds=Math.round((w.end_time-en)*1000)/1000;
      used.add(best);
      fixed++;
    }
  }

  if(used.size){
    S.doc.ai.voice_gaps=gaps.filter((_,i)=>!used.has(i));
    S.dirty=true;
  }
  return fixed;
}
"""
if helper not in s:
    if anchor not in s: raise SystemExit('PATCH_FAIL:refresh_anchor')
    s=s.replace(anchor,helper,1)

F.write_text(s,encoding='utf-8')
print('PATCH_SUSTAINED_WORD_END=OK')
