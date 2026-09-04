#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER="DJGABO_HABLADO_DETERMINISTIC_V1"

def one(t,old,new,label):
    n=t.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia, encontre {n}")
    return t.replace(old,new,1)

def main():
    if len(sys.argv)!=2:
        raise SystemExit("uso: patch_editor.py /ruta/editor_v1/index.html")
    p=Path(sys.argv[1])
    t=p.read_text(encoding="utf-8")
    if MARKER in t:
        print("ALREADY_PATCHED=YES")
        return

    old='''function applyRoleToIndices(indices, role, quiet=false){
  const ids=[...new Set(indices)].filter(i=>i>=0&&i<S.words.length).sort((a,b)=>a-b);
  if(!ids.length) return false;
  const allTarget=ids.every(i=>wordRole(S.words[i])===role);
  push(docSnapshot());
  ids.forEach(i=>applyWordRole(S.words[i], allTarget?"none":role));
  S.dirty=true; pvInvalidate(); paintNow(); paintLyrics(); draw(); scheduleSave();
  if(!quiet) toast(`${ids.length} palabra${ids.length===1?"":"s"} · ${allTarget?"SIN ROL":roleLabel(role)}`,1100);
  return true;
}'''

    new='''/* DJGABO_HABLADO_DETERMINISTIC_V1
   Los roles son deterministas, NO toggles sobre las palabras:
   - HABLADO siempre deja spoken=true.
   - HOMBRE/MUJER/DUO siempre aplican ese rol.
   - SOLO SIN ROL puede limpiar spoken/rol.
   Esto preserva HABLADO como VACIO para la lógica karaoke/countdown. */
function applyRoleToIndices(indices, role, quiet=false){
  const ids=[...new Set(indices)].filter(i=>i>=0&&i<S.words.length).sort((a,b)=>a-b);
  if(!ids.length) return false;
  push(docSnapshot());
  ids.forEach(i=>applyWordRole(S.words[i], role));
  S.dirty=true; pvInvalidate(); paintNow(); paintLyrics(); draw(); scheduleSave();
  if(!quiet) toast(`${ids.length} palabra${ids.length===1?"":"s"} · ${roleLabel(role)}`,1100);
  return true;
}'''

    t=one(t,old,new,"applyRoleToIndices")
    p.write_text(t,encoding="utf-8")
    print("PATCH=OK")

if __name__=="__main__":
    main()
