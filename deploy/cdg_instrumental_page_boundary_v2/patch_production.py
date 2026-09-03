#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

E_MARK="DJGABO_INSTRUMENTAL_PAGE_BOUNDARY_V2"
N_MARK="DJGABO_INSTRUMENTAL_PAGE_BOUNDARY_NORMALIZER_V2"

def replace_one(s,old,new,label):
    n=s.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontré {n}")
    return s.replace(old,new,1)

def replace_between(s,start,end,new,label):
    i=s.find(start)
    if i<0: raise RuntimeError(f"{label}: no encontré inicio")
    j=s.find(end,i)
    if j<0: raise RuntimeError(f"{label}: no encontré fin")
    return s[:i]+new+s[j:]

def patch_editor(p:Path):
    s=p.read_text(encoding="utf-8")
    if E_MARK in s:
        return

    start="function pvWrap(){"
    end="\n\n/* El bloque de instrumental son LÍNEAS DE LETRA"
    new=r'''/* DJGABO_INSTRUMENTAL_PAGE_BOUNDARY_V2
   Un INSTRUMENTAL largo también es un corte de página lógico.
   Así ninguna página de letra puede contener texto de antes y después del
   instrumental: el Preview y el CDG comparten exactamente el mismo bloque. */
function pvWrap(){
  const lpp=PV.cfg.linesPerPage;
  const blocks=[];let block=[];
  const flushBlock=()=>{if(block.length){blocks.push(block);block=[];}};

  for(const seg of S.doc.segments){
    if(seg.kind==="break"){flushBlock();continue;}
    const renderWords=(seg.words||[]).filter(w=>!w.spoken);
    if(!renderWords.length)continue;
    if(renderWords.some(w=>w.start_time===null)){flushBlock();break;}
    let cur=[];
    for(const w of renderWords){
      const probe=cur.concat([w]),txt=probe.map(x=>pvText(x.text)).join(" ");
      if(cur.length&&advWidth(txt)>PV.WRAP){block.push(cur);cur=[w];}else cur=probe;
    }
    if(cur.length)block.push(cur);
  }
  flushBlock();

  const instStarts=new Set(
    _diagInstrumentalDecisions()
      .filter(x=>x.renderer_inserted&&x.next&&x.next.id)
      .map(x=>x.next.id)
  );
  const splitBlocks=[];
  for(const b of blocks){
    let bb=[];
    for(const line of b){
      let ll=[];
      for(const w of line){
        if(instStarts.has(w.id) && (ll.length||bb.length)){
          if(ll.length){bb.push(ll);ll=[];}
          if(bb.length){splitBlocks.push(bb);bb=[];}
        }
        ll.push(w);
      }
      if(ll.length)bb.push(ll);
    }
    if(bb.length)splitBlocks.push(bb);
  }

  const out=[];const padPage=()=>{while(out.length%lpp)out.push([]);};
  for(const b of splitBlocks){
    if(out.length)padPage();
    for(let i=0;i<b.length;i+=lpp){
      if(i>0)padPage();
      const chunk=b.slice(i,i+lpp),top=Math.floor((lpp-chunk.length)/2),bottom=lpp-chunk.length-top;
      for(let j=0;j<top;j++)out.push([]);
      out.push(...chunk);
      for(let j=0;j<bottom;j++)out.push([]);
    }
  }
  while(out.length&&!out[out.length-1].length)out.pop();
  return out;
}'''
    s=replace_between(s,start,end,new,"editor pvWrap V2")

    s=replace_one(
        s,
        '      next:{line:_diagLineNoForWord(next),text:next?.text||null,start:next?.start_time??null,end:next?.end_time??null},',
        '      next:{id:next?.id||null,line:_diagLineNoForWord(next),text:next?.text||null,start:next?.start_time??null,end:next?.end_time??null},',
        "editor diag next id"
    )
    s=replace_one(
        s,
        '    version:"CDG_RENDER_PAGES_V1",\n    source:"KARAOKE_PREVIEW",',
        '    version:"CDG_RENDER_PAGES_V2",\n    source:"KARAOKE_PREVIEW_INSTRUMENTAL_BOUNDARIES",',
        "editor render pages version"
    )
    s=replace_one(
        s,
        '    clear_mode:"page",\n    pages',
        '    clear_mode:"page",\n    instrumental_boundaries_locked:true,\n    pages',
        "editor page boundary flag"
    )
    s=replace_one(
        s,
        'diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V4",',
        'diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V5",',
        "editor diagnostic V5"
    )
    s=s.replace("</body>",f"<!-- {E_MARK} -->\n</body>",1)
    p.write_text(s,encoding="utf-8")

