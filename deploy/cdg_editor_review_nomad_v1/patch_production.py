#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER="DJGABO_EDITOR_REVIEW_NOMAD_V1"
COMPOSER_MARKER="DJGABO_INSTRUMENTAL_UNIT_V1"
NORMALIZE_MARKER="DJGABO_INSTRUMENTAL_DOT_END_V1"

def replace_once(text, old, new, label):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontre {n}")
    return text.replace(old,new,1)

def replace_between(text,start_marker,end_marker,new_block,label):
    a=text.find(start_marker)
    if a<0: raise RuntimeError(f"{label}: no encontre inicio")
    b=text.find(end_marker,a)
    if b<0: raise RuntimeError(f"{label}: no encontre fin")
    return text[:a]+new_block+text[b:]

def patch_editor(path:Path):
    text=path.read_text(encoding="utf-8")
    if MARKER in text:
        print("EDITOR_PATCH_ALREADY_PRESENT=YES")
        return

    css=r'''
/* DJGABO_EDITOR_REVIEW_NOMAD_V1
   Limpieza visual + revision rapida. Los botones ocultos siguen en DOM para
   conservar compatibilidad con el flujo interno/autoguardado/reintentos. */
#btnLyrics,#btnSave,#btnCdg{display:none!important}
#editHint{display:none!important}
#aiToolsBar{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:7px 8px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.015)
}
#aiToolsBar .aiPrimary,#aiToolsBar .aiSecondary{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
#aiToolsBar #btnAiBlock,#aiToolsBar #btnAiFull{
  min-height:34px;padding:7px 12px;font-weight:800;border-color:rgba(79,209,197,.45);
  background:rgba(79,209,197,.06)
}
#aiToolsBar #btnAiFull{border-color:rgba(242,169,0,.5);background:rgba(242,169,0,.07)}
#vocalRoles{flex-wrap:wrap!important}
#vocalRoles>.hbtn{flex:0 0 auto}
.rates .rate{min-width:44px}
.rates.reviewLocked .rate:not([data-rate="1"]){opacity:.38;cursor:not-allowed}
@media(max-width:820px){
  header{grid-template-columns:repeat(4,minmax(0,1fr))!important}
  #songName{grid-column:1/-1!important}
  #counter{grid-column:1/3!important}
  #status{grid-column:3/5!important}
  #btnPreview{grid-column:1/3!important}
  #btnSettings{grid-column:3/5!important}
  #aiToolsBar{align-items:stretch;flex-direction:column;padding:6px 8px}
  #aiToolsBar .aiPrimary,#aiToolsBar .aiSecondary{width:100%;display:grid;grid-template-columns:1fr 1fr}
  #aiToolsBar .hbtn,#aiToolsBar button{width:100%;min-width:0}
  #vocalRoles{overflow-x:auto;flex-wrap:nowrap!important;padding:6px 8px!important}
}
'''
    text=replace_once(text,"</style>",css+"\n</style>","insert css")

    old_header='''  <button class="hbtn" id="btnPreview">Pantalla</button>
  <button class="hbtn" id="btnLyrics">Letra</button>
  <button class="hbtn" id="btnSettings">Ajustes</button>
  <button class="hbtn" id="btnSave">Guardar proyecto</button>
  <button class="hbtn go" id="btnCdg">Crear CDG</button>'''
    new_header='''  <button class="hbtn" id="btnPreview">Pantalla</button>
  <button class="hbtn" id="btnLyrics" aria-hidden="true" tabindex="-1">Letra</button>
  <button class="hbtn" id="btnSettings">Ajustes</button>
  <button class="hbtn" id="btnSave" aria-hidden="true" tabindex="-1">Guardar proyecto</button>
  <button class="hbtn go" id="btnCdg" aria-hidden="true" tabindex="-1">Crear CDG</button>'''
    text=replace_once(text,old_header,new_header,"header hidden compatibility buttons")

    drawer_start='''    <div id="lyricsDrawer">
      <div id="editHint">'''
    a=text.find(drawer_start)
    if a<0: raise RuntimeError("lyricsDrawer start no encontrado")
    aiqa='''      <div id="aiQaBar" class="aiQaBar" hidden></div>'''
    b=text.find(aiqa,a)
    if b<0: raise RuntimeError("aiQaBar no encontrado")
    new_drawer='''    <div id="lyricsDrawer">
      <div id="editHint"></div>
      <div id="aiToolsBar">
        <div class="aiPrimary">
          <button class="hbtn" id="btnAiBlock" title="Alinear solo la seleccion con ElevenLabs Forced Alignment">✨ IA BLOQUE</button>
          <button class="hbtn" id="btnAiFull" title="Sincronizar toda la letra existente con ElevenLabs Scribe v2">✨ IA TODA LA LETRA</button>
        </div>
        <div class="aiSecondary">
          <button id="btnResync" type="button">↻ RESINCRONIZAR SELECCIÓN</button>
          <button class="hbtn" id="btnDiagJson" title="Abrir diagnostico JSON de timings, voz sin texto e instrumentales">📋 DIAGNÓSTICO</button>
        </div>
      </div>
      <div id="vocalRoles" style="display:flex;gap:6px;align-items:center;padding:6px 8px;border-bottom:1px solid var(--line)">
        <button class="hbtn roleNone" id="btnRoleNone">SIN ROL <span class="roleKey">0</span></button>
        <button class="hbtn roleMale" id="btnMale">HOMBRE <span class="roleKey">1</span></button>
        <button class="hbtn roleFemale" id="btnFemale">MUJER <span class="roleKey">2</span></button>
        <button class="hbtn roleDuet" id="btnDuet">DUO <span class="roleKey">3</span></button>
        <button class="hbtn spoken" id="btnSpoken2">HABLADO <span class="roleKey">4</span></button>
      </div>
'''
    text=text[:a]+new_drawer+text[b:]

    old_rates='''  <div class="rates" title="Velocidad de audición · solo para corregir letra">
    <button class="rate on" data-rate="1">1.0×</button>
    <button class="rate" data-rate="1.25">1.25×</button>
    <button class="rate" data-rate="1.5">1.5×</button>
    <button class="rate" data-rate="1.75">1.75×</button>
    <button class="rate" data-rate="2">2.0×</button>
    <button class="rate" data-rate="3">3.0×</button>
  </div>'''
    new_rates='''  <div class="rates reviewLocked" title="Velocidad de revisión · solo cambia la escucha, nunca los timings">
    <button class="rate on" data-rate="1">1×</button>
    <button class="rate" data-rate="2">2×</button>
    <button class="rate" data-rate="3">3×</button>
    <button class="rate" data-rate="4">4×</button>
    <button class="rate" data-rate="5">5×</button>
  </div>'''
    text=replace_once(text,old_rates,new_rates,"review rates")

    old_rate_fn='''function setRate(r, force=false){
  // R26: las velocidades variables son sólo para CORREGIR LETRA.
  // En sincronización no se pueden cambiar ni por botón ni por atajo.
  if(document.body.classList.contains("phase2") && !force) return;
  r=+r || 1;
  S.audio.playbackRate = r;
  document.querySelectorAll(".rate").forEach(b=>b.classList.toggle("on", +b.dataset.rate===r));
}'''
    new_rate_fn='''function reviewRateEnabled(){
  return !!(S.doc && isSongCompleteForReview());
}
function syncReviewRateUi(){
  const box=document.querySelector("#transport .rates");
  if(!box)return;
  const enabled=reviewRateEnabled();
  box.classList.toggle("reviewLocked",!enabled);
  box.querySelectorAll(".rate").forEach(b=>{
    const fast=Number(b.dataset.rate)!==1;
    b.disabled=fast&&!enabled;
    b.title=fast&&!enabled
      ?"Disponible cuando toda la canción esté sincronizada"
      :"Solo cambia la velocidad de escucha; START/END y CDG no cambian";
  });
  if(!enabled && Number(S.audio.playbackRate||1)!==1) S.audio.playbackRate=1;
}
function setRate(r, force=false){
  r=Number(r)||1;
  if(![1,2,3,4,5].includes(r)) r=1;
  if(!force && r!==1 && !reviewRateEnabled()){
    toast("La velocidad rápida se habilita cuando toda la canción está sincronizada.",2200);
    r=1;
  }
  S.audio.playbackRate=r;
  document.querySelectorAll(".rate").forEach(b=>b.classList.toggle("on",Number(b.dataset.rate)===r));
  syncReviewRateUi();
}'''
    text=replace_once(text,old_rate_fn,new_rate_fn,"setRate review only")

    old_keys='''    case "Digit1": setRate(1); return;
    case "Digit2": setRate(1.25); return;
    case "Digit3": setRate(1.5); return;
    case "Digit4": setRate(1.75); return;
    case "Digit5": setRate(2); return;
    case "Digit6": setRate(3); return;'''
    new_keys='''    case "Digit1": setRate(1); return;
    case "Digit2": setRate(2); return;
    case "Digit3": setRate(3); return;
    case "Digit4": setRate(4); return;
    case "Digit5": setRate(5); return;'''
    text=replace_once(text,old_keys,new_keys,"rate keyboard")

    text=replace_once(text,"  updatePlayHighlight();\n","  updatePlayHighlight();\n  syncReviewRateUi();\n","rate ui refresh")

    old_mobile='''  btnScreen.onclick = (event) => {
    if (setMobileView("screen")) {
      if (event) event.preventDefault();
      return;
    }
    if (typeof desktopScreenHandler === "function") {
      desktopScreenHandler.call(btnScreen, event);
    }
  };'''
    new_mobile='''  btnScreen.onclick = (event) => {
    if (mq.matches) {
      setMobileView(mobileView === "screen" ? "lyrics" : "screen");
      if (event) event.preventDefault();
      return;
    }
    if (typeof desktopScreenHandler === "function") {
      desktopScreenHandler.call(btnScreen, event);
    }
  };'''
    text=replace_once(text,old_mobile,new_mobile,"mobile Pantalla toggle")

    plan_start="/* DJGABO_SMART_OVERWRITE_RENDER_PLAN_V1"
    plan_end="/* --- 2. plan de dibujado, SMART OVERWRITE autoritativo --- */"
    new_plan=r'''/* DJGABO_NOMAD_LINE_DELAYED_PREVIEW_V1
   Diagnóstico del mismo scheduler que usa el CDG final.
   START/END musicales permanecen inmutables. */
function buildRenderPlanDecision(){
  const p=pvPlan();
  const lpp=Math.max(2,Math.min(8,Number(PV.cfg.linesPerPage||6)));
  const inst=_diagInstrumentalDecisions().filter(x=>x.renderer_inserted);
  const instIntervals=inst.map((x,i)=>({
    id:"instrumental:"+i,
    start:Number(x.preview_show_from_seconds),
    end:Number(x.hide_at_seconds),
    next_word_id:x.next?.id||null
  })).filter(x=>Number.isFinite(x.start)&&Number.isFinite(x.end)&&x.end>x.start);
  const events=[];
  if(p){
    for(let li=0;li<p.lines.length;li++){
      const line=p.lines[li]||[];
      if(!line.length||line.some(w=>w._inst))continue;
      const timed=line.filter(w=>w.start_time!==null&&w.start_time!==undefined);
      if(!timed.length)continue;
      const st=Math.min(...timed.map(w=>Number(w.start_time)));
      const en=Math.max(...timed.map(w=>Number(w.end_time??w.start_time)));
      const ids=line.map(w=>w.id).filter(Boolean);
      const d=p.draw[li],e=p.erase[li];
      events.push({
        line_id:"line:"+ids[0]+":"+ids[ids.length-1],
        visual_index:li,
        slot:(li%lpp)+1,
        word_ids:ids,
        page_index:Math.floor(li/lpp)+1,
        text:line.map(w=>pvText(w.text)).join(" "),
        sweep_start:+st.toFixed(3),
        sweep_end:+en.toFixed(3),
        display_at:d==null?null:+(d/300).toFixed(3),
        remove_at:e==null?null:+(e/300).toFixed(3)
      });
    }
  }
  return {
    version:"CDG_RENDER_PLAN_V2",
    source:"KARAOKE_PREVIEW_NOMAD_LINE_DELAYED",
    mode:"NOMAD_LINE_DELAYED",
    lines_per_screen:lpp,
    line_draw_erase_gap_frames:PV.GAP,
    timing_source:"nomadkaraoke.cdgmaker.LINE_DELAYED",
    policy:{
      musical_word_timings_are_immutable:true,
      preview_and_renderer_share_scheduler:true,
      intro_delay_seconds:0,
      hidden_offsets:false
    },
    clear_events:[],
    instrumental_intervals:instIntervals,
    lines:events
  };
}

'''
    text=replace_between(text,plan_start,plan_end,new_plan,"render plan")

    pv_start="function pvPlan(){"
    pv_end="let pvT;"
    new_pv=r'''function pvSchedulerVisual(){
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
      const firstLine=content[0], firstWord=firstLine[0];
      const d=decisions.get(String(firstWord?.id||""));
      if(d){
        const dotsStart=Number(d.renderer_dots_start_seconds);
        const dotsEnd=Number(d.renderer_dots_end_seconds);
        const firstSynthetic=Number(d.renderer_first_synthetic_sync_seconds);
        const labelAt=Number(d.renderer_label_at_seconds);
        if(Number.isFinite(dotsStart)&&Number.isFinite(dotsEnd)&&dotsEnd>dotsStart&&Number.isFinite(firstSynthetic)){
          const top=Math.max(0,Math.floor(lpp/2)-1);
          const bottom=Math.max(0,lpp-2-top);
          for(let j=0;j<top;j++)out.push([]);
          const labelLine=[];
          if(Number.isFinite(labelAt)&&labelAt>firstSynthetic+.15){
            labelLine.push({id:"pv-in"+serial+"s",text:"_",_inst:true,_silent:true,_label:true,start_time:firstSynthetic,end_time:labelAt});
            labelLine.push({id:"pv-in"+serial,text:PV.cfg.instrumental.label,_inst:true,_label:true,start_time:labelAt,end_time:dotsStart});
          }else{
            labelLine.push({id:"pv-in"+serial,text:PV.cfg.instrumental.label,_inst:true,_label:true,start_time:firstSynthetic,end_time:dotsStart});
          }
          out.push(labelLine);
          const dotLine=[];
          const dots=Math.max(1,Number(PV.cfg.instrumental.dots||4));
          const step=(dotsEnd-dotsStart)/dots;
          for(let i=0;i<dots;i++){
            dotLine.push({id:"pv-dot"+serial+"_"+i,text:PV.cfg.instrumental.dot,_inst:true,_dotline:true,start_time:dotsStart+i*step,end_time:dotsStart+(i+1)*step});
          }
          // cdgmaker necesita un punto mudo al END para que el último círculo
          // no se barra hasta la siguiente voz.
          dotLine.push({id:"pv-dot"+serial+"_end",text:"_",_inst:true,_dotline:true,_silent:true,_dotend:true,start_time:dotsEnd,end_time:Number(firstWord.start_time)});
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

function pvNomadLineDelayedSchedule(lines,syl,lpp){
  const n=lines.length,G=PV.GAP;
  const draw=new Array(n).fill(null),erase=new Array(n).fill(null);
  if(!syl.length)return {draw,erase};
  const firstByLine=new Map();
  for(const s of syl){if(!firstByLine.has(s.li))firstByLine.set(s.li,s.s)}
  let drawTime=syl[0].s-900;
  for(let i=0;i<lpp;i++){
    if(i<n){draw[i]=drawTime;drawTime+=G;}
  }
  for(let k=1;k<syl.length;k++){
    const last=syl[k-1],wipe=syl[k];
    if(wipe.li<=last.li)continue;
    const lastPage=Math.floor(last.li/lpp),thisPage=Math.floor(wipe.li/lpp);
    if(lastPage===thisPage){
      let eraseTime=Math.min(wipe.s+100,last.e+450);
      for(let i=last.li;i<wipe.li;i++){
        if(i<n){erase[i]=eraseTime;eraseTime+=G;}
      }
      continue;
    }
    const lastEnd=Math.max(last.e,last.s+100);
    const inter=wipe.s-lastEnd;
    const lastLineStart=firstByLine.get(last.li)??last.s;
    let eraseTime=Math.min(wipe.s+100,lastEnd+450,lastEnd+Math.floor(inter/3));
    let nextDraw=Math.max(lastLineStart+100,wipe.s-900,lastEnd+Math.floor(inter/3));
    if(inter>=1200){
      for(let i=last.li;i<wipe.li;i++){
        if(i<n){erase[i]=eraseTime;eraseTime+=G;}
      }
      nextDraw=Math.max(nextDraw,eraseTime);
      const startLine=lastPage*lpp;
      for(let i=startLine;i<startLine+lpp;i++){
        const j=i+lpp;
        if(j<n){draw[j]=nextDraw;nextDraw+=G;}
      }
      continue;
    }
    nextDraw=lastLineStart+150;
    const startLine=lastPage*lpp;
    for(let i=startLine;i<last.li;i++){
      const j=i+lpp;
      if(j<n){draw[j]=nextDraw;nextDraw+=G;}
    }
    nextDraw=Math.max(nextDraw,lastEnd+Math.floor(inter/3));
    for(let i=last.li;i<wipe.li;i++){
      if(i<n){erase[i]=nextDraw;nextDraw+=G;}
    }
    for(let i=last.li;i<wipe.li;i++){
      const j=i+lpp;
      if(j<n){draw[j]=nextDraw;nextDraw+=G;}
    }
  }
  const last=syl[syl.length-1];
  if(last&&last.li<n)erase[last.li]=last.e+600;

  // INSTRUMENTAL + círculos = una unidad: ambos se dibujan juntos y ambos
  // empiezan a borrarse en el END real del último círculo.
  for(let i=0;i+1<n;i++){
    const a=lines[i]||[],b=lines[i+1]||[];
    if(!a.some(w=>w._label)||!b.some(w=>w._dotline))continue;
    const finite=[draw[i],draw[i+1]].filter(Number.isFinite);
    if(finite.length){
      const jointDraw=Math.min(...finite);
      draw[i]=jointDraw;draw[i+1]=jointDraw;
    }
    const visibleDots=syl.filter(s=>s.li===i+1&&!s.silent);
    if(visibleDots.length){
      const jointEnd=Math.max(...visibleDots.map(s=>s.e));
      erase[i]=jointEnd;erase[i+1]=jointEnd;
    }
  }
  return {draw,erase};
}

function pvPlan(){
  const lpp=PV.cfg.linesPerPage,lth=PV.cfg.lineTileHeight;
  PV.cfg.row=Math.max(1,Math.floor((18-lpp*lth)/2));
  const lines=pvSchedulerVisual();
  if(!lines.length)return null;

  const syl=[],geom=[];
  const flatWords=lines.flat().filter(Boolean);
  const fallbackEnd=(w)=>{
    if(w.end_time!==null&&w.end_time!==undefined&&w.end_time>w.start_time)return w.end_time;
    const idx=flatWords.indexOf(w),nxt=idx>=0?flatWords[idx+1]:null;
    if(nxt&&nxt.start_time>w.start_time)return Math.min(nxt.start_time,w.start_time+.45);
    return w.start_time+.45;
  };
  for(let li=0;li<lines.length;li++){
    const line=lines[li]||[];
    const txt=line.map(w=>pvText(w.text)).join(" ");
    const lw=advWidth(txt);
    const x0=Math.floor((PV.W-lw)/2);
    const y=PV.cfg.row*PV.TILE+(li%lpp)*lth*PV.TILE;
    const boxes=[];
    let cx0=x0;
    for(const w of line){
      const t=pvText(w.text),ww=advWidth(t);
      boxes.push({text:t,x:cx0,w:ww,noSweep:!!w._label,role:w.vocal_role||null});
      cx0+=ww+advWidth(" ");
    }
    geom.push({y,boxes,text:txt,skip:line.some(w=>w._inst)});
    for(let k=0;k<line.length;k++){
      const w=line[k];
      if(w.start_time===null||w.start_time===undefined)continue;
      const st=cs(w.start_time)*3;
      const en=Math.max(st+3,cs(fallbackEnd(w))*3);
      syl.push({li,si:k,s:st,e:en,silent:!!w._silent||pvText(w.text).trim()===""});
    }
  }
  const schedule=pvNomadLineDelayedSchedule(lines,syl,lpp);
  return {lines,geom,syl,draw:schedule.draw,erase:schedule.erase,lpp,clearMode:"delayed",scheduler:"NOMAD_LINE_DELAYED"};
}

'''
    text=replace_between(text,pv_start,pv_end,new_pv,"pvPlan")

    old_draw='''    if(!p.lines[li].length) continue;
    const dTime = p.draw[li], eTime = p.erase[li];
    if(dTime === 0 || tf < dTime) continue;
    if(eTime !== 0 && tf >= eTime) continue;
    visible++;'''
    new_draw='''    if(!p.lines[li].length || p.lines[li].some(w=>w._inst)) continue;
    const dTime=p.draw[li],eTime=p.erase[li];
    if(dTime==null || tf<dTime)continue;
    if(eTime!=null && tf>=eTime)continue;
    visible++;'''
    text=replace_once(text,old_draw,new_draw,"preview scheduled visibility")

    old_total='''  const total = p.lines.filter(l => l.length).length;
  const idx = active >= 0 ? p.lines.slice(0, active+1).filter(l => l.length).length : 0;'''
    new_total='''  const total=p.lines.filter(l=>l.length&&!l.some(w=>w._inst)).length;
  const idx=active>=0?p.lines.slice(0,active+1).filter(l=>l.length&&!l.some(w=>w._inst)).length:0;'''
    text=replace_once(text,old_total,new_total,"preview line counters")

    path.write_text(text,encoding="utf-8")
    print("EDITOR_PATCH=OK")

