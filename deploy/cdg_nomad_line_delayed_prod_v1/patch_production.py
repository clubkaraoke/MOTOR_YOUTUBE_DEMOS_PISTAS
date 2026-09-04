#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "DJGABO_NOMAD_LINE_DELAYED_PROD_V1"

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontre {count}")
    return text.replace(old, new, 1)

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("uso: patch_production.py /ruta/renderer/normalize.py")
    path = Path(sys.argv[1]).resolve()
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("PATCH_ALREADY_PRESENT=YES")
        print("PATH="+str(path))
        return

    # ÚNICO cambio funcional:
    # el Preview original sigue consumiendo render_pages/render_plan como antes.
    # Sólo el TOML del CDG final deja de imponer SMART_OVERWRITE y permite que
    # cdgmaker ejecute su scheduler LINE_DELAYED nativo.
    old_clear = '''        f"screen_clear_sync = [{', '.join(str(x) for x in n.screen_clear_sync)}]",'''
    new_clear = '''        "screen_clear_sync = []",  # DJGABO_NOMAD_LINE_DELAYED_PROD_V1: no clears legacy en CDG final'''
    text = replace_once(text, old_clear, new_clear, "screen_clear_sync")

    old_explicit = '''        "explicit_timeline = true",'''
    new_explicit = '''        "explicit_timeline = false",  # CDG final: Nomad LINE_DELAYED controla draw/erase'''
    text = replace_once(text, old_explicit, new_explicit, "explicit_timeline")

    path.write_text(text, encoding="utf-8")
    print("PATCH=OK")
    print("MARKER="+MARKER)
    print("PATH="+str(path))

if __name__ == "__main__":
    main()
