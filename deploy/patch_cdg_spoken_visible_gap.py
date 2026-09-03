#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
EDITOR=ROOT/'editor_v1'/'index.html'
NORMALIZE=ROOT/'renderer'/'normalize.py'

def replace_once(text, old, new, label):
    if old not in text:
        if new in text:
            print(label+'=ALREADY_PATCHED')
            return text
        raise SystemExit('PATCH_FAIL:'+label)
    return text.replace(old,new,1)

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
for p in (EDITOR,NORMALIZE):
    if not p.is_file():
        raise SystemExit('MISSING:'+str(p))
    shutil.copy2(p,p.with_name(p.name+'.bak_spoken_visible_gap_'+stamp))

# Preview: R11 must be based on the whole visible gap between sung words,
# not on the duration of one merged spoken fragment.
e=EDITOR.read_text(encoding='utf-8')
old_js="""    const hasSpoken=overlaps.length>0;
    const longSpoken=overlaps.some(([a,b])=>(b-a)>=(c.spokenMin??4));
    const regularGap=gap>=c.minGap && !hasSpoken;
    if(!longSpoken && !regularGap) continue;"""
new_js="""    const hasSpoken=overlaps.length>0;
    // R11: HABLADO oculta texto. Si el HUECO VISUAL entre canto y canto
    // dura >= spokenMin, mostramos INSTRUMENTAL aunque el hablado tenga
    // pausas internas o Scribe lo haya dividido en varios intervalos.
    const longSpoken=hasSpoken && gap>=(c.spokenMin??6);
    const regularGap=gap>=c.minGap && !hasSpoken;
    if(!longSpoken && !regularGap) continue;"""
e=replace_once(e,old_js,new_js,'preview_visible_spoken_gap')
e=e.replace(
"""    // R28: un bloque HABLADO largo se trata visualmente como pausa de canto,
    // pero la próxima letra se libera 3 s antes del siguiente START cantado.""",
"""    // R11: un hueco visual creado por HABLADO se trata como pausa de canto.
    // La próxima letra se libera spokenLead segundos antes del siguiente START cantado.""",
1)
EDITOR.write_text(e,encoding='utf-8')

# Final renderer: same exact rule as preview.
n=NORMALIZE.read_text(encoding='utf-8')
old_py="""        has_spoken = bool(overlaps)
        long_spoken = any((sb - sa) >= spoken_min for sa, sb in overlaps)
        regular_gap = gap >= min_gap and not has_spoken"""
new_py="""        has_spoken = bool(overlaps)
        # R11: la duración que manda es el HUECO VISUAL entre dos líneas cantadas.
        # Los intervalos HABLADO sólo prueban que ese hueco fue ocultado a propósito.
        # Así, pausas internas del locutor no parten el bloque ni impiden INSTRUMENTAL.
        long_spoken = has_spoken and gap >= spoken_min
        regular_gap = gap >= min_gap and not has_spoken"""
n=replace_once(n,old_py,new_py,'renderer_visible_spoken_gap')
n=n.replace(
"""        # R28: HABLADO largo (>4 s por defecto) se comporta visualmente como
        # una pausa de canto: no exportamos ese texto y mostramos INSTRUMENTAL.
        # La próxima página cantada queda libre `spoken_lead` segundos antes.""",
"""        # R11: si HABLADO crea un hueco visual >= spoken_min, no exportamos
        # ese texto y mostramos INSTRUMENTAL. La próxima página cantada queda
        # libre spoken_lead segundos antes.""",
1)
NORMALIZE.write_text(n,encoding='utf-8')

print('PATCH_SPOKEN_VISIBLE_GAP=OK')
