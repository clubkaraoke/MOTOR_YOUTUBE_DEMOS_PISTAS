#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

E_MARK="DJGABO_AUTHORITATIVE_PAGES_V1"
N_MARK="DJGABO_AUTHORITATIVE_PAGES_NORMALIZER_V1"
R_MARK="DJGABO_AUTHORITATIVE_PAGES_RENDERER_V1"
C_MARK="DJGABO_AUTHORITATIVE_PAGES_COMPOSER_V1"

def one(text,old,new,label):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontré {n}")
    return text.replace(old,new,1)

def patch_editor(p:Path):
    s=p.read_text(encoding="utf-8")
    if E_MARK in s:
        return

    anchor='''/* --- 2. plan de dibujado, portado de KaraokeComposer._set_draw_times --- */
function pvPlan(){'''
    helper=r'''/* DJGABO_AUTHORITATIVE_PAGES_V1
   Las páginas que ves en Karaoke son las páginas que se exportan.
   El renderer ya no vuelve a reagrupar por pausas ni por heurísticas. */
function buildRenderPagesDecision(){
  const lpp=Math.max(2,Math.min(8,Number(PV.cfg.linesPerPage||6)));
  const raw=pvWrap();
  const pages=[];
  const count=Math.ceil(raw.length/lpp);
  for(let pi=0;pi<count;pi++){
    const lines=[];
    for(let slot=0;slot<lpp;slot++){
      const line=raw[pi*lpp+slot]||[];
      lines.push({
        slot:slot+1,
        word_ids:line.map(w=>w.id),
        text:line.map(w=>pvText(w.text)).join(" ")
      });
    }
    pages.push({page:pi+1,lines});
  }
  return {
    version:"CDG_RENDER_PAGES_V1",
    source:"KARAOKE_PREVIEW",
    lines_per_page:lpp,
    clear_mode:"page",
    pages
  };
}

/* --- 2. plan de dibujado, PAGINADO BLOQUEADO al preview --- */
function pvPlan(){'''
    s=one(s,anchor,helper,"editor page helper anchor")

    old=r'''  const n = lines.length;
  const draw = new Array(n).fill(0), erase = new Array(n).fill(0);
  const firstOfLine = li => syl.find(s => s.li === li);

  let dt = syl[0].s - 900;                                    // 3 s antes
  for(let i=0; i<lpp && i<n; i++){ draw[i] = dt; dt += PV.GAP; }

  for(let k=1; k<syl.length; k++){
    const a = syl[k-1], b = syl[k];
    if(b.li <= a.li) continue;

    const pageA = Math.floor(a.li / lpp), pageB = Math.floor(b.li / lpp);
    if(pageA === pageB){
      let et = Math.min(b.s + 100, a.e + 450);
      for(let i=a.li; i<b.li; i++) if(i<n){ erase[i] = et; et += PV.GAP; }
      continue;
    }
    const aEnd = Math.max(a.e, a.s + 100);
    const inter = b.s - aEnd;
    const lineStart = (firstOfLine(a.li) || a).s;
    let et = Math.min(b.s + 100, aEnd + 450, aEnd + Math.floor(inter/3));
    let dtp = Math.max(lineStart + 100, b.s - 900, aEnd + Math.floor(inter/3));

    if(inter >= 1200){                                        // 4 s o más entre páginas
      for(let i=a.li; i<b.li; i++) if(i<n){ erase[i] = et; et += PV.GAP; }
      dtp = Math.max(dtp, et);
      const start = pageA * lpp;
      for(let i=start; i<start+lpp; i++){ const j=i+lpp; if(j<n){ draw[j]=dtp; dtp+=PV.GAP; } }
      continue;
    }
    dtp = lineStart + 150;
    const start = pageA * lpp;
    for(let i=start; i<a.li; i++){ const j=i+lpp; if(j<n){ draw[j]=dtp; dtp+=PV.GAP; } }
    dtp = Math.max(dtp, aEnd + Math.floor(inter/3));
    for(let i=a.li; i<b.li; i++) if(i<n){ erase[i] = dtp; dtp += PV.GAP; }
    for(let i=a.li; i<b.li; i++){ const j=i+lpp; if(j<n){ draw[j]=dtp; dtp+=PV.GAP; } }
  }
  const last = syl[syl.length-1];
  erase[last.li] = last.e + 600;

  return {lines, geom, syl, draw, erase, lpp};
'''
    new=r'''  const n = lines.length;
  const draw = new Array(n).fill(0), erase = new Array(n).fill(0);

  // PAGE LOCK: la página anterior se limpia completa y la nueva se dibuja
  // completa. Nunca mezclamos filas de dos páginas.
  const pageCount=Math.ceil(n/lpp);
  const pageSyllables=Array.from({length:pageCount},()=>[]);
  for(const x of syl) pageSyllables[Math.floor(x.li/lpp)].push(x);

  if(pageSyllables[0]&&pageSyllables[0].length){
    let dt=pageSyllables[0][0].s-900;
    for(let i=0;i<Math.min(lpp,n);i++){draw[i]=dt;dt+=PV.GAP;}
  }

  for(let page=1;page<pageCount;page++){
    const cur=pageSyllables[page]||[];
    if(!cur.length)continue;
    const prev=(pageSyllables[page-1]||[]);
    const first=cur[0];
    const lastPrev=prev.length?prev[prev.length-1]:null;
    // 3 s de anticipación cuando cabe; nunca alteramos START/END para fabricar hueco.
    const pageAt=Math.max(first.s-900,lastPrev?lastPrev.e+12:first.s-900);

    const prevStart=(page-1)*lpp;
    for(let i=prevStart;i<Math.min(prevStart+lpp,n);i++) erase[i]=pageAt;

    let dt=pageAt;
    const start=page*lpp;
    for(let i=start;i<Math.min(start+lpp,n);i++){draw[i]=dt;dt+=PV.GAP;}
  }

  return {lines, geom, syl, draw, erase, lpp, clearMode:"page"};
'''
    s=one(s,old,new,"editor pvPlan page timing")

    s=one(s,
          'diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V3",',
          'diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V4",',
          "editor diag version")
    s=one(s,
          '    render_timeline:buildRenderTimelineDecision(),\n    ai_block_alignments:S.doc?.ai?.block_alignments||[],',
          '    render_timeline:buildRenderTimelineDecision(),\n    render_pages:buildRenderPagesDecision(),\n    ai_block_alignments:S.doc?.ai?.block_alignments||[],',
          "editor diag pages")
    s=one(s,
          '  out.render_timeline = buildRenderTimelineDecision();\n  for(const seg of out.segments){',
          '  out.render_timeline = buildRenderTimelineDecision();\n  out.render_pages = buildRenderPagesDecision();\n  for(const seg of out.segments){',
          "editor export pages")
    s=s.replace("</body>",f"<!-- {E_MARK} -->\n</body>",1)
    p.write_text(s,encoding="utf-8")

