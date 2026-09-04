#!/usr/bin/env python3
from pathlib import Path
import re
import sys

EDITOR_MARKER="DJGABO_COUNTDOWN_DOTS_ONLY_PREVIEW_V1"
NORMALIZE_MARKER="DJGABO_COUNTDOWN_DOTS_ONLY_NORMALIZER_V1"
COMPOSER_MARKER="DJGABO_COUNTDOWN_DOTS_ONLY_COMPOSER_V1"

def replace_between(text,start,end,new,label):
    a=text.find(start)
    if a<0:
        raise RuntimeError(f"{label}: no encontre inicio {start!r}")
    b=text.find(end,a)
    if b<0:
        raise RuntimeError(f"{label}: no encontre fin {end!r}")
    return text[:a]+new+text[b:]

def patch_editor(path:Path):
    text=path.read_text(encoding="utf-8")
    if EDITOR_MARKER in text:
        print("EDITOR_PATCH_ALREADY_PRESENT=YES")
        return

    # Configuración visible/diagnóstico: ya no existe un label instrumental.
    text,n=re.subn(
        r'instrumental:\s*\{label:"INSTRUMENTAL",\s*dot:"(?:\\u25CF|●)",\s*dots:4,',
        'instrumental: {dot:"●", dots:4,',
        text,count=1
    )
    if n!=1:
        raise RuntimeError(f"editor instrumental config: esperaba 1 reemplazo y encontre {n}")

    # Diagnóstico: la misma detección de pausa, pero presupuesto completo para
    # cuatro círculos. No se reserva tiempo para texto/label.
    diag_new=r'''function _diagInstrumentalDecisions(){
  /* DJGABO_COUNTDOWN_DOTS_ONLY_PREVIEW_V1
     Se conserva la detección de pausas. La representación visual es sólo
     ● ● ● ●; no existe rótulo INSTRUMENTAL ni tiempo reservado para él. */
  const c=PV.cfg.instrumental;
  const sung=S.words.filter(w=>!w.spoken && w.start_time!==null).sort((a,b)=>a.start_time-b.start_time);
  const spoken=pvMergedSpokenIntervals();
  const out=[];
  for(let i=0;i<sung.length;i++){
    const next=sung[i],prev=i?sung[i-1]:null;
    const base=prev?(prev.end_time??prev.start_time):Math.max(0,pvOpeningDecision().end+.25);
    const gap=Number(next.start_time)-Number(base);
    const overlaps=spoken.map(([a,b])=>[Math.max(a,base),Math.min(b,next.start_time)]).filter(([a,b])=>b>a);
    const hasSpoken=overlaps.length>0;
    const voiceOverlaps=(S.doc?.ai?.voice_gaps||[])
      .map(g=>[Math.max(Number(g.start),base),Math.min(Number(g.end),next.start_time)])
      .filter(([a,b])=>Number.isFinite(a)&&Number.isFinite(b)&&b>a);
    const hasUntranscribedVoice=voiceOverlaps.length>0;
    const longSpoken=hasSpoken && gap>=(c.spokenMin??6);
    const regularGap=gap>=c.minGap && !hasSpoken && !hasUntranscribedVoice;
    if(!(longSpoken||regularGap) && gap<1.5) continue;
    const shouldShow=!!(longSpoken||regularGap);
    const lead=longSpoken?(c.spokenLead??4):c.lead;
    const hideAt=Number(next.start_time)-Number(lead);
    const avail=hideAt-(Number(base)+.4);
    const useSpan=shouldShow?Math.min(Number(c.span||6),avail):0;
    const minSpan=longSpoken?.6:1.0;
    const rendererWillInsert=shouldShow&&useSpan>=minSpan;
    const dotsEnd=rendererWillInsert?hideAt:null;
    const dotsStart=rendererWillInsert?dotsEnd-useSpan:null;
    out.push({
      prev:{line:_diagLineNoForWord(prev),text:prev?.text||null,start:prev?.start_time??null,end:prev?.end_time??null},
      next:{id:next?.id||null,line:_diagLineNoForWord(next),text:next?.text||null,start:next?.start_time??null,end:next?.end_time??null},
      base_seconds:+Number(base).toFixed(3),
      gap_seconds:+gap.toFixed(3),
      has_spoken:hasSpoken,
      spoken_overlap_seconds:+overlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      untranscribed_voice_overlap_seconds:+voiceOverlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      has_untranscribed_voice:hasUntranscribedVoice,
      rule:longSpoken?"HABLADO>=6s":regularGap?"PAUSA_REGULAR>=6s":hasUntranscribedVoice?"VOZ_SIN_TEXTO_SUPRIME_CUENTA":"SIN_CUENTA",
      should_show_countdown:shouldShow,
      lead_seconds:+Number(lead).toFixed(3),
      preview_show_from_seconds:rendererWillInsert?+Number(dotsStart).toFixed(3):null,
      hide_at_seconds:rendererWillInsert?+Number(dotsEnd).toFixed(3):null,
      renderer_inserted:rendererWillInsert,
      renderer_visual:"DOTS_ONLY",
      renderer_first_synthetic_sync_seconds:dotsStart==null?null:+Number(dotsStart).toFixed(3),
      renderer_dots_start_seconds:dotsStart==null?null:+Number(dotsStart).toFixed(3),
      renderer_dots_end_seconds:dotsEnd==null?null:+Number(dotsEnd).toFixed(3),
      renderer_span_seconds:rendererWillInsert?+Number(useSpan).toFixed(3):0
    });
  }
  return out;
}
'''
    text=replace_between(
        text,
        "function _diagInstrumentalDecisions(){",
        "/* DJGABO_KARAOKE_TOTAL_TIMELINE_V1",
        diag_new,
        "editor diagnostic countdown"
    )

    # Camino heredado de composición visual: también queda sólo con círculos.
    countdowns_new=r'''function pvCountdowns(visual){
  const c=PV.cfg.instrumental;
  const out=[];
  let prevEnd=null;
  const lpp=PV.cfg.linesPerPage;
  const padToPage=()=>{while(out.length%lpp)out.push([]);};
  for(const line of visual){
    if(!line.length){out.push([]);continue;}
    const start=line[0].start_time;
    const gap=prevEnd===null?start:start-prevEnd;
    const base=prevEnd||0;
    if(gap>=c.minGap&&c.dots>0){
      const avail=(start-c.lead)-(base+.4);
      const span=Math.min(c.span,avail);
      if(span>=1.0){
        const end=start-c.lead,step=span/c.dots,from=end-span;
        padToPage();
        const top=Math.max(0,Math.floor(lpp/2));
        const bottom=Math.max(0,lpp-1-top);
        for(let j=0;j<top;j++)out.push([]);
        const dots=[];
        for(let i=0;i<c.dots;i++){
          dots.push({text:c.dot,_inst:true,_dotline:true,
                     start_time:from+i*step,end_time:from+(i+1)*step});
        }
        out.push(dots);
        for(let j=0;j<bottom;j++)out.push([]);
      }
    }
    out.push(line);
    prevEnd=Math.max(...line.map(w=>w.end_time??w.start_time));
  }
  return out;
}

'''
    text=replace_between(
        text,
        "function pvCountdowns(visual){",
        "/* DJGABO_AUTHORITATIVE_PAGES_V1",
        countdowns_new,
        "editor legacy countdowns"
    )

    # Scheduler activo NOMAD: genera una página sintética con UNA sola fila de
    # círculos y una sílaba muda al END exacto del último círculo.
    scheduler_new=r'''function pvSchedulerVisual(){
  const lpp=Math.max(2,Math.min(8,Number(PV.cfg.linesPerPage||6)));
  const raw=pvWrap();
  const decisions=new Map(
    _diagInstrumentalDecisions()
      .filter(x=>x.renderer_inserted&&x.next&&x.next.id)
      .map(x=>[String(x.next.id),x])
  );
  const out=[];
  let serial=0;
  for(let pos=0;pos<raw.length;pos+=lpp){
    const page=raw.slice(pos,pos+lpp);
    while(page.length<lpp)page.push([]);
    const content=page.filter(line=>line&&line.length);
    if(content.length){
      const firstLine=content[0],firstWord=firstLine[0];
      const d=decisions.get(String(firstWord?.id||""));
      if(d){
        const dotsStart=Number(d.renderer_dots_start_seconds);
        const dotsEnd=Number(d.renderer_dots_end_seconds);
        if(Number.isFinite(dotsStart)&&Number.isFinite(dotsEnd)&&dotsEnd>dotsStart){
          const top=Math.max(0,Math.floor(lpp/2));
          const bottom=Math.max(0,lpp-1-top);
          for(let j=0;j<top;j++)out.push([]);
          const dotLine=[];
          const dots=Math.max(1,Number(PV.cfg.instrumental.dots||4));
          const step=(dotsEnd-dotsStart)/dots;
          for(let i=0;i<dots;i++){
            dotLine.push({id:"pv-dot"+serial+"_"+i,text:PV.cfg.instrumental.dot,_inst:true,_dotline:true,
                          start_time:dotsStart+i*step,end_time:dotsStart+(i+1)*step});
          }
          dotLine.push({id:"pv-dot"+serial+"_end",text:"_",_inst:true,_dotline:true,_silent:true,_dotend:true,
                        start_time:dotsEnd,end_time:Number(firstWord.start_time)});
          out.push(dotLine);
          for(let j=0;j<bottom;j++)out.push([]);
          serial++;
        }
      }
    }
    out.push(...page);
  }
  return out;
}

'''
    text=replace_between(
        text,
        "function pvSchedulerVisual(){",
        "function pvNomadLineDelayedSchedule",
        scheduler_new,
        "editor nomad countdown visual"
    )

    # Ya no hay pareja label+dotline. La única regla especial física es que la
    # fila de círculos desaparece al END real del último círculo.
    old_start="  // INSTRUMENTAL + círculos = una unidad"
    if old_start in text:
        a=text.find(old_start)
        b=text.find("  return {draw,erase};",a)
        if b<0: raise RuntimeError("editor scheduler special end no encontrado")
        special=r'''  // DJGABO_COUNTDOWN_DOTS_ONLY_SCHEDULER_V1
  // La fila de círculos termina exactamente con el END del cuarto círculo.
  for(let i=0;i<n;i++){
    const line=lines[i]||[];
    if(!line.some(w=>w._dotline))continue;
    const visibleDots=syl.filter(s=>s.li===i&&!s.silent);
    if(visibleDots.length){
      erase[i]=Math.max(...visibleDots.map(s=>s.e));
    }
  }
'''
        text=text[:a]+special+text[b:]

    # Preview dedicado de pausa: sólo cuatro círculos.
    draw_new=r'''function pvDrawCountdown(state){
  pvx.fillStyle="#000";pvx.fillRect(0,0,PV.VW,PV.VH);
  const cy=PV.VH/2,r=13,gap=42,sx=PV.VW/2-gap*1.5;
  const total=4;
  for(let j=0;j<total;j++){
    const x=sx+j*gap;
    const order=total-1-j;
    const stage=state.phase-order*2;
    pvx.save();
    pvx.beginPath();pvx.arc(x,cy,r,0,Math.PI*2);pvx.clip();
    pvx.fillStyle="#FFFFFF";pvx.fillRect(x-r-2,cy-r-2,2*r+4,2*r+4);
    if(stage>0){
      const frac=stage>=2?1:.5;
      pvx.fillStyle="#F2B705";
      pvx.fillRect(x-r-2,cy-r-2,(2*r+4)*frac,2*r+4);
    }
    pvx.restore();
    pvx.strokeStyle="#111111";pvx.lineWidth=2.5;
    pvx.beginPath();pvx.arc(x,cy,r,0,Math.PI*2);pvx.stroke();
  }
}

'''
    text=replace_between(
        text,
        "function pvDrawInstrumental(state){",
        "function pvDrawOutlinedText",
        draw_new,
        "editor draw dots only"
    )
    if "pvDrawInstrumental(instState)" not in text:
        raise RuntimeError("editor call pvDrawInstrumental no encontrado")
    text=text.replace("pvDrawInstrumental(instState)","pvDrawCountdown(instState)",1)
    text=text.replace(
        'info.textContent="KARAOKE · INSTRUMENTAL · entrada de voz en "+instState.remain.toFixed(1)+" s"',
        'info.textContent="KARAOKE · CUENTA REGRESIVA · entrada de voz en "+instState.remain.toFixed(1)+" s"',
        1
    )

    # El modo estático sólo existía para el rótulo. Sin label, todos los eventos
    # sintéticos son círculos con sweep normal.
    text=text.replace("noSweep:!!w._label","noSweep:false")

    path.write_text(text,encoding="utf-8")
    print("EDITOR_PATCH=OK")
    print("EDITOR_MARKER="+EDITOR_MARKER)

