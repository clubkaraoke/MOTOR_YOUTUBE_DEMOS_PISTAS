#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

F=Path('/opt/djgabo-cdg-ia-test/editor_v1/index.html')
if not F.is_file():
    raise SystemExit('MISSING:'+str(F))

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
shutil.copy2(F,F.with_name(F.name+'.bak_rolebar_visual_'+stamp))

s=F.read_text(encoding='utf-8')
old='#vocalRoles.armed{background:linear-gradient(rgba(139,92,246,.14),rgba(139,92,246,.14)),var(--bg-elevated);box-shadow:inset 0 0 0 1px rgba(139,92,246,.45)}'
new='#vocalRoles.armed{background:var(--bg-elevated);box-shadow:none}'
if new in s:
    print('ROLEBAR_ARMED_VISUAL=ALREADY_FIXED')
elif old in s:
    s=s.replace(old,new,1)
    F.write_text(s,encoding='utf-8')
    print('ROLEBAR_ARMED_VISUAL=FIXED')
else:
    raise SystemExit('PATCH_FAIL:armed_style_not_found')