def patch_normalize(p:Path):
    s=p.read_text(encoding="utf-8")
    if N_MARK in s:
        return

    s=one(s,
          '''    render_timeline: dict
    warnings: list[Warning_] = field(default_factory=list)
''',
          '''    render_timeline: dict
    render_pages: dict
    warnings: list[Warning_] = field(default_factory=list)
''',
          "normalizer dataclass pages")

    anchor='''def wipe_spans(visual: list[list[dict]], tail: float = 0.45) -> None:
'''
    helper=r'''# DJGABO_AUTHORITATIVE_PAGES_NORMALIZER_V1
def resolve_render_pages(doc: dict, style: dict, font: ImageFont.FreeTypeFont, uppercase: bool) -> tuple[list[list[dict]], dict]:
    """Reconstruye EXACTAMENTE las páginas que exportó la pestaña Karaoke.

    Si el proyecto es antiguo y todavía no trae render_pages, usamos el mismo
    criterio del preview: saltos explícitos + wrap por ancho + bloques de LPP.
    Ya no se crean páginas nuevas sólo porque exista una pausa temporal.
    """
    lpp=max(2,min(8,int(style.get("lines_per_page",6))))
    raw=doc.get("render_pages")
    by_id={
        str(w.get("id")):w
        for seg in doc.get("segments",[])
        for w in seg.get("words",[])
        if w.get("id") is not None
    }

    if isinstance(raw,dict) and isinstance(raw.get("pages"),list):
        declared=int(raw.get("lines_per_page") or lpp)
        if declared!=lpp:
            raise NormalizeError(
                f"render_pages fue creado con {declared} líneas/página pero el render pide {lpp}."
            )
        visual=[]
        seen=[]
        for pi,page in enumerate(raw["pages"]):
            lines=page.get("lines") if isinstance(page,dict) else None
            if not isinstance(lines,list) or len(lines)!=lpp:
                raise NormalizeError(f"render_pages página {pi+1}: esperaba exactamente {lpp} slots.")
            for li,spec in enumerate(lines):
                ids=(spec or {}).get("word_ids") if isinstance(spec,dict) else None
                ids=[] if ids is None else ids
                if not isinstance(ids,list):
                    raise NormalizeError(f"render_pages página {pi+1} línea {li+1}: word_ids inválido.")
                line=[]
                for wid in ids:
                    w=by_id.get(str(wid))
                    if w is None:
                        raise NormalizeError(f"render_pages referencia una palabra inexistente: {wid}.")
                    if w.get("spoken"):
                        raise NormalizeError(f"render_pages incluyó HABLADO en una línea cantada: {wid}.")
                    if w.get("start_time") is None:
                        raise NormalizeError(f"render_pages incluyó una palabra sin timing: {wid}.")
                    line.append(w); seen.append(str(wid))
                visual.append(line)

        expected=[
            str(w.get("id"))
            for seg in doc.get("segments",[])
            for w in seg.get("words",[])
            if not w.get("spoken") and w.get("start_time") is not None
        ]
        if seen!=expected:
            # El orden también es autoridad: no basta con que estén las mismas palabras.
            raise NormalizeError(
                "render_pages no coincide con el orden actual de las palabras. "
                "Vuelve a abrir/guardar el proyecto para regenerar las páginas."
            )
        meta=dict(raw)
        meta["source"]="KARAOKE_PREVIEW"
        meta["clear_mode"]="page"
        meta["authoritative"]=True
        return visual,meta

    visual=wrap_lines(doc,font,uppercase)
    visual=center_stanza_pages(visual,lpp)
    visual=center_last_page(visual,lpp)
    pages=[]
    for pi in range(0,len(visual),lpp):
        chunk=visual[pi:pi+lpp]
        if len(chunk)<lpp: chunk=chunk+[[] for _ in range(lpp-len(chunk))]
        pages.append({
            "page":len(pages)+1,
            "lines":[
                {"slot":slot+1,
                 "word_ids":[str(w.get("id")) for w in line],
                 "text":" ".join((w["text"].upper() if uppercase else w["text"]) for w in line)}
                for slot,line in enumerate(chunk)
            ],
        })
    return visual,{
        "version":"CDG_RENDER_PAGES_V1",
        "source":"NORMALIZER_FALLBACK_PREVIEW_COMPAT",
        "lines_per_page":lpp,
        "clear_mode":"page",
        "authoritative":False,
        "pages":pages,
    }


def wipe_spans(visual: list[list[dict]], tail: float = 0.45) -> None:
'''
    s=one(s,anchor,helper,"normalizer page resolver anchor")

    old='''    visual = wrap_lines(doc, font, upper)
    visual = smart_page_breaks(visual, float(style.get("smart_page_gap_seconds", 2.0)))
    visual = center_stanza_pages(visual, style["lines_per_page"])
    visual = build_instrumentals(visual, style, spoken_intervals, voice_gaps)
    visual = center_last_page(visual, style["lines_per_page"])
    instrumentals: list[dict] = []
'''
    new='''    # La composición de páginas viene del preview/JSON. No se vuelve a
    # reagrupar por pausas de 2 s: esa era la causa de que Preview y CDG
    # mostraran distintas combinaciones de líneas.
    visual, render_pages = resolve_render_pages(doc, style, font, upper)
    visual = build_instrumentals(visual, style, spoken_intervals, voice_gaps)
    visual = center_last_page(visual, style["lines_per_page"])
    instrumentals: list[dict] = []
'''
    s=one(s,old,new,"normalizer authoritative pages usage")

    s=one(s,
          '''        render_timeline=render_timeline,
        warnings=warns,
''',
          '''        render_timeline=render_timeline,
        render_pages=render_pages,
        warnings=warns,
''',
          "normalizer return pages")
    p.write_text(s,encoding="utf-8")

