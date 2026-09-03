#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

E_MARK="DJGABO_SMART_OVERWRITE_RENDER_PLAN_V1"
N_MARK="DJGABO_SMART_OVERWRITE_NORMALIZER_V1"
R_MARK="DJGABO_SMART_OVERWRITE_RENDERER_V1"
C_MARK="DJGABO_SMART_OVERWRITE_COMPOSER_V1"
CFG_MARK="DJGABO_SMART_OVERWRITE_CONFIG_V1"

def one(s,old,new,label):
    n=s.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontré {n}")
    return s.replace(old,new,1)

def between(s,start,end,new,label):
    i=s.find(start)
    if i<0: raise RuntimeError(f"{label}: no encontré inicio")
    j=s.find(end,i)
    if j<0: raise RuntimeError(f"{label}: no encontré fin")
    return s[:i]+new+s[j:]

def patch_editor(p:Path):
    s=p.read_text(encoding="utf-8")
    if E_MARK in s:
        return

    anchor='''/* --- 2. plan de dibujado, PAGINADO BLOQUEADO al preview --- */
function pvPlan(){'''
    helper=r'''/* DJGABO_SMART_OVERWRITE_RENDER_PLAN_V1
   UNA sola fuente de verdad visual:
   - N líneas = capacidad de pantalla, no obligación de borrar por página.
   - display_at = momento en que la línea FUTURA ya debe estar legible.
   - sweep_start/end = timings musicales, jamás se mueven aquí.
   - remove_at = cuándo se libera el slot.
   - los CLEAR completos sólo ocurren en pausas realmente seguras,
     instrumentales o antes del ending.
*/
function buildRenderPlanDecision(){
  const lpp=Math.max(2,Math.min(8,Number(PV.cfg.linesPerPage||6)));
  const readAhead=2.50;
  const postHold=0.18;
  const safeClearGap=4.00;
  const opening=pvOpeningDecision();
  const ending=pvEndingDecision();
  const raw=pvWrap();
  const lines=[];

  for(let li=0;li<raw.length;li++){
    const line=raw[li]||[];
    if(!line.length) continue;
    const timed=line.filter(w=>w.start_time!==null&&w.start_time!==undefined);
    if(!timed.length) continue;
    const st=Math.min(...timed.map(w=>Number(w.start_time)));
    const en=Math.max(...timed.map(w=>Number(w.end_time??w.start_time)));
    const ids=line.map(w=>w.id);
    lines.push({
      line_id:"line:"+ids[0]+":"+ids[ids.length-1],
      visual_index:li,
      slot:(li%lpp)+1,
      word_ids:ids,
      text:line.map(w=>pvText(w.text)).join(" "),
      sweep_start:+st.toFixed(3),
      sweep_end:+en.toFixed(3),
      preferred_display_at:+Math.max(0,st-readAhead).toFixed(3),
      display_at:null,
      remove_at:null,
      read_ahead_seconds:null,
      shortfall_seconds:0
    });
  }

  const inst=_diagInstrumentalDecisions().filter(x=>x.renderer_inserted);
  const instIntervals=inst.map((x,i)=>({
    id:"instrumental:"+i,
    start:Number(x.preview_show_from_seconds),
    end:Number(x.hide_at_seconds),
    next_word_id:x.next?.id||null
  })).filter(x=>Number.isFinite(x.start)&&Number.isFinite(x.end)&&x.end>x.start);

  const clearEvents=[];
  const addClear=(at,reason,meta={})=>{
    at=Number(at);
    if(!Number.isFinite(at)||at<0)return;
    if(clearEvents.some(x=>Math.abs(x.at-at)<.035))return;
    clearEvents.push({at:+at.toFixed(3),reason,...meta});
  };

  for(const it of instIntervals){
    addClear(it.start,"INSTRUMENTAL_START",{instrumental_id:it.id});
    addClear(it.end,"INSTRUMENTAL_END",{instrumental_id:it.id});
  }

  // Una pausa >=4 s da tiempo para un CLEAR barato y read-ahead.
  for(let i=1;i<lines.length;i++){
    const a=lines[i-1],b=lines[i];
    const gap=Number(b.sweep_start)-Number(a.sweep_end);
    const overlapsInst=instIntervals.some(x=>x.start<b.sweep_start&&x.end>a.sweep_end);
    if(gap>=safeClearGap && !overlapsInst){
      addClear(Number(a.sweep_end)+.20,"SAFE_GAP",{gap_seconds:+gap.toFixed(3)});
    }
  }

  if(ending.preview_start>0){
    const last=lines.length?lines[lines.length-1]:null;
    if(!last || Number(last.sweep_end)<=Number(ending.preview_start)){
      addClear(ending.preview_start,"ENDING");
    }
  }
  clearEvents.sort((a,b)=>a.at-b.at);

  const lastBySlot=new Map();
  for(const ev of lines){
    let display=Math.max(Number(ev.preferred_display_at),Number(opening.end||0)+.02);

    // Nunca preparamos letra encima de un INSTRUMENTAL.
    for(const it of instIntervals){
      if(display>=it.start-.001 && display<it.end){
        display=it.end+.05;
      }
      // Si el clear final del instrumental cae después del display preferido,
      // la línea debe esperar a ese clear.
      if(it.end<ev.sweep_start && it.end>display){
        display=it.end+.05;
      }
    }

    // Cualquier CLEAR previo a esta línea invalida dibujos anteriores.
    const lastClear=clearEvents.filter(x=>x.at<ev.sweep_start-.01).slice(-1)[0];
    if(lastClear && lastClear.at>display) display=lastClear.at+.05;

    // Overwrite circular: el slot se reutiliza sólo después del END anterior.
    const prev=lastBySlot.get(ev.slot);
    if(prev){
      const cleared=clearEvents.some(x=>x.at>prev.sweep_end-.001 && x.at<=display+.001);
      if(!cleared) display=Math.max(display,Number(prev.sweep_end)+postHold);
    }

    ev.display_at=+display.toFixed(3);
    ev.read_ahead_seconds=+Math.max(0,Number(ev.sweep_start)-display).toFixed(3);
    ev.shortfall_seconds=+Math.max(0,readAhead-ev.read_ahead_seconds).toFixed(3);
    ev.remove_at=ending.preview_start>0?+ending.preview_start.toFixed(3):+Math.max(Number(S.duration||0),Number(ev.sweep_end)+2).toFixed(3);

    if(prev) prev.remove_at=Math.min(Number(prev.remove_at),display);
    lastBySlot.set(ev.slot,ev);
  }

  // Un CLEAR seguro libera todos los slots ya terminados.
  for(const ce of clearEvents){
    for(const ev of lines){
      if(ev.display_at<ce.at && ev.sweep_end<=ce.at+.001 && ev.remove_at>ce.at){
        ev.remove_at=ce.at;
      }
    }
  }
  for(const ev of lines){
    if(ev.remove_at<=ev.sweep_end) ev.remove_at=+(Number(ev.sweep_end)+postHold).toFixed(3);
    else ev.remove_at=+Number(ev.remove_at).toFixed(3);
  }

  return {
    version:"CDG_RENDER_PLAN_V1",
    source:"KARAOKE_PREVIEW",
    mode:"SMART_OVERWRITE",
    lines_per_screen:lpp,
    read_ahead_seconds:readAhead,
    post_sweep_hold_seconds:postHold,
    safe_clear_gap_seconds:safeClearGap,
    policy:{
      musical_word_timings_are_immutable:true,
      lines_are_capacity_not_page_rule:true,
      preview_and_renderer_share_plan:true,
      full_clear_only_when_safe:true,
      renderer_may_preroll_packets_before_display_at:true
    },
    clear_events:clearEvents,
    instrumental_intervals:instIntervals,
    lines
  };
}

/* --- 2. plan de dibujado, SMART OVERWRITE autoritativo --- */
function pvPlan(){'''
    s=one(s,anchor,helper,"editor render plan helper")

    start='''  const n = lines.length;
  const draw = new Array(n).fill(0), erase = new Array(n).fill(0);

  // PAGE LOCK: la página anterior se limpia completa y la nueva se dibuja
'''
    end='''

let pvT;'''
    new=r'''  const n = lines.length;
  const draw = new Array(n).fill(0), erase = new Array(n).fill(0);
  const renderPlan=buildRenderPlanDecision();
  const byKey=new Map(
    renderPlan.lines.map(ev=>[(ev.word_ids||[]).join("|"),ev])
  );

  // Preview = plan visual real. No vuelve a inventar páginas ni tiempos.
  for(let li=0;li<n;li++){
    const line=lines[li]||[];
    if(!line.length)continue;
    const ev=byKey.get(line.map(w=>w.id).join("|"));
    if(!ev)continue;
    draw[li]=cs(Number(ev.display_at))*3;
    erase[li]=cs(Number(ev.remove_at))*3;
  }

  return {lines, geom, syl, draw, erase, lpp, clearMode:"smart_overwrite",renderPlan};
}'''
    s=between(s,start,end,new,"editor pvPlan smart overwrite")

    s=one(s,
          'diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V6",',
          'diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V7",',
          "editor diagnostic v7")
    s=one(s,
          '    render_pages:buildRenderPagesDecision(),\n    ai_block_alignments:S.doc?.ai?.block_alignments||[],',
          '    render_pages:buildRenderPagesDecision(),\n    render_plan:buildRenderPlanDecision(),\n    ai_block_alignments:S.doc?.ai?.block_alignments||[],',
          "editor diagnostic render plan")
    s=one(s,
          '  out.render_pages = buildRenderPagesDecision();\n  for(const seg of out.segments){',
          '  out.render_pages = buildRenderPagesDecision();\n  out.render_plan = buildRenderPlanDecision();\n  for(const seg of out.segments){',
          "editor export render plan")
    s=s.replace("</body>",f"<!-- {E_MARK} -->\n</body>",1)
    p.write_text(s,encoding="utf-8")

