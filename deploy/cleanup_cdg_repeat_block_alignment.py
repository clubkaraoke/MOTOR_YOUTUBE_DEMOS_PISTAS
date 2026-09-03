#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
EDITOR=ROOT/'editor_v1'/'index.html'

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
for p in (SERVER,EDITOR):
    if not p.is_file():
        raise SystemExit('MISSING:'+str(p))
    shutil.copy2(p,p.with_name(p.name+'.bak_repeat_cleanup_'+stamp))

s=SERVER.read_text(encoding='utf-8')
marker='def _ai_norm_repeat_token(text):'
count=s.count(marker)
while count>1:
    a=s.find(marker)
    b=s.find(marker,a+len(marker))
    if a<0 or b<0: break
    s=s[:a]+s[b:]
    count=s.count(marker)

required=[
    'def _ai_repeat_profile(clean):',
    'def _ai_alignment_quality(aligned):',
    'def _ai_align_repeat_chunks(',
    "session(d.get('token'))",
    "strategy='repeat_chunks_partial' if reviews else 'repeat_chunks'",
]
for x in required:
    if x not in s:
        raise SystemExit('MISSING_MARKER:'+x)

SERVER.write_text(s,encoding='utf-8')

e=EDITOR.read_text(encoding='utf-8')
for x in [
    'strategy:j.strategy||"full"',
    'const applied=Number(j.applied_words??(j.updates||[]).length)',
]:
    if x not in e:
        raise SystemExit('EDITOR_MISSING:'+x)

print('REPEAT_HELPER_SETS='+str(s.count(marker)))
print('CLEANUP_REPEAT_ALIGNMENT=OK')