def patch_normalize(path:Path):
    text=path.read_text(encoding="utf-8")
    if NORMALIZE_MARKER in text:
        print("NORMALIZE_PATCH_ALREADY_PRESENT=YES")
        return

    # La etiqueta deja de ser una entrada de configuración usada por el motor.
    text,n=re.subn(
        r'^    label = style\.get\("instrumental_label", "INSTRUMENTAL"\)\n',
        '',
        text,count=1,flags=re.M
    )
    if n!=1:
        raise RuntimeError(f"normalize label config: esperaba 1 y encontre {n}")

    make_new=r'''    def make_instrumental(base: float, start: float, n: int):
        # DJGABO_COUNTDOWN_DOTS_ONLY_NORMALIZER_V1
        long_spoken,regular_gap,_=decision(base,start)
        if not (n_dots>0 and (long_spoken or regular_gap)):
            return None
        use_lead=spoken_lead if long_spoken else lead
        avail=(start-use_lead)-(base+.4)
        use_span=min(span,avail)
        min_span=.6 if long_spoken else 1.0
        if use_span<min_span:
            return None

        dots_end=start-use_lead
        step=use_span/n_dots
        dots_start=dots_end-use_span

        # Una sola fila visual: ● ● ● ●. Se conserva una página completa para
        # no mezclar los slots de letra de antes/después de la pausa.
        page=[]
        top=max(0,lpp//2)
        bottom=max(0,lpp-1-top)
        page.extend([[] for _ in range(top)])
        page.append([
            {"id":f"dot{n}_{i}","text":dot,"_inst":True,"_dotline":True,
             "start_time":dots_start+i*step,
             "end_time":dots_start+(i+1)*step}
            for i in range(n_dots)
        ])
        page.extend([[] for _ in range(bottom)])
        if len(page)!=lpp:
            raise NormalizeError(f"Página de cuenta regresiva inválida: {len(page)} slots, esperaba {lpp}.")
        return page

'''
    text=replace_between(
        text,
        "    def make_instrumental(base: float, start: float, n: int):",
        "    out:list[list[dict]]=[]",
        make_new,
        "normalize make instrumental"
    )

    # No existe singer/modo estático para un label.
    if 'modes.append(4 if w.get("_label") else 0)' not in text:
        raise RuntimeError("normalize mode label no encontrado")
    text=text.replace('modes.append(4 if w.get("_label") else 0)','modes.append(0)',1)

    # Sustituimos el bloque anterior label+dot por la única regla que queda:
    # cerrar el último círculo con un sync mudo y pintar la fila con singer 3.
    marker="# DJGABO_INSTRUMENTAL_DOT_END_V1"
    a=text.find(marker)
    if a<0:
        raise RuntimeError("normalize DOT_END marker no encontrado")
    # incluir indentación inicial de comentario
    a=text.rfind("        ",0,a+1)
    b=text.find("        text_lines.append(body)",a)
    if b<0:
        raise RuntimeError("normalize text_lines append no encontrado")
    dot_block=r'''        # DJGABO_COUNTDOWN_DOTS_ONLY_END_V1
        # El último círculo termina en su END acordado mediante una sílaba
        # muda. No existe ninguna línea/rótulo INSTRUMENTAL.
        if line[0].get("_dotline"):
            final_end=max(float(w.get("end_time") or w["start_time"]) for w in line)
            parts.append("_")
            sync.append(int(round(final_end*100)))
            modes.append(0)
            body=" ".join(parts)
            body="3|"+body
'''
    text=text[:a]+dot_block+text[b:]

    path.write_text(text,encoding="utf-8")
    print("NORMALIZE_PATCH=OK")
    print("NORMALIZE_MARKER="+NORMALIZE_MARKER)