def patch_normalize(p:Path):
    s=p.read_text(encoding="utf-8")
    if N_MARK in s:
        return

    s=one(s,
'''    render_timeline: dict
    render_pages: dict
    warnings: list[Warning_] = field(default_factory=list)
''',
'''    render_timeline: dict
    render_pages: dict
    render_plan: dict
    line_draw_sync: list[int]
    line_erase_sync: list[int]
    screen_clear_sync: list[int]
    warnings: list[Warning_] = field(default_factory=list)
''',
"normalizer dataclass smart overwrite")

    anchor='''def wipe_spans(visual: list[list[dict]], tail: float = 0.45) -> None:
'''
    helpers=r'''# DJGABO_SMART_OVERWRITE_NORMALIZER_V1
def _fallback_render_plan(visual: list[list[dict]], style: dict, duration: float) -> dict:
    """Compatibilidad para proyectos antiguos que todavía no traen render_plan."""
    lpp=max(2,min(8,int(style.get("lines_per_page",6))))
    read_ahead=2.5
    hold=.18
    lines=[]
    last_by_slot={}
    for li,line in enumerate(visual):
        if not line:
            continue
        st=min(float(w["start_time"]) for w in line)
        en=max(float(w.get("end_time") or w["start_time"]) for w in line)
        slot=(li%lpp)+1
        display=max(0.0,st-read_ahead)
        prev=last_by_slot.get(slot)
        if prev is not None:
            display=max(display,float(prev["sweep_end"])+hold)
            prev["remove_at"]=display
        ev={
            "line_id":f"fallback:{line[0].get('id')}:{line[-1].get('id')}",
            "visual_index":li,"slot":slot,
            "word_ids":[str(w.get("id")) for w in line],
            "text":" ".join(str(w.get("text") or "") for w in line),
            "sweep_start":st,"sweep_end":en,
            "preferred_display_at":max(0.0,st-read_ahead),
            "display_at":display,
            "remove_at":max(duration,en+2.0),
            "read_ahead_seconds":max(0.0,st-display),
            "shortfall_seconds":max(0.0,read_ahead-max(0.0,st-display)),
        }
        lines.append(ev); last_by_slot[slot]=ev
    return {
        "version":"CDG_RENDER_PLAN_V1",
        "source":"NORMALIZER_FALLBACK",
        "mode":"SMART_OVERWRITE",
        "lines_per_screen":lpp,
        "read_ahead_seconds":read_ahead,
        "post_sweep_hold_seconds":hold,
        "safe_clear_gap_seconds":4.0,
        "policy":{"preview_and_renderer_share_plan":False},
        "clear_events":[],
        "instrumental_intervals":[],
        "lines":lines,
        "authoritative":False,
    }


def resolve_render_plan(doc: dict, visual: list[list[dict]], style: dict) -> dict:
    raw=doc.get("render_plan")
    if not isinstance(raw,dict) or raw.get("version")!="CDG_RENDER_PLAN_V1":
        return _fallback_render_plan(
            visual,style,float((doc.get("song") or {}).get("duration") or 0.0)
        )

    lpp=max(2,min(8,int(style.get("lines_per_page",6))))
    if int(raw.get("lines_per_screen") or 0)!=lpp:
        raise NormalizeError(
            f"render_plan fue calculado para {raw.get('lines_per_screen')} líneas, "
            f"pero el render pide {lpp}."
        )
    events=raw.get("lines")
    if not isinstance(events,list):
        raise NormalizeError("render_plan.lines no es una lista.")

    expected=[
        [str(w.get("id")) for w in line]
        for line in visual if line
    ]
    got=[
        [str(x) for x in (ev.get("word_ids") or [])]
        for ev in events if isinstance(ev,dict)
    ]
    if got!=expected:
        raise NormalizeError(
            "render_plan no coincide con las líneas actuales. "
            "Recarga el editor para regenerar el plan visual."
        )

    out=dict(raw)
    out["authoritative"]=True
    out["source"]="KARAOKE_PREVIEW"
    return out


def _line_draw_cost_seconds(line: list[dict], font: ImageFont.FreeTypeFont,
                            line_tile_height: int, draw_bw: int, highlight_bw: int,
                            uppercase: bool) -> float:
    if not line:
        return 0.0
    txt=" ".join(
        (str(w.get("text") or "").upper() if uppercase else str(w.get("text") or ""))
        for w in line
    )
    width=max(1.0,text_width(txt,font))
    tile_cols=max(1,math.ceil(width/6.0))
    packets=tile_cols*max(1,int(line_tile_height))
    share=max(1,int(draw_bw))/max(1,int(draw_bw)+int(highlight_bw))
    throughput=max(1.0,300.0*share)
    return max(.08,packets/throughput)


def materialize_render_plan(visual: list[list[dict]], plan: dict, style: dict,
                            font: ImageFont.FreeTypeFont, uppercase: bool):
    """Convierte display/remove del JSON a PRE-ROLL físico para CDG.

    El JSON manda sobre el momento visible. El backend sólo comienza a mandar
    tiles un poco antes para que la línea esté COMPLETA en display_at.
    """
    lpp=max(2,min(8,int(style.get("lines_per_page",6))))
    event_map={
        tuple(str(x) for x in (ev.get("word_ids") or [])):dict(ev)
        for ev in (plan.get("lines") or [])
        if isinstance(ev,dict)
    }
    line_draw=[0]*len(visual)
    line_erase=[0]*len(visual)
    enriched=[]
    record_by_visual={}

    for li,line in enumerate(visual):
        if not line:
            continue
        if line[0].get("_inst"):
            st=min(float(w["start_time"]) for w in line)
            en=max(float(w.get("end_time") or w["start_time"]) for w in line)
            cost=_line_draw_cost_seconds(
                line,font,style["line_tile_height"],
                style["draw_bandwidth"],style["highlight_bandwidth"],uppercase
            )
            draw=max(0.0,st-cost-.08)
            erase=max(en+.05,st+.10)
            rec={
                "kind":"instrumental","visual_index":li,"slot":(li%lpp)+1,
                "display_at":st,"cdg_draw_begin_at":draw,"remove_at":erase,
                "cdg_draw_cost_seconds":cost,
            }
        else:
            key=tuple(str(w.get("id")) for w in line)
            ev=event_map.get(key)
            if ev is None:
                raise NormalizeError(
                    "No encuentro en render_plan la línea: "+
                    " ".join(str(w.get("text") or "") for w in line)
                )
            display=float(ev["display_at"])
            remove=float(ev["remove_at"])
            cost=_line_draw_cost_seconds(
                line,font,style["line_tile_height"],
                style["draw_bandwidth"],style["highlight_bandwidth"],uppercase
            )
            draw=max(0.0,display-cost)
            rec=dict(ev)
            rec.update({
                "kind":"lyric","visual_index":li,
                "cdg_draw_begin_at":draw,
                "cdg_draw_cost_seconds":cost,
            })
            erase=remove
        line_draw[li]=int(round(draw*100))
        line_erase[li]=int(round(erase*100))
        enriched.append(rec)
        record_by_visual[li]=rec

    # Para overwrite, el borrado del ocupante anterior debe terminar ANTES
    # de que empiece el pre-roll de la línea nueva en el mismo slot.
    last_by_slot={}
    for li in sorted(record_by_visual):
        rec=record_by_visual[li]
        slot=int(rec["slot"])
        prev_li=last_by_slot.get(slot)
        if prev_li is not None:
            prev=record_by_visual[prev_li]
            erase_cost=_line_draw_cost_seconds(
                visual[prev_li],font,style["line_tile_height"],
                style["draw_bandwidth"],style["highlight_bandwidth"],uppercase
            )
            replace_begin=float(rec["cdg_draw_begin_at"])
            desired=max(float(prev.get("sweep_end") or 0.0)+.02,replace_begin-erase_cost)
            line_erase[prev_li]=min(line_erase[prev_li],int(round(max(0.0,desired)*100)))
            prev["cdg_erase_begin_at"]=line_erase[prev_li]/100.0
        last_by_slot[slot]=li

    clear_sync=[]
    for ce in (plan.get("clear_events") or []):
        if not isinstance(ce,dict): continue
        try: at=max(0.0,float(ce.get("at")))
        except Exception: continue
        clear_sync.append(int(round(at*100)))
    clear_sync=sorted(set(clear_sync))

    # Las líneas que el plan dice que mueren en un CLEAR se dejan exactamente
    # en ese punto; el compositor hará memory preset y saltará sus erases.
    clear_set=set(clear_sync)
    for li,rec in record_by_visual.items():
        rm=int(round(float(rec.get("remove_at") or 0)*100))
        if rm in clear_set:
            line_erase[li]=rm
            rec["cdg_erase_begin_at"]=rm/100.0
        else:
            rec.setdefault("cdg_erase_begin_at",line_erase[li]/100.0)

    out=dict(plan)
    out["renderer_mode"]="EXPLICIT_SMART_OVERWRITE"
    out["renderer_lines"]=enriched
    out["screen_clear_sync"]=clear_sync
    return line_draw,line_erase,clear_sync,out


def wipe_spans(visual: list[list[dict]], tail: float = 0.45) -> None:
'''
    s=one(s,anchor,helpers,"normalizer helpers anchor")

    old='''    visual, render_pages = resolve_render_pages(doc, style, font, upper)
    visual = build_instrumentals(visual, style, spoken_intervals, voice_gaps)
    visual = center_last_page(visual, style["lines_per_page"])
    instrumentals: list[dict] = []

    wipe_spans(visual)
'''
    new='''    visual, render_pages = resolve_render_pages(doc, style, font, upper)
    render_plan = resolve_render_plan(doc, visual, style)
    visual = build_instrumentals(visual, style, spoken_intervals, voice_gaps)
    visual = center_last_page(visual, style["lines_per_page"])
    instrumentals: list[dict] = []

    wipe_spans(visual)
    line_draw_sync,line_erase_sync,screen_clear_sync,render_plan = materialize_render_plan(
        visual,render_plan,style,font,upper
    )
'''
    s=one(s,old,new,"normalizer use render plan")

    s=one(s,
'''        render_timeline=render_timeline,
        render_pages=render_pages,
        warnings=warns,
''',
'''        render_timeline=render_timeline,
        render_pages=render_pages,
        render_plan=render_plan,
        line_draw_sync=line_draw_sync,
        line_erase_sync=line_erase_sync,
        screen_clear_sync=screen_clear_sync,
        warnings=warns,
''',
"normalizer return plan")

    s=one(s,
'''        f"draw_bandwidth = {style['draw_bandwidth']}",
        f"background = {_q(style['background'])}",
''',
'''        f"draw_bandwidth = {style['draw_bandwidth']}",
        f"screen_clear_sync = [{', '.join(str(x) for x in n.screen_clear_sync)}]",
        f"background = {_q(style['background'])}",
''',
"normalizer toml screen clears")

    s=one(s,
'''        f"lines_per_page = {n.lines_per_page}",
        f"sync = [{', '.join(str(s) for s in n.sync)}]",
        f"syllable_modes = [{', '.join(str(m) for m in n.syllable_modes)}]",
''',
'''        f"lines_per_page = {n.lines_per_page}",
        "explicit_timeline = true",
        f"line_draw = [{', '.join(str(x) for x in n.line_draw_sync)}]",
        f"line_erase = [{', '.join(str(x) for x in n.line_erase_sync)}]",
        f"sync = [{', '.join(str(s) for s in n.sync)}]",
        f"syllable_modes = [{', '.join(str(m) for m in n.syllable_modes)}]",
''',
"normalizer toml explicit line timing")

    p.write_text(s,encoding="utf-8")

