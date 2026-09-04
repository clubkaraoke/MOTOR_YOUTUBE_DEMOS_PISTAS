#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER="DJGABO_REVIEW_RATES_VISIBLE_V1"

def main():
    if len(sys.argv)!=2:
        raise SystemExit("uso: patch_production.py index.html")
    p=Path(sys.argv[1]).resolve()
    text=p.read_text(encoding="utf-8")
    if MARKER in text:
        print("PATCH_ALREADY_PRESENT=YES")
        return
    anchor='''.rates.reviewLocked .rate:not([data-rate="1"]){opacity:.38;cursor:not-allowed}
'''
    if text.count(anchor)!=1:
        raise RuntimeError(f"anchor esperado 1 vez; encontrado {text.count(anchor)}")
    override='''/* DJGABO_REVIEW_RATES_VISIBLE_V1
   La hoja antigua ocultaba .rates durante phase2. Ahora se muestran siempre
   en el transporte; JS deja 2x-5x deshabilitados hasta que la canción esté
   completamente sincronizada. */
body.phase2 #transport .rates{display:flex!important}
body.phase2 #transport .tsep.rateSep{display:block!important}
'''
    text=text.replace(anchor,anchor+override,1)
    p.write_text(text,encoding="utf-8")
    print("PATCH=OK")
    print("MARKER="+MARKER)

if __name__=="__main__":
    main()
