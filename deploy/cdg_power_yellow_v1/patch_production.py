#!/usr/bin/env python3
from pathlib import Path
import json, sys

MARKER="DJGABO_POWER_YELLOW_V1"

def main():
    if len(sys.argv)!=3:
        raise SystemExit("uso: patch_production.py index.html style.json")
    editor=Path(sys.argv[1]); style=Path(sys.argv[2])
    t=editor.read_text(encoding="utf-8")
    if MARKER not in t:
        old='b.role==="female"?"#FF4FA3":b.role==="male"?"#32B7FF":b.role==="duet"?"#7ED957":"#F2A900"'
        new='b.role==="female"?"#FF4FA3":b.role==="male"?"#32B7FF":b.role==="duet"?"#7ED957":"#FFFF00"'
        if t.count(old)!=1:
            raise RuntimeError("preview default sweep anchor count="+str(t.count(old)))
        t=t.replace(old,new,1)
        t=t.replace("</style>","/* "+MARKER+" · SIN ROL: blanco -> #FFFF00 */\n</style>",1)
        editor.write_text(t,encoding="utf-8")
    d=json.loads(style.read_text(encoding="utf-8"))
    d["highlight_default"]="#FFFF00"
    d["highlight_color"]="#FFFF00"
    style.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("EDITOR_YELLOW=OK")
    print("STYLE_YELLOW=OK")

if __name__=="__main__":
    main()