def patch_render(p:Path):
    s=p.read_text(encoding="utf-8")
    if R_MARK in s:
        return
    s=one(s,
'''        # DJGABO_AUTHORITATIVE_PAGES_RENDERER_V1
        # No mezclar filas de página anterior/nueva: el compositor limpia por página.
        style_run["clear_mode"] = "page"
''',
'''        # DJGABO_SMART_OVERWRITE_RENDERER_V1
        # El JSON trae display/remove por línea. "delayed" desactiva el CLEAR
        # automático por página; cdgmaker usa los tiempos explícitos.
        style_run["clear_mode"] = "delayed"
''',
"renderer smart overwrite mode")
    s=one(s,
'''        "render_timeline": norm.render_timeline,
        "render_pages": norm.render_pages,
    }
''',
'''        "render_timeline": norm.render_timeline,
        "render_pages": norm.render_pages,
        "render_plan": norm.render_plan,
    }
''',
"renderer report plan")
    p.write_text(s,encoding="utf-8")

def patch_config(p:Path):
    s=p.read_text(encoding="utf-8")
    if CFG_MARK in s:
        return
    s=one(s,
'''class SettingsLyric:
    sync: list[int]
    text: str
    line_tile_height: int
    lines_per_page: int

    singer: int = 1
    row: int = 1
    syllable_modes: list[int] = field(factory=list)
''',
'''class SettingsLyric:
    sync: list[int]
    text: str
    line_tile_height: int
    lines_per_page: int

    singer: int = 1
    row: int = 1
    syllable_modes: list[int] = field(factory=list)
    # DJGABO_SMART_OVERWRITE_CONFIG_V1
    explicit_timeline: bool = False
    line_draw: list[int] = field(factory=list)
    line_erase: list[int] = field(factory=list)
''',
"config lyric explicit timeline")
    s=one(s,
'''    draw_bandwidth: int = 1
    background: RGBColor = field(converter=to_rgbcolor, default="black")
''',
'''    draw_bandwidth: int = 1
    screen_clear_sync: list[int] = field(factory=list)
    background: RGBColor = field(converter=to_rgbcolor, default="black")
''',
"config screen clear sync")
    s=p.read_text(encoding="utf-8") if False else s
    p.write_text(s,encoding="utf-8")

