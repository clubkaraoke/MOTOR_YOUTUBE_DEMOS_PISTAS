#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import argparse

SERVER_MARK="DJGABO_REPEAT_MICROTIMING_V1"
EDITOR_MARK="DJGABO_REPEAT_MICROTIMING_DIAG_V1"

def replace_once(text, old, new, label):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontré {n}")
    return text.replace(old,new,1)

def replace_between(text,start,end,new,label):
    i=text.find(start)
    if i<0: raise RuntimeError(f"{label}: no encontré inicio")
    j=text.find(end,i)
    if j<0: raise RuntimeError(f"{label}: no encontré fin")
    return text[:i]+new+text[j:]

def patch_server(path:Path):
    s=path.read_text(encoding="utf-8")
    if SERVER_MARK in s:
        print("SERVER=ALREADY_PRESENT")
        return

    # 1) Detector genérico: también reconoce corridas cortas de 3+ palabras
    # repetidas (AY AY AY, LA LA LA, OH OH OH).
    start="def _ai_repeat_profile(clean):"
    end="\n\ndef _ai_alignment_quality("
    new_profile=r'''def _ai_repeat_profile(clean):
    toks=[_ai_norm_repeat_token(x.get('text')) for x in clean]
    toks=[x for x in toks if x]
    n=len(toks)
    if not n:
        return {'repetitive':False,'unique_ratio':1.0,'dominant_ratio':0.0,'adjacent_ratio':0.0,'max_run':0}
    counts={}
    for t in toks: counts[t]=counts.get(t,0)+1
    unique_ratio=len(counts)/max(1,n)
    dominant_ratio=max(counts.values())/max(1,n)
    adjacent=sum(1 for i in range(1,n) if toks[i]==toks[i-1])/max(1,n-1)
    max_run=1; run=1
    for i in range(1,n):
        if toks[i]==toks[i-1]:
            run+=1; max_run=max(max_run,run)
        else:
            run=1
    # DJGABO_REPEAT_MICROTIMING_V1
    # La protección antigua cubría bloques repetitivos largos (Amor Rebelde).
    # Ahora 3 sílabas iguales seguidas también cuentan como repetición.
    repetitive=(max_run>=3) or (n>=8 and (unique_ratio<=0.45 or dominant_ratio>=0.34 or adjacent>=0.28))
    return {
        'repetitive':bool(repetitive),
        'unique_ratio':round(unique_ratio,4),
        'dominant_ratio':round(dominant_ratio,4),
        'adjacent_ratio':round(adjacent,4),
        'max_run':int(max_run),
    }'''
    s=replace_between(s,start,end,new_profile,"repeat profile")

    # 2) En alineación manual, si el bloque es repetitivo, una sola duración
    # <=60 ms ya fuerza el fallback adaptativo existente.
    s=replace_once(
        s,
        "            quality=_ai_alignment_quality(aligned)\n            strategy='full'",
        "            quality=_ai_alignment_quality(aligned, strict_repeat=profile['repetitive'])\n            strategy='full'",
        "strict repeat initial quality"
    )

    # 3) Reparación determinista del Scribe COMPLETO, sin otra llamada a ElevenLabs.
    anchor="def _ai_repeat_profile(clean):"
    helper=r'''def _ai_repair_repeated_microtimings(words, min_run=3, tiny_seconds=0.060):
    """Repara sólo microtimings imposibles dentro de corridas repetidas.

    Contrato:
      - sólo corridas adyacentes de 3+ tokens iguales normalizados;
      - sólo actúa si hay <=60 ms o inicios prácticamente colapsados;
      - conserva el START del primer repetido y el START del último cuando cabe;
      - conserva el END del último si era válido;
      - nunca mueve la primera palabra posterior al bloque;
      - no toca ninguna palabra fuera de la repetición.

    Ejemplo: AY 106.94 / AY 107.42(10ms) / AY 107.44
    -> mantiene 106.94 y 107.44, redistribuye el AY central.
    """
    seq=list(words or [])
    repairs=[]
    if len(seq)<min_run:
        return seq,repairs

    def tok(item):
        return _ai_norm_repeat_token(
            item.get('master_text') or item.get('text') or item.get('scribe_text') or ''
        )

    i=0
    while i<len(seq):
        t=tok(seq[i])
        if not t:
            i+=1; continue
        j=i+1
        while j<len(seq) and tok(seq[j])==t:
            j+=1
        count=j-i
        if count<min_run:
            i=j; continue

        run=seq[i:j]
        parsed=[]
        valid=True
        for w in run:
            try:
                a=float(w.get('start')); b=float(w.get('end'))
            except Exception:
                valid=False; break
            if b<a:
                valid=False; break
            parsed.append((a,b))
        if not valid:
            i=j; continue

        durations=[max(0.0,b-a) for a,b in parsed]
        starts=[a for a,_ in parsed]
        collapsed=any((starts[k]-starts[k-1])<=0.030 for k in range(1,count))
        tiny=any(d<=tiny_seconds for d in durations)
        if not (tiny or collapsed):
            i=j; continue

        first_start=starts[0]
        last_start=starts[-1]
        last_end=parsed[-1][1]
        onset_span=last_start-first_start

        # Preferimos bloquear los dos onsets exteriores. Si ElevenLabs también
        # colapsó toda la corrida, usamos el END exterior como segundo ancla.
        min_step=0.090
        if onset_span>=min_step*(count-1):
            step=onset_span/(count-1)
            new_starts=[first_start+step*k for k in range(count)]
            method='repeat_locked_first_last_start'
        else:
            usable_end=last_end
            if j<len(seq):
                try:
                    next_start=float(seq[j].get('start'))
                    if next_start>first_start+.10:
                        usable_end=min(usable_end,next_start-.020) if usable_end>first_start else next_start-.020
                except Exception:
                    pass
            span=usable_end-first_start
            if span<min_step*count:
                i=j; continue
            step=span/count
            new_starts=[first_start+step*k for k in range(count)]
            method='repeat_locked_outer_bounds'

        original=[]
        for k,w in enumerate(run):
            oa,ob=parsed[k]
            original.append({
                'index':i+k,
                'text':str(w.get('text') or w.get('master_text') or ''),
                'start':round(oa,6),'end':round(ob,6),
            })

        new_times=[]
        for k,w in enumerate(run):
            ns=new_starts[k]
            if k<count-1:
                cap=new_starts[k+1]-.010
                local_step=max(.001,new_starts[k+1]-ns)
                desired_min=min(.20,max(.10,local_step*.70))
                ne=max(parsed[k][1],ns+desired_min)
                ne=min(cap,ne)
            else:
                ne=last_end
                if ne<=ns+.060:
                    ext_cap=None
                    if j<len(seq):
                        try:
                            ext=float(seq[j].get('start'))
                            if ext>ns+.08: ext_cap=ext-.020
                        except Exception:
                            pass
                    target=ns+min(.20,max(.10,step*.70))
                    ne=min(ext_cap,target) if ext_cap is not None else target
            if ne<=ns+.050:
                # Si ni siquiera caben 50 ms, preferimos no tocar esta corrida.
                new_times=[]; break
            new_times.append((ns,ne))

        if not new_times:
            i=j; continue

        for k,w in enumerate(run):
            oa,ob=parsed[k]; ns,ne=new_times[k]
            w['timing_original_start']=round(oa,6)
            w['timing_original_end']=round(ob,6)
            w['start']=round(ns,6)
            w['end']=round(ne,6)
            w['timing_repaired']=True
            w['timing_repair']='repeat_microtiming_v1'
            w['timing_repair_token']=t

        repairs.append({
            'version':'REPEAT_MICROTIMING_V1',
            'token':t,
            'count':count,
            'start_index':i,
            'end_index':j-1,
            'method':method,
            'trigger':'tiny_or_collapsed',
            'original':original,
            'repaired':[
                {'index':i+k,'start':round(a,6),'end':round(b,6)}
                for k,(a,b) in enumerate(new_times)
            ],
        })
        i=j

    return seq,repairs


'''
    s=s.replace(anchor,helper+anchor,1)

    # 4) La sincronización completa pasa por el reparador antes de construir el proyecto.
    s=replace_once(
        s,
        "            payload=rr.json(); ai_words=payload.get('words') or []\n            if not ai_words: raise ValueError('Scribe v2 no devolvió palabras con tiempos.')",
        "            payload=rr.json(); ai_words=payload.get('words') or []\n            if not ai_words: raise ValueError('Scribe v2 no devolvió palabras con tiempos.')\n            _ai_task_set(task_id,status='running',progress=82,stage='Revisando repeticiones y microtimings…',eta_seconds=7,estimate=True)\n            ai_words,repeat_micro_repairs=_ai_repair_repeated_microtimings(ai_words)",
        "full sync repeat repair"
    )

    # 5) Preservar trazabilidad por palabra en el proyecto del editor.
    old="""            words.append({
                'id':f'w{wi:04d}','text':txt,
                'start_time':round(float(a),6) if a is not None else None,
                'end_time':round(float(b),6) if b is not None else None,
                'locked':False,'spoken':False,'vocal_role':None,
                'ai_confidence':float(item.get('confidence') or 0),
                'ai_status':str(item.get('qa_status') or ''),
                'scribe_text':item.get('scribe_text'),
                'ai_match_type':str(item.get('match_type') or ''),
            })
            wi+=1"""
    new="""            word={
                'id':f'w{wi:04d}','text':txt,
                'start_time':round(float(a),6) if a is not None else None,
                'end_time':round(float(b),6) if b is not None else None,
                'locked':False,'spoken':False,'vocal_role':None,
                'ai_confidence':float(item.get('confidence') or 0),
                'ai_status':str(item.get('qa_status') or ''),
                'scribe_text':item.get('scribe_text'),
                'ai_match_type':str(item.get('match_type') or ''),
            }
            if item.get('timing_repaired'):
                word['ai_timing_repaired']=True
                word['ai_timing_repair']=str(item.get('timing_repair') or 'repeat_microtiming_v1')
                word['ai_timing_repair_token']=str(item.get('timing_repair_token') or '')
                word['ai_original_start']=item.get('timing_original_start')
                word['ai_original_end']=item.get('timing_original_end')
                if not word['ai_match_type'] or word['ai_match_type']=='scribe_raw':
                    word['ai_match_type']='scribe_repeat_repaired'
            words.append(word)
            wi+=1"""
    s=replace_once(s,old,new,"project timing trace")

    # 6) Resumen de reparación en project.ai y en el resultado/log.
    s=replace_once(
        s,
        "            ai['coverage_check']='audio_energy_vs_scribe'\n            diffs=sum(1 for w in ai_words",
        "            ai['coverage_check']='audio_energy_vs_scribe'\n            ai['repeat_microtiming_version']='REPEAT_MICROTIMING_V1'\n            ai['repeat_microtiming_repairs']=repeat_micro_repairs\n            diffs=sum(1 for w in ai_words",
        "project ai repeat summary"
    )
    s=replace_once(
        s,
        "                    source_mode+' · '+str(len(ai_words))+' palabras · diferencias='+str(diffs))",
        "                    source_mode+' · '+str(len(ai_words))+' palabras · diferencias='+str(diffs)+' · microtiming_repairs='+str(len(repeat_micro_repairs)))",
        "log repeat count"
    )
    s=replace_once(
        s,
        "                                 'diff_count':diffs,'voice_gaps':len(gaps),'source_mode':source_mode})",
        "                                 'diff_count':diffs,'voice_gaps':len(gaps),'source_mode':source_mode,\n                                 'repeat_microtiming_repairs':len(repeat_micro_repairs)})",
        "task result repeat count"
    )

    path.write_text(s,encoding="utf-8")
    print("SERVER=PATCHED")
    print("SERVER_MARK="+SERVER_MARK)