def patch_composer(path:Path):
    text=path.read_text(encoding="utf-8")
    if COMPOSER_MARKER in text:
        print("COMPOSER_PATCH_ALREADY_PRESENT=YES")
        return
    start="        # DJGABO_INSTRUMENTAL_UNIT_V1"
    a=text.find(start)
    if a<0:
        raise RuntimeError("composer old instrumental unit marker no encontrado")
    b=text.find('        self.logger.info("draw times set")',a)
    if b<0:
        raise RuntimeError("composer draw-times end no encontrado")
    new=r'''        # DJGABO_COUNTDOWN_DOTS_ONLY_COMPOSER_V1
        # Sólo queda la fila ● ● ● ●. Su borrado empieza exactamente en el
        # END del último círculo visible. No hay rótulo ni segunda fila asociada.
        for lyric,times in zip(self.lyrics,self.lyric_times):
            for i,line in enumerate(lyric.lines):
                visible=[s for s in line.syllables if str(s.text).strip()]
                is_dots=bool(visible) and all(str(s.text).strip() in {"§","●"} for s in visible)
                if not is_dots:
                    continue
                times.line_erase[i]=max(s.end_offset for s in visible)
                self.logger.info(
                    "countdown dots-only: line %d erase=%d",
                    i,times.line_erase[i]
                )

'''
    text=text[:a]+new+text[b:]
    path.write_text(text,encoding="utf-8")
    print("COMPOSER_PATCH=OK")
    print("COMPOSER_MARKER="+COMPOSER_MARKER)

def main():
    if len(sys.argv)!=4:
        raise SystemExit("uso: patch_production.py index.html normalize.py composer.py")
    editor,normalize,composer=map(lambda x:Path(x).resolve(),sys.argv[1:])
    patch_editor(editor)
    patch_normalize(normalize)
    patch_composer(composer)

if __name__=="__main__":
    main()