def patch_normalize(p:Path):
    s=p.read_text(encoding="utf-8")
    if N_MARK in s:
        return

    s=replace_one(
        s,
        '        "version":"CDG_RENDER_PAGES_V1",\n        "source":"NORMALIZER_FALLBACK_PREVIEW_COMPAT",',
        '        "version":"CDG_RENDER_PAGES_V2",\n        "source":"NORMALIZER_FALLBACK_PREVIEW_COMPAT",',
        "normalizer fallback page version"
    )
    s=replace_one(
        s,
        '        "clear_mode":"page",\n        "authoritative":False,',
        '        "clear_mode":"page",\n        "instrumental_boundaries_locked":False,\n        "authoritative":False,',
        "normalizer fallback boundary flag"
    )

    start="def build_instrumentals("
    end="\n\ndef check_packet_budget("
    new=r'''# DJGABO_INSTRUMENTAL_PAGE_BOUNDARY_NORMALIZER_V2
def build_instrumentals(visual: list[list[dict]], style: dict,
                        spoken_intervals: list[tuple[float, float]] | None = None,
                        voice_gaps: list[tuple[float, float]] | None = None) -> list[list[dict]]:
    """Inserta cada INSTRUMENTAL como página completa SIN tocar slots de letra.

    V1 recorría línea por línea. Si una página cantada empezaba con un slot
    vacío por centrado, copiaba ese vacío y recién después insertaba la página
    instrumental. Eso corría las líneas y mezclaba dos páginas.
    """
    label = style.get("instrumental_label", "INSTRUMENTAL")
    dot = "§"
    n_dots = int(style.get("instrumental_dots", 4))
    span = float(style.get("instrumental_span_seconds", 6.0))
    lead = float(style.get("instrumental_lead_seconds", 4.0))
    min_gap = float(style.get("instrumental_min_gap", 6.0))
    spoken_min = float(style.get("spoken_instrumental_min_seconds", 6.0))
    spoken_lead = float(style.get("spoken_instrumental_lead_seconds", 4.0))
    spoken_join = float(style.get("spoken_block_join_seconds", 0.75))
    lpp = max(2, min(8, int(style.get("lines_per_page", 8))))
    spoken_intervals = spoken_intervals or []
    voice_gaps = voice_gaps or []

    merged_spoken: list[list[float]] = []
    for sa, sb in sorted(spoken_intervals):
        if not merged_spoken or sa - merged_spoken[-1][1] > spoken_join:
            merged_spoken.append([sa, sb])
        else:
            merged_spoken[-1][1] = max(merged_spoken[-1][1], sb)

    def decision(base: float, start: float):
        gap=start-base
        overlaps=[
            (max(sa,base),min(sb,start))
            for sa,sb in merged_spoken
            if sa<start and sb>base
        ]
        overlaps=[(a,b) for a,b in overlaps if b>a]
        voice_overlaps=[
            (max(va,base),min(vb,start))
            for va,vb in voice_gaps
            if va<start and vb>base
        ]
        voice_overlaps=[(a,b) for a,b in voice_overlaps if b>a]
        has_spoken=bool(overlaps)
        has_untranscribed_voice=bool(voice_overlaps)
        long_spoken=has_spoken and gap>=spoken_min
        regular_gap=gap>=min_gap and not has_spoken and not has_untranscribed_voice
        return long_spoken,regular_gap,gap

    def make_instrumental(base: float, start: float, n: int):
        long_spoken,regular_gap,_=decision(base,start)
        if not (label and n_dots>0 and (long_spoken or regular_gap)):
            return None
        use_lead=spoken_lead if long_spoken else lead
        label_slot=.55
        avail=(start-use_lead)-(base+.4)
        use_label=avail>=1.0+label_slot
        use_span=min(span,avail-(label_slot if use_label else 0.0))
        min_span=.6 if long_spoken else 1.0
        if use_span<min_span:
            return None

        dots_end=start-use_lead
        step=use_span/n_dots
        dots_start=dots_end-use_span
        spacer_at=base+.3
        label_at=dots_start-label_slot

        page=[]
        dot_row=lpp//2
        top=max(0,dot_row-1)
        bottom=max(0,lpp-2-top)
        page.extend([[] for _ in range(top)])
        if use_label and label_at>spacer_at+.15:
            page.append([
                {"id":f"in{n}s","text":"_","_inst":True,"_silent":True,
                 "_label":True,"start_time":spacer_at,"end_time":label_at},
                {"id":f"in{n}","text":label,"_inst":True,"_label":True,
                 "start_time":label_at,"end_time":dots_start-.05},
            ])
        else:
            page.append([{
                "id":f"in{n}","text":label,"_inst":True,"_label":True,
                "start_time":spacer_at,
                "end_time":max(spacer_at+.4,dots_start-.05),
            }])
        page.append([
            {"id":f"dot{n}_{i}","text":dot,"_inst":True,"_dotline":True,
             "start_time":dots_start+i*step,
             "end_time":dots_start+(i+1)*step}
            for i in range(n_dots)
        ])
        page.extend([[] for _ in range(bottom)])
        if len(page)!=lpp:
            raise NormalizeError(f"Página instrumental inválida: {len(page)} slots, esperaba {lpp}.")
        return page

    out:list[list[dict]]=[]
    prev_end:float|None=None
    inst_n=0

    for pos in range(0,len(visual),lpp):
        page=list(visual[pos:pos+lpp])
        if len(page)<lpp:
            page += [[] for _ in range(lpp-len(page))]
        content=[line for line in page if line]
        if not content:
            out.extend(page)
            continue

        first=content[0]
        first_start=float(first[0]["start_time"])
        base=(max(0.0,float(style.get("intro_duration_seconds",0.0) or 0.0)+.25)
              if prev_end is None else prev_end)

        inst_page=make_instrumental(base,first_start,inst_n)
        if inst_page is not None:
            out.extend(inst_page)
            inst_n+=1

        scan_end=max(w["end_time"] for w in first)
        for line in content[1:]:
            st=float(line[0]["start_time"])
            long_spoken,regular_gap,_=decision(scan_end,st)
            if long_spoken or regular_gap:
                raise NormalizeError(
                    "Paginado V1 detectado: hay un INSTRUMENTAL dentro de una "
                    "página de letra. Recarga el editor (Ctrl+F5) para generar "
                    "CDG_RENDER_PAGES_V2 antes de renderizar."
                )
            scan_end=max(w["end_time"] for w in line)

        out.extend(page)
        prev_end=max(w["end_time"] for w in content[-1])

    return out
'''
    s=replace_between(s,start,end,new,"normalizer build instrumentals V2")

    needle='''    if isinstance(raw,dict) and isinstance(raw.get("pages"),list):
        declared=int(raw.get("lines_per_page") or lpp)
'''
    repl='''    if isinstance(raw,dict) and isinstance(raw.get("pages"),list):
        if str(raw.get("version") or "") != "CDG_RENDER_PAGES_V2":
            raise NormalizeError(
                "Este proyecto conserva paginado V1. Recarga el editor (Ctrl+F5) "
                "y vuelve a Crear CDG para regenerar las páginas V2."
            )
        declared=int(raw.get("lines_per_page") or lpp)
'''
    s=replace_one(s,needle,repl,"normalizer require V2")

    s=replace_one(
        s,
        '        meta["clear_mode"]="page"\n        meta["authoritative"]=True',
        '        meta["clear_mode"]="page"\n        meta["instrumental_boundaries_locked"]=True\n        meta["authoritative"]=True',
        "normalizer V2 meta flag"
    )

    p.write_text(s,encoding="utf-8")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    a=ap.parse_args()
    root=Path(a.root)
    editor=root/"editor_v1"/"index.html"
    norm=root/"renderer"/"normalize.py"
    for p in (editor,norm):
        if not p.is_file(): raise SystemExit(f"Falta {p}")
    patch_editor(editor)
    patch_normalize(norm)
    print("PATCH=OK")
    print(E_MARK)
    print(N_MARK)

if __name__=="__main__":
    main()
