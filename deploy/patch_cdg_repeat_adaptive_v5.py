#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'

def replace_once(text, old, new, label):
    if new in text:
        print(label+'=ALREADY_PATCHED')
        return text
    if old not in text:
        raise SystemExit('PATCH_FAIL:'+label)
    return text.replace(old,new,1)

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
if not SERVER.is_file():
    raise SystemExit('MISSING:'+str(SERVER))
shutil.copy2(SERVER,SERVER.with_name(SERVER.name+'.bak_repeat_v5_'+stamp))

s=SERVER.read_text(encoding='utf-8')

s=replace_once(
    s,
    "def _ai_alignment_quality(aligned):",
    "def _ai_alignment_quality(aligned, strict_repeat=False):",
    "quality_signature"
)

old="""    tiny=sum(1 for v in vals if v<=0.025)
    huge_limit=max(2.0,med*6.0)
    huge=sum(1 for v in vals if v>=huge_limit)
    collapsed=1; run=1"""
new="""    tiny=sum(1 for v in vals if v<=0.025)
    # En bloques repetitivos una sílaba de 30–50 ms suele ser señal de que
    # Forced Alignment comprimió una repetición. En modo normal no tocamos
    # este umbral para no perjudicar palabras cortas reales.
    repeat_short=sum(1 for v in vals if v<=0.060) if strict_repeat else 0
    huge_limit=max(2.0,med*6.0)
    huge=sum(1 for v in vals if v>=huge_limit)
    collapsed=1; run=1"""
s=replace_once(s,old,new,"repeat_short_metric")

old="""    if tiny>=max(3,int(math.ceil(n*.10))): reasons.append('tiny_cluster')
    if huge>=max(2,int(math.ceil(n*.08))): reasons.append('stretched_words')
    if collapsed>=max(3,int(math.ceil(n*.10))): reasons.append('same_timestamp_cluster')
    return {
        'ok':not reasons,
        'reasons':reasons,
        'tiny':tiny,
        'huge':huge,"""
new="""    if tiny>=max(3,int(math.ceil(n*.10))): reasons.append('tiny_cluster')
    if strict_repeat and repeat_short>=1: reasons.append('repeat_short_word')
    if huge>=max(2,int(math.ceil(n*.08))): reasons.append('stretched_words')
    if collapsed>=max(3,int(math.ceil(n*.10))): reasons.append('same_timestamp_cluster')
    return {
        'ok':not reasons,
        'reasons':reasons,
        'tiny':tiny,
        'repeat_short':repeat_short,
        'huge':huge,"""
s=replace_once(s,old,new,"repeat_short_reason")

old="        quality=_ai_alignment_quality(aligned)\n        if not quality['ok']:\n            return split_or_review("
new="        quality=_ai_alignment_quality(aligned, strict_repeat=True)\n        if not quality['ok']:\n            return split_or_review("
s=replace_once(s,old,new,"strict_repeat_in_chunks")

SERVER.write_text(s,encoding='utf-8')
print('PATCH_REPEAT_ADAPTIVE_8_4_2_V5=OK')
