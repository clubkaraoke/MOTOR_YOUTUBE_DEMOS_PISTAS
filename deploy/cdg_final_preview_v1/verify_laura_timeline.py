#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    ap.add_argument("--project",required=True)
    args=ap.parse_args()
    root=Path(args.root).resolve()
    renderer=root/"renderer"
    sys.path.insert(0,str(renderer))
    sys.path.insert(0,str(renderer/"vendor"))

    import normalize as N
    from cdgmaker.composer import KaraokeComposer

    project=Path(args.project)
    doc=N.load(project)
    style=json.loads((renderer/"style.json").read_text(encoding="utf-8"))
    norm=N.normalize(doc,style)

    original=[]
    for seg in doc.get("segments",[]):
        for w in seg.get("words",[]):
            if w.get("spoken"): continue
            if w.get("start_time") is not None:
                original.append(w)
    if not original:
        raise SystemExit("VERIFY_FAIL=no sung words")
    first=min(original,key=lambda w:float(w["start_time"]))
    first_t=float(first["start_time"])
    first_text=str(first.get("text") or "").strip().upper()

    intro=float(style.get("intro_duration_seconds") or 0.0)
    earliest=min(norm.sync)/100.0 if norm.sync else 999999.0

    # La primera instruccion sintetica ya NO puede pertenecer al intervalo del
    # opening. Este era el origen del catch-up que dejaba los circulos vivos.
    if earliest < intro + 0.20:
        raise SystemExit(f"VERIFY_FAIL=synthetic event {earliest:.2f}s before intro safe end {intro+0.20:.2f}s")

    toml=N.to_toml(norm,style,Path("verify-audio.mp3"),"verify-final-cdg",renderer)
    comp=KaraokeComposer.from_string(toml,relative_dir=renderer)

    line_idx=None
    for i,line in enumerate(comp.lyrics[0].lines):
        if first_text and first_text in str(line.text or "").upper():
            line_idx=i
            break
    if line_idx is None:
        raise SystemExit("VERIFY_FAIL=first lyric line not found in composer")

    draw_frame=comp.lyric_times[0].line_draw[line_idx]
    draw_s=draw_frame/300.0
    if not draw_s < first_t:
        raise SystemExit(f"VERIFY_FAIL=first lyric draw {draw_s:.2f}s is not before vocal {first_t:.2f}s")
    if draw_s > first_t-1.0:
        raise SystemExit(f"VERIFY_FAIL=first lyric page too late draw={draw_s:.2f}s vocal={first_t:.2f}s")

    print("VERIFY=PASS")
    print(f"FIRST_WORD={first_text}")
    print(f"FIRST_VOCAL={first_t:.2f}")
    print(f"INTRO_END={intro:.2f}")
    print(f"EARLIEST_SYNTHETIC={earliest:.2f}")
    print(f"FIRST_LYRIC_DRAW={draw_s:.2f}")
    print(f"EXPECTED_CIRCLES_HARD_END={first_t-float(style.get('instrumental_lead_seconds') or 4.0):.2f}")


if __name__=="__main__":
    main()
