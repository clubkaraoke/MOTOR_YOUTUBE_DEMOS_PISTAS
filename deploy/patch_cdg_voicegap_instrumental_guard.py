#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
EDITOR=ROOT/'editor_v1'/'index.html'
NORMALIZE=ROOT/'renderer'/'normalize.py'

def replace_once(text,old,new,label):
    if new in text:
        print(label+'=ALREADY_PATCHED')
        return text
    if old not in text:
        raise SystemExit('PATCH_FAIL:'+label)
    return text.replace(old,new,1)

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
for p in (EDITOR,NORMALIZE):
    if not p.is_file():
        raise SystemExit('MISSING:'+str(p))
    shutil.copy2(p,p.with_name(p.name+'.bak_voicegap_inst_'+stamp))

# ---------------- frontend / preview ----------------
e=EDITOR.read_text(encoding='utf-8')

old="""    const hasSpoken=overlaps.length>0;
    // R11: HABLADO oculta texto. Si el HUECO VISUAL entre canto y canto
    // dura >= spokenMin, mostramos INSTRUMENTAL aunque el hablado tenga
    // pausas internas o Scribe lo haya dividido en varios intervalos.
    const longSpoken=hasSpoken && gap>=(c.spokenMin??6);
    const regularGap=gap>=c.minGap && !hasSpoken;"""
new="""    const hasSpoken=overlaps.length>0;
    const voiceOverlaps=(S.doc?.ai?.voice_gaps||[])
      .map(g=>[Math.max(Number(g.start),base),Math.min(Number(g.end),next.start_time)])
      .filter(([a,b])=>Number.isFinite(a)&&Number.isFinite(b)&&b>a);
    const hasUntranscribedVoice=voiceOverlaps.length>0;
    // R11/R13:
    // - HABLADO explícito sí puede convertirse en INSTRUMENTAL cuando el hueco visual >= 6 s.
    // - Una pausa "regular" sólo es instrumental si NO hay voz detectada dentro del hueco.
    //   Así evitamos falsos INSTRUMENTAL cuando Scribe dejó una vocalización sin texto.
    const longSpoken=hasSpoken && gap>=(c.spokenMin??6);
    const regularGap=gap>=c.minGap && !hasSpoken && !hasUntranscribedVoice;"""
e=replace_once(e,old,new,'preview_voicegap_guard')

old="""    const hasSpoken=overlaps.length>0;
    const longSpoken=hasSpoken && gap>=(c.spokenMin??6);
    const regularGap=gap>=c.minGap && !hasSpoken;
    if(!(longSpoken||regularGap) && gap<1.5) continue;
    out.push({"""
new="""    const hasSpoken=overlaps.length>0;
    const voiceOverlaps=(S.doc?.ai?.voice_gaps||[])
      .map(g=>[Math.max(Number(g.start),base),Math.min(Number(g.end),next.start_time)])
      .filter(([a,b])=>Number.isFinite(a)&&Number.isFinite(b)&&b>a);
    const hasUntranscribedVoice=voiceOverlaps.length>0;
    const longSpoken=hasSpoken && gap>=(c.spokenMin??6);
    const regularGap=gap>=c.minGap && !hasSpoken && !hasUntranscribedVoice;
    if(!(longSpoken||regularGap) && gap<1.5) continue;
    out.push({"""
e=replace_once(e,old,new,'diag_voicegap_guard')

old="""      spoken_overlap_seconds:+overlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      rule:longSpoken?"HABLADO>=6s":regularGap?"PAUSA_REGULAR>=6s":"NO_INSTRUMENTAL",
      should_show_instrumental:!!(longSpoken||regularGap),"""
new="""      spoken_overlap_seconds:+overlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      untranscribed_voice_overlap_seconds:+voiceOverlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      has_untranscribed_voice:hasUntranscribedVoice,
      rule:longSpoken?"HABLADO>=6s":regularGap?"PAUSA_REGULAR>=6s":hasUntranscribedVoice?"VOZ_SIN_TEXTO_SUPRIME_INSTRUMENTAL":"NO_INSTRUMENTAL",
      should_show_instrumental:!!(longSpoken||regularGap),"""
e=replace_once(e,old,new,'diag_voicegap_fields')

EDITOR.write_text(e,encoding='utf-8')

# ---------------- final renderer ----------------
n=NORMALIZE.read_text(encoding='utf-8')

n=replace_once(
    n,
    "def build_instrumentals(visual: list[list[dict]], style: dict, spoken_intervals: list[tuple[float, float]] | None = None) -> list[list[dict]]:",
    "def build_instrumentals(visual: list[list[dict]], style: dict, spoken_intervals: list[tuple[float, float]] | None = None, voice_gaps: list[tuple[float, float]] | None = None) -> list[list[dict]]:",
    'renderer_signature'
)

n=replace_once(
    n,
    """    spoken_intervals = spoken_intervals or []

    # Un bloque HABLADO puede estar compuesto por muchas palabras cortas.""",
    """    spoken_intervals = spoken_intervals or []
    voice_gaps = voice_gaps or []

    # Un bloque HABLADO puede estar compuesto por muchas palabras cortas.""",
    'renderer_voicegap_init'
)

old="""        has_spoken = bool(overlaps)
        # R11: la duración que manda es el HUECO VISUAL entre dos líneas cantadas.
        # Los intervalos HABLADO sólo prueban que ese hueco fue ocultado a propósito.
        # Así, pausas internas del locutor no parten el bloque ni impiden INSTRUMENTAL.
        long_spoken = has_spoken and gap >= spoken_min
        regular_gap = gap >= min_gap and not has_spoken"""
new="""        has_spoken = bool(overlaps)
        voice_overlaps = [
            (max(va, base), min(vb, start))
            for va, vb in voice_gaps
            if va < start and vb > base
        ]
        voice_overlaps = [(va, vb) for va, vb in voice_overlaps if vb > va]
        has_untranscribed_voice = bool(voice_overlaps)
        # R11/R13:
        # HABLADO explícito manda. Pero un hueco normal NO es instrumental si
        # el detector QA encontró voz sin texto dentro de ese mismo hueco.
        long_spoken = has_spoken and gap >= spoken_min
        regular_gap = gap >= min_gap and not has_spoken and not has_untranscribed_voice"""
n=replace_once(n,old,new,'renderer_voicegap_guard')

old="""    visual = wrap_lines(doc, font, upper)
    visual = smart_page_breaks(visual, float(style.get("smart_page_gap_seconds", 2.0)))
    visual = center_stanza_pages(visual, style["lines_per_page"])
    visual = build_instrumentals(visual, style, spoken_intervals)"""
new="""    voice_gaps = []
    for g in ((doc.get("ai") or {}).get("voice_gaps") or []):
        try:
            va, vb = float(g.get("start")), float(g.get("end"))
        except Exception:
            continue
        if vb > va:
            voice_gaps.append((va, vb))

    visual = wrap_lines(doc, font, upper)
    visual = smart_page_breaks(visual, float(style.get("smart_page_gap_seconds", 2.0)))
    visual = center_stanza_pages(visual, style["lines_per_page"])
    visual = build_instrumentals(visual, style, spoken_intervals, voice_gaps)"""
n=replace_once(n,old,new,'renderer_call_voicegaps')

NORMALIZE.write_text(n,encoding='utf-8')
print('PATCH_VOICE_GAP_SUPPRESSES_FALSE_INSTRUMENTAL=OK')
