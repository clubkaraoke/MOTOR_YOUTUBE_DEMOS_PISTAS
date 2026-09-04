#!/usr/bin/env python3
from __future__ import annotations
import json, math, re, tempfile, wave, zipfile
from pathlib import Path
from typing import Any
from PIL import Image, ImageFont

ENGINE_VERSION="DJGABO_CDG_ENGINE_V2_0_1"
UPSTREAM_COMMIT="bedbcdc3bdba3aa475c5d8fb08c32fe799b3bf88"
CDG_VISIBLE_WIDTH=280

class EngineV2Error(ValueError):
    pass

def num(v, default=None):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def font_path(project, options):
    s=project.get("cdg_settings") or {}
    candidates=[
        options.get("font_path"), s.get("font_path"), s.get("font"),
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/impact.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for raw in candidates:
        if raw and Path(str(raw)).expanduser().is_file():
            return Path(str(raw)).expanduser().resolve()
    raise EngineV2Error("No encuentro una fuente TTF para CDG V2.")

def width(text, font):
    return sum(float(font.getlength(ch)) for ch in text)

def valid_words(project):
    out=[]; warnings=[]; seen=set()
    for si,seg in enumerate(project.get("segments") or []):
        if not isinstance(seg,dict) or seg.get("kind")=="break":
            continue
        for wi,raw in enumerate(seg.get("words") or []):
            if not isinstance(raw,dict) or raw.get("spoken"):
                continue
            w=dict(raw); wid=str(w.get("id") or f"s{si}w{wi}")
            if wid in seen:
                wid=f"{wid}__{si}_{wi}"
                warnings.append({"kind":"duplicate_id","word_id":wid,"detail":"ID repetido; V2 lo hizo unico."})
            seen.add(wid); w["id"]=wid
            st=num(w.get("start_time"))
            en=num(w.get("end_time"))
            if st is None:
                warnings.append({"kind":"missing_start","word_id":wid,"text":w.get("text"),"detail":"Sin START; palabra excluida."})
                continue
            if en is None or en<=st:
                en=st+0.05
                warnings.append({"kind":"bad_end","word_id":wid,"text":w.get("text"),"detail":"END invalido; fallback 50 ms."})
            w["start_time"]=max(0.0,st); w["end_time"]=max(w["start_time"]+0.001,en)
            out.append(w)
    out.sort(key=lambda w:(w["start_time"],w["end_time"]))
    return out,warnings

def visual_lines(project, font, uppercase):
    valid,_=valid_words(project); byid={w["id"]:w for w in valid}
    lines=[]; warnings=[]
    for seg in project.get("segments") or []:
        if not isinstance(seg,dict) or seg.get("kind")=="break":
            continue
        words=[]
        for raw in seg.get("words") or []:
            if isinstance(raw,dict) and not raw.get("spoken"):
                w=byid.get(str(raw.get("id") or ""))
                if w: words.append(w)
        cur=[]
        for w in words:
            probe=cur+[w]
            txt=" ".join((str(x.get("text") or "").strip().upper() if uppercase else str(x.get("text") or "").strip()) for x in probe)
            if cur and width(txt,font)>CDG_VISIBLE_WIDTH:
                lines.append(cur); cur=[w]
            else:
                cur=probe
        if cur: lines.append(cur)
    for i,line in enumerate(lines):
        txt=" ".join((str(w.get("text") or "").strip().upper() if uppercase else str(w.get("text") or "").strip()) for w in line)
        px=width(txt,font)
        if px>CDG_VISIBLE_WIDTH+.5:
            warnings.append({"kind":"too_wide","line":i+1,"text":txt,"width_px":round(px,2),"max_px":CDG_VISIBLE_WIDTH})
    return lines,warnings

def build_timeline(project:dict, options:dict|None=None)->dict:
    if not isinstance(project,dict): raise EngineV2Error("Proyecto V2 invalido.")
    options=dict(options or {}); s=project.get("cdg_settings") or {}
    uppercase=bool(options.get("uppercase",s.get("uppercase",True)))
    fs=max(12,min(28,int(options.get("font_size") or s.get("font_size") or 18)))
    lpp=max(2,min(8,int(options.get("lines_per_screen") or s.get("lines_per_page") or 6)))
    lth=max(2,min(4,int(options.get("line_tile_height") or s.get("line_tile_height") or 2)))
    read=float(options.get("read_ahead_seconds") or 2.50)
    hold=float(options.get("post_hold_seconds") or .18)
    fp=font_path(project,options); font=ImageFont.truetype(str(fp),fs)
    words,warnings=valid_words(project)
    visual,ww=visual_lines(project,font,uppercase); warnings.extend(ww)
    if not visual: raise EngineV2Error("No hay palabras cantadas con timing para CDG V2.")
    duration=num((project.get("song") or {}).get("duration"))
    last=max(w["end_time"] for w in words) if words else 0
    if duration is None or duration<last: duration=last+3
    lines=[]; lastslot={}
    for i,ws in enumerate(visual):
        slot=i%lpp; st=min(w["start_time"] for w in ws); en=max(w["end_time"] for w in ws)
        display=max(0.0,st-read); prev=lastslot.get(slot)
        if prev:
            display=max(display,float(prev["sweep_end"])+hold)
            prev["remove_at"]=round(display,3)
        short=max(0.0,read-max(0.0,st-display))
        item={
            "line_id":f"v2-line-{i+1}","visual_index":i,"page_index":i//lpp+1,"slot":slot+1,
            "text":" ".join((str(w.get("text") or "").strip().upper() if uppercase else str(w.get("text") or "").strip()) for w in ws),
            "word_ids":[w["id"] for w in ws],"display_at":round(display,3),"remove_at":round(duration+.5,3),
            "sweep_start":round(st,3),"sweep_end":round(en,3),"read_ahead_seconds":round(max(0.0,st-display),3),
            "shortfall_seconds":round(short,3),
            "words":[{"id":w["id"],"text":(str(w.get("text") or "").strip().upper() if uppercase else str(w.get("text") or "").strip()),
                      "start":round(float(w["start_time"]),3),"end":round(float(w["end_time"]),3),
                      "role":str(w.get("vocal_role") or "none")} for w in ws],
        }
        lines.append(item); lastslot[slot]=item
    return {
        "engine":ENGINE_VERSION,"upstream":"nomadkaraoke/karaoke-gen","upstream_commit":UPSTREAM_COMMIT,
        "policy":{"elevenlabs_word_start_end_are_immutable":True,"preview_and_cdg_share_this_timeline":True,
                  "intro_delay_seconds":0,"hidden_offsets":False,"phase":"KARAOKE_CORE_FIRST"},
        "song":project.get("song") or {},"duration":round(float(duration),3),
        "layout":{"visible_width":280,"screen_width":300,"screen_height":216,"lines_per_screen":lpp,
                  "font_size":fs,"font_path_server":str(fp),"line_tile_height":lth,
                  "row":max(1,(18-lpp*lth)//2),"stroke_width":int(options.get("stroke_width") or s.get("stroke_width") or 1)},
        "lines":lines,"warnings":warnings,"source_word_count":len(words),"rendered_line_count":len(lines),
    }

def silent_wav(path:Path, seconds:float, rate:int=44100):
    frames=max(1,int((seconds+.5)*rate)); path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),"wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
        block=b"\0\0"*rate
        while frames:
            n=min(frames,rate); wf.writeframes(block[:n*2]); frames-=n

def render_cdg(project:dict, output_dir:Path, options:dict|None=None)->dict:
    options=dict(options or {}); tl=build_timeline(project,options)
    output_dir=Path(output_dir).resolve(); output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"timeline_v2.json").write_text(json.dumps(tl,ensure_ascii=False,indent=2),encoding="utf-8")
    try:
        from vendor.nomad_cdgmaker.composer import KaraokeComposer
        from vendor.nomad_cdgmaker.config import Settings,SettingsLyric,SettingsSinger
    except Exception as e:
        raise EngineV2Error("No se pudo cargar Nomad cdgmaker V2: "+str(e)) from e
    sync=[]; ends=[]
    for line in tl["lines"]:
        for w in line["words"]:
            sync.append(int(round(float(w["start"])*100))); ends.append(int(round(float(w["end"])*100)))
    layout=tl["layout"]; song=project.get("song") or {}
    title=str(song.get("title") or "CDG V2"); artist=str(song.get("artist") or "DJGABO")
    outname=re.sub(r"[^A-Za-z0-9._-]+","_",f"{artist}-{title}-V2").strip("_") or "cdg-v2"
    with tempfile.TemporaryDirectory(prefix="djgabo-cdg-v2-") as td0:
        td=Path(td0); audio=td/"clock.wav"; silent_wav(audio,float(tl["duration"]))
        black=td/"black.png"; Image.new("RGB",(300,216),(0,0,0)).save(black)
        lyric=SettingsLyric(
            sync=sync,end_sync=ends,text="\n".join(x["text"] for x in tl["lines"]),
            line_tile_height=int(layout["line_tile_height"]),lines_per_page=int(layout["lines_per_screen"]),
            singer=1,row=int(layout["row"]),explicit_timeline=True,
            line_draw=[int(round(float(x["display_at"])*100)) for x in tl["lines"]],
            line_erase=[int(round(float(x["remove_at"])*100)) for x in tl["lines"]],
        )
        singer=SettingsSinger(inactive_fill="#FFFFFF",inactive_stroke="#000000",active_fill="#F2B705",active_stroke="#000000")
        cfg=Settings(
            title=title,artist=artist,file=audio,font=Path(layout["font_path_server"]),
            title_screen_background=black,outro_background=black,outname=outname,
            clear_mode="delayed",sync_offset=0,highlight_bandwidth=int(options.get("highlight_bandwidth") or 4),
            draw_bandwidth=int(options.get("draw_bandwidth") or 1),background="#000000",border="#000000",
            font_size=int(layout["font_size"]),stroke_width=int(layout["stroke_width"]),stroke_type="octagon",
            instrumentals=[],singers=[singer],lyrics=[lyric],intro_duration_seconds=0.0,
            first_syllable_buffer_seconds=0.0,outro_text_line1="",outro_text_line2="",
        )
        kc=KaraokeComposer(cfg,relative_dir=td); kc.compose()
        zp=td/f"{outname}.zip"
        if not zp.is_file(): raise EngineV2Error("Nomad cdgmaker no produjo ZIP.")
        with zipfile.ZipFile(zp,"r") as zf: data=zf.read(f"{outname}.cdg")
    out=output_dir/"output_v2.cdg"; tmp=output_dir/"output_v2.tmp"; tmp.write_bytes(data); tmp.replace(out)
    return {"ok":True,"engine":ENGINE_VERSION,"timeline":tl,"timeline_path":str(output_dir/"timeline_v2.json"),
            "cdg_path":str(out),"cdg_size":out.stat().st_size,"warnings":tl.get("warnings") or []}

def smoke_project():
    return {"song":{"artist":"DJGABO","title":"V2 SMOKE","duration":8.0},
            "cdg_settings":{"lines_per_page":4,"font_size":18,"stroke_width":1},
            "segments":[{"kind":"lyrics","words":[{"id":"w1","text":"HOLA","start_time":2.0,"end_time":2.5},
             {"id":"w2","text":"MUNDO","start_time":2.6,"end_time":3.3}]},
             {"kind":"lyrics","words":[{"id":"w3","text":"PRUEBA","start_time":4.0,"end_time":4.6},
             {"id":"w4","text":"V2","start_time":4.7,"end_time":5.2}]}]}

if __name__=="__main__":
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("--smoke",action="store_true"); ap.add_argument("--project",type=Path); ap.add_argument("--out",type=Path,default=Path("/tmp/djgabo-v2-smoke"))
    a=ap.parse_args(); p=smoke_project() if a.smoke else json.loads(a.project.read_text(encoding="utf-8"))
    r=render_cdg(p,a.out); print(json.dumps({"ok":r["ok"],"size":r["cdg_size"],"lines":r["timeline"]["rendered_line_count"]}))