def patch_editor(path:Path):
    s=path.read_text(encoding="utf-8")
    if EDITOR_MARK in s:
        print("EDITOR=ALREADY_PRESENT")
        return

    s=replace_once(
        s,
        '    ai_end_extended:!!w.ai_end_extended,\n    ai_end_extension_seconds:w.ai_end_extension_seconds??null',
        '    ai_end_extended:!!w.ai_end_extended,\n    ai_end_extension_seconds:w.ai_end_extension_seconds??null,\n    ai_timing_repaired:!!w.ai_timing_repaired,\n    ai_timing_repair:w.ai_timing_repair||null,\n    ai_timing_repair_token:w.ai_timing_repair_token||null,\n    ai_original_start:w.ai_original_start??null,\n    ai_original_end:w.ai_original_end??null',
        "diagnostic word timing trace"
    )
    s=replace_once(
        s,
        '    diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V5",',
        '    diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V6",',
        "diagnostic V6"
    )
    s=replace_once(
        s,
        '      voice_gaps:(S.doc?.ai?.voice_gaps||[]).length\n    },',
        '      voice_gaps:(S.doc?.ai?.voice_gaps||[]).length,\n      repeat_microtiming_repairs:(S.doc?.ai?.repeat_microtiming_repairs||[]).length\n    },',
        "diagnostic count repeat repairs"
    )
    s=replace_once(
        s,
        '    ai_block_alignments:S.doc?.ai?.block_alignments||[],\n    words',
        '    ai_block_alignments:S.doc?.ai?.block_alignments||[],\n    repeat_microtiming_repairs:S.doc?.ai?.repeat_microtiming_repairs||[],\n    words',
        "diagnostic repeat repair list"
    )
    s=s.replace("</body>",f"<!-- {EDITOR_MARK} -->\n</body>",1)
    path.write_text(s,encoding="utf-8")
    print("EDITOR=PATCHED")
    print("EDITOR_MARK="+EDITOR_MARK)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    a=ap.parse_args()
    root=Path(a.root)
    server=root/"server.py"
    editor=root/"editor_v1"/"index.html"
    for p in (server,editor):
        if not p.is_file(): raise SystemExit("MISSING:"+str(p))
    patch_server(server)
    patch_editor(editor)
    print("PATCH=OK")

if __name__=="__main__":
    main()