def patch_normalize(path:Path):
    text=path.read_text(encoding="utf-8")
    if NORMALIZE_MARKER in text:
        print("NORMALIZE_PATCH_ALREADY_PRESENT=YES")
        return
    old='''        if line[0].get("_label"):
            body = "2|" + body
        elif line[0].get("_dotline"):
            body = "3|" + body'''
    new='''        # DJGABO_INSTRUMENTAL_DOT_END_V1
        # El último círculo termina en su END acordado. Añadimos una sílaba
        # muda exactamente allí; de otro modo cdgmaker alargaba el último
        # círculo hasta el START de la siguiente voz.
        if line[0].get("_dotline"):
            final_end=max(float(w.get("end_time") or w["start_time"]) for w in line)
            parts.append("_")
            sync.append(int(round(final_end*100)))
            modes.append(0)
            body=" ".join(parts)
        if line[0].get("_label"):
            body = "2|" + body
        elif line[0].get("_dotline"):
            body = "3|" + body'''
    text=replace_once(text,old,new,"instrumental dot end marker")
    path.write_text(text,encoding="utf-8")
    print("NORMALIZE_PATCH=OK")

def patch_composer(path:Path):
    text=path.read_text(encoding="utf-8")
    if COMPOSER_MARKER in text:
        print("COMPOSER_PATCH_ALREADY_PRESENT=YES")
        return
    anchor='''        self.logger.info("draw times set")
'''
    block='''        # DJGABO_INSTRUMENTAL_UNIT_V1
        # El rótulo INSTRUMENTAL y la fila de círculos forman UNA unidad visual:
        # se preparan juntos y empiezan a borrarse juntos en el END del último
        # círculo visible. No cambia ningún timing de letra real.
        for lyric,times in zip(self.lyrics,self.lyric_times):
            for i in range(len(lyric.lines)-1):
                label_line=lyric.lines[i]
                dot_line=lyric.lines[i+1]
                is_label=("INSTRUMENTAL" in str(label_line.text).upper())
                visible_dots=[s for s in dot_line.syllables if str(s.text).strip()]
                looks_dots=bool(visible_dots) and all(str(s.text).strip() in {"§","●"} for s in visible_dots)
                if not (is_label and looks_dots):
                    continue
                joint_draw=min(times.line_draw[i],times.line_draw[i+1])
                times.line_draw[i]=joint_draw
                times.line_draw[i+1]=joint_draw
                joint_end=max(s.end_offset for s in visible_dots)
                times.line_erase[i]=joint_end
                times.line_erase[i+1]=joint_end
                self.logger.info(
                    "instrumental visual unit: lines %d/%d draw=%d erase=%d",
                    i,i+1,joint_draw,joint_end
                )

        self.logger.info("draw times set")
'''
    text=replace_once(text,anchor,block,"composer instrumental unit")
    path.write_text(text,encoding="utf-8")
    print("COMPOSER_PATCH=OK")

def main():
    if len(sys.argv)!=4:
        raise SystemExit("uso: patch_production.py index.html normalize.py composer.py")
    editor,normalize,composer=map(lambda s:Path(s).resolve(),sys.argv[1:])
    patch_editor(editor)
    patch_normalize(normalize)
    patch_composer(composer)
    print("MARKER="+MARKER)
    print("NORMALIZE_MARKER="+NORMALIZE_MARKER)
    print("COMPOSER_MARKER="+COMPOSER_MARKER)

if __name__=="__main__":
    main()