def patch_composer(p:Path):
    s=p.read_text(encoding="utf-8")
    if C_MARK in s:
        return

    old='''        self._set_draw_times()
'''
    new='''        self._set_draw_times()
        # DJGABO_SMART_OVERWRITE_COMPOSER_V1
        self.screen_clear_index = 0
'''
    s=one(s,old,new,"composer screen clear state")

    anchor='''            line_count = len(lyric.lines)
            line_draw: list[int] = [0] * line_count
            line_erase: list[int] = [0] * line_count

            # The first page is drawn 3 seconds before the first
'''
    repl='''            line_count = len(lyric.lines)
            line_draw: list[int] = [0] * line_count
            line_erase: list[int] = [0] * line_count

            # DJGABO_SMART_OVERWRITE_COMPOSER_V1
            cfg_lyric = self.config.lyrics[lyric.lyric_index]
            if getattr(cfg_lyric, "explicit_timeline", False):
                if len(cfg_lyric.line_draw) != line_count or len(cfg_lyric.line_erase) != line_count:
                    raise RuntimeError(
                        f"explicit timeline lyric {lyric.lyric_index}: "
                        f"{len(cfg_lyric.line_draw)}/{len(cfg_lyric.line_erase)} tiempos "
                        f"para {line_count} líneas"
                    )
                line_draw = [sync_to_cdg(int(x)) for x in cfg_lyric.line_draw]
                line_erase = [sync_to_cdg(int(x)) for x in cfg_lyric.line_erase]
                self.logger.info(
                    "using explicit SMART_OVERWRITE line timeline for lyric %d (%d lines)",
                    lyric.lyric_index, line_count
                )
                self.lyric_times.append(LyricTimes(line_draw=line_draw, line_erase=line_erase))
                continue

            # The first page is drawn 3 seconds before the first
'''
    s=one(s,anchor,repl,"composer explicit timeline set")

    anchor2='''        current_time = self.writer.packets_queued - self.sync_offset - self.intro_delay

        should_draw_this_line = False
'''
    repl2='''        current_time = self.writer.packets_queued - self.sync_offset - self.intro_delay

        # CLEAR completo barato (Memory Preset) sólo cuando el render_plan lo
        # autorizó: pausa segura, instrumental o ending.
        clears=getattr(self.config,"screen_clear_sync",[]) or []
        if self.screen_clear_index < len(clears):
            clear_time=sync_to_cdg(int(clears[self.screen_clear_index]))
            if current_time >= clear_time:
                self.logger.debug("explicit screen clear at %d", clear_time)
                for st in lyric_states:
                    st.highlight_queue.clear()
                    st.draw_queue.clear()
                packets=[*memory_preset_repeat(self.BACKGROUND),*load_color_table(self.color_table)]
                if self.config.border is not None:
                    packets.append(border_preset(self.BORDER))
                self.lyric_packet_indices.update(
                    range(self.writer.packets_queued,self.writer.packets_queued+len(packets))
                )
                self.writer.queue_packets(packets)
                # No vuelvas a gastar ancho de banda borrando línea por línea
                # lo que este Memory Preset ya limpió.
                for idx,st in enumerate(lyric_states):
                    tm=self.lyric_times[idx]
                    while st.line_erase < len(tm.line_erase) and tm.line_erase[st.line_erase] <= clear_time:
                        st.line_erase += 1
                self.screen_clear_index += 1
                composer_state.just_cleared=True
                return

        should_draw_this_line = False
'''
    s=one(s,anchor2,repl2,"composer explicit screen clear")

    p.write_text(s,encoding="utf-8")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    ap.add_argument("--composer",required=True)
    ap.add_argument("--config",required=True)
    a=ap.parse_args()
    root=Path(a.root)
    paths=[
        root/"editor_v1"/"index.html",
        root/"renderer"/"normalize.py",
        root/"renderer"/"render.py",
        Path(a.composer),
        Path(a.config),
    ]
    for p in paths:
        if not p.is_file(): raise SystemExit("MISSING:"+str(p))
    patch_editor(paths[0])
    patch_normalize(paths[1])
    patch_render(paths[2])
    patch_composer(paths[3])
    patch_config(paths[4])
    print("PATCH=OK")
    print(E_MARK,N_MARK,R_MARK,C_MARK,CFG_MARK)

if __name__=="__main__":
    main()