def patch_render(p:Path):
    s=p.read_text(encoding="utf-8")
    if R_MARK in s:
        return
    old='''        style_run["intro_duration_seconds"] = float(norm.render_timeline["opening"]["duration_seconds"])
        style_run["intro_mode"] = "always" if style_run["intro_duration_seconds"] > 0 else "never"
'''
    new='''        style_run["intro_duration_seconds"] = float(norm.render_timeline["opening"]["duration_seconds"])
        style_run["intro_mode"] = "always" if style_run["intro_duration_seconds"] > 0 else "never"
        # DJGABO_AUTHORITATIVE_PAGES_RENDERER_V1
        # No mezclar filas de página anterior/nueva: el compositor limpia por página.
        style_run["clear_mode"] = "page"
'''
    s=one(s,old,new,"renderer force page clear")
    s=one(s,
          '''        "render_timeline": norm.render_timeline,
    }
''',
          '''        "render_timeline": norm.render_timeline,
        "render_pages": norm.render_pages,
    }
''',
          "renderer report pages")
    p.write_text(s,encoding="utf-8")

def patch_composer(p:Path):
    s=p.read_text(encoding="utf-8")
    if C_MARK in s:
        return
    old=r'''        # Calculate the available time between the start of this line
        # and the desired page draw time
        available_time = wipe.start_offset - page_draw_time
        # Calculate the absolute minimum time from the last line to this
        # line
        # NOTE This is a sensible minimum, but not guaranteed.
        minimum_time = wipe.start_offset - last_wipe.start_offset - 24

        # Warn the user if there's not likely to be enough time
        if minimum_time < 32:
            self.logger.warning("not enough bandwidth to clear screen on lyric " f"{wipe.lyric_index} line {wipe.line_index}")

        # If there's not enough time between the end of the last line
        # and the start of this line, but there is enough time between
        # the start of the last line and the start of this page
        if available_time < 32:
            # Shorten the last wipe's duration to make room
            new_duration = wipe.start_offset - last_wipe.start_offset - 150
            if new_duration > 0:
                last_wipe.end_offset = last_wipe.start_offset + new_duration
                page_draw_time = last_wipe.end_offset + 12
            else:
                last_wipe.end_offset = last_wipe.start_offset
                page_draw_time = last_wipe.end_offset + 32
'''
    new=r'''        # DJGABO_AUTHORITATIVE_PAGES_COMPOSER_V1
        # La página es autoridad y los timings también. Si el hueco es corto,
        # dibujamos tan pronto como sea físicamente posible, pero NUNCA
        # recortamos el barrido anterior para fabricar espacio.
        available_time = wipe.start_offset - page_draw_time
        minimum_time = wipe.start_offset - last_wipe.start_offset - 24
        if minimum_time < 32 or available_time < 32:
            self.logger.warning(
                "tight page transition on lyric %d line %d; preserving syllable timings",
                wipe.lyric_index,
                wipe.line_index,
            )
'''
    s=one(s,old,new,"composer do not mutate wipe for page")
    p.write_text(s,encoding="utf-8")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    ap.add_argument("--composer",required=True)
    a=ap.parse_args()
    root=Path(a.root)
    files=[
        root/"editor_v1"/"index.html",
        root/"renderer"/"normalize.py",
        root/"renderer"/"render.py",
        Path(a.composer),
    ]
    for p in files:
        if not p.is_file():
            raise SystemExit(f"Falta {p}")
    patch_editor(files[0]);patch_normalize(files[1]);patch_render(files[2]);patch_composer(files[3])
    print("PATCH=OK")
    print(E_MARK,N_MARK,R_MARK,C_MARK)

if __name__=="__main__":
    main()
