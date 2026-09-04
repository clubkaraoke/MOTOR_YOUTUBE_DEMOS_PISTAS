#!/usr/bin/env python3
from __future__ import annotations
import json, math, re, tempfile, wave, zipfile
from pathlib import Path
from typing import Any
from PIL import Image, ImageFont

ENGINE_VERSION="DJGABO_CDG_ENGINE_V2_0_3"
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

NOMAD_BG="#111427"
PANEL_ACTIVE_DEFAULT="#F2A900"
PANEL_ACTIVE_MALE="#32B7FF"
PANEL_ACTIVE_FEMALE="#FF4FA3"
PANEL_ACTIVE_DUET="#7ED957"
CLEAR_MODES={"eager","delayed","page"}

def _opt_bool(options,key,default=True):
    v=options.get(key,default)
    if isinstance(v,str):
        return v.strip().lower() not in {"0","false","no","off",""}
    return bool(v)

def _hex_color(v,default):
    raw=str(v or "").strip()
    return raw if re.fullmatch(r"#[0-9A-Fa-f]{6}",raw) else default

def _style_spec(project,options):
    s=project.get("cdg_settings") or {}
    roles=s.get("role_active") if isinstance(s.get("role_active"),dict) else {}
    return {
        "background":NOMAD_BG,
        "border":NOMAD_BG,
        "title_color":_hex_color(options.get("title_color") or s.get("title_color"),"#FFFFFF"),
        "artist_color":_hex_color(options.get("artist_color") or s.get("artist_color"),PANEL_ACTIVE_DEFAULT),
        "inactive_fill":_hex_color(options.get("inactive_fill") or s.get("inactive_fill"),"#FFFFFF"),
        "inactive_stroke":_hex_color(options.get("inactive_stroke") or s.get("inactive_stroke"),"#000000"),
        "active_stroke":_hex_color(options.get("active_stroke") or s.get("active_stroke"),"#000000"),
        "role_active":{
            "none":_hex_color(roles.get("none") or s.get("active_fill"),PANEL_ACTIVE_DEFAULT),
            "male":_hex_color(roles.get("male") or roles.get("hombre") or s.get("male_active_fill"),PANEL_ACTIVE_MALE),
            "female":_hex_color(roles.get("female") or roles.get("mujer") or s.get("female_active_fill"),PANEL_ACTIVE_FEMALE),
            "duet":_hex_color(roles.get("duet") or roles.get("duo") or s.get("duet_active_fill"),PANEL_ACTIVE_DUET),
        },
        "instrumental_fill":_hex_color(options.get("instrumental_fill") or s.get("instrumental_font_color"),PANEL_ACTIVE_DEFAULT),
        "outro_line1_color":_hex_color(options.get("outro_line1_color") or s.get("outro_line1_color"),"#FFFFFF"),
        "outro_line2_color":_hex_color(options.get("outro_line2_color") or s.get("outro_line2_color"),PANEL_ACTIVE_DEFAULT),
    }

def _norm_role(raw):
    r=str(raw or "").strip().lower()
    if r in {"hombre","male","man"}: return "male"
    if r in {"mujer","female","woman"}: return "female"
    if r in {"duo","duet","dúo"}: return "duet"
    return "none"

def _line_role(words):
    for w in words:
        if not w.get("_v2_synthetic"):
            return _norm_role(w.get("vocal_role"))
    return _norm_role(words[0].get("vocal_role")) if words else "none"

def _feature_flags(options):
    return {
        "opening":_opt_bool(options,"show_title_artist",True),
        "instrumental":_opt_bool(options,"show_instrumental",True),
        "ending":_opt_bool(options,"show_ending",True),
        "lead_in":_opt_bool(options,"show_lead_in",True),
    }

def _clamp(v,a,b):
    return a if v<a else b if v>b else v

def _clear_mode(project,options):
    s=project.get("cdg_settings") or {}
    raw=str(options.get("clear_mode") or s.get("clear_mode") or "delayed").strip().lower()
    aliases={"line_delayed":"delayed","line_eager":"eager"}
    raw=aliases.get(raw,raw)
    return raw if raw in CLEAR_MODES else "delayed"

def _all_timed_words(project):
    out=[]
    for si,seg in enumerate(project.get("segments") or []):
        if not isinstance(seg,dict) or seg.get("kind")=="break":
            continue
        for wi,raw in enumerate(seg.get("words") or []):
            if not isinstance(raw,dict):
                continue
            st=num(raw.get("start_time")); en=num(raw.get("end_time"))
            if st is None:
                continue
            if en is None or en<=st:
                en=st+.05
            w=dict(raw)
            w["id"]=str(w.get("id") or f"all-s{si}w{wi}")
            w["start_time"]=max(0.0,float(st))
            w["end_time"]=max(w["start_time"]+.001,float(en))
            out.append(w)
    out.sort(key=lambda w:(w["start_time"],w["end_time"]))
    return out

def _opening_spec(project, all_words):
    s=project.get("cdg_settings") or {}
    rt=project.get("render_timeline") or {}
    ro=rt.get("opening") or {}
    if isinstance(ro,dict) and ("duration_seconds" in ro or "enabled" in ro):
        duration=max(0.0,float(num(ro.get("duration_seconds"),0.0) or 0.0))
        enabled=bool(ro.get("enabled",duration>0)) and duration>0
        return {
            "enabled":enabled,"start":0.0,"end":round(duration if enabled else 0.0,3),
            "duration":round(duration if enabled else 0.0,3),
            "rule":str(ro.get("rule") or "PANEL_RENDER_TIMELINE"),
            "transition":str(s.get("intro_transition") or "centertexttoplogobottomtext"),
        }
    first=min((float(w["start_time"]) for w in all_words),default=None)
    normal=float(num(s.get("intro_duration_seconds"),6.0) or 6.0)
    short=float(num(s.get("intro_short_duration_seconds"),3.0) or 3.0)
    buffer=3.0
    if first is None:
        duration=normal; rule="SIN_VOZ_REAL"
    elif first < short+buffer:
        duration=0.0; rule="AUTO_SKIP_NO_CABE"
    elif first < normal+buffer:
        duration=short; rule="AUTO_SHORT_FITS"
    else:
        duration=normal; rule="AUTO_NORMAL_FITS"
    return {
        "enabled":duration>0,"start":0.0,"end":round(duration,3),"duration":round(duration,3),
        "rule":rule,"transition":str(s.get("intro_transition") or "centertexttoplogobottomtext"),
    }

def _ending_spec(project,duration):
    s=project.get("cdg_settings") or {}
    sec=max(0.0,min(float(duration),8.0))
    return {
        "enabled":sec>0,
        "start":round(max(0.0,float(duration)-sec),3),
        "end":round(float(duration),3),
        "duration":round(sec,3),
        "line1":str(s.get("outro_line1") or "Ediciones Personalizadas."),
        "line2":str(s.get("outro_line2") or "Whatsapp +51921675846"),
        "size":int(num(s.get("outro_size"),18) or 18),
        "transition":str(s.get("outro_transition") or "centertexttoplogobottomtext"),
    }

def _overlaps(a,b,x,y):
    return max(float(a),float(x)) < min(float(b),float(y))

def _instrumentals(project,sung_words,opening):
    if len(sung_words)<2:
        return []
    s=project.get("cdg_settings") or {}
    threshold=max(3.0,float(num(s.get("instrumental_gap_threshold"),6.0) or 6.0))
    all_words=_all_timed_words(project)
    spoken=[(float(w["start_time"]),float(w["end_time"])) for w in all_words if w.get("spoken")]
    voice=[]
    for g in ((project.get("ai") or {}).get("voice_gaps") or []):
        a=num(g.get("start") if isinstance(g,dict) else None)
        b=num(g.get("end") if isinstance(g,dict) else None)
        if a is not None and b is not None and b>a:
            voice.append((float(a),float(b)))
    out=[]
    prev=sung_words[0]
    for nxt in sung_words[1:]:
        prev_end=float(prev["end_time"])
        ns=float(nxt["start_time"])
        gap=ns-prev_end
        blocked=any(_overlaps(prev_end,ns,a,b) for a,b in spoken) or any(_overlaps(prev_end,ns,a,b) for a,b in voice)
        if gap>=threshold and not blocked:
            start=prev_end+.20
            prepare=max(start,ns-3.0)
            if prepare-start>=.40:
                out.append({
                    "id":f"inst-{len(out)+1}",
                    "prev_word_id":str(prev["id"]),
                    "next_word_id":str(nxt["id"]),
                    "gap_start":round(prev_end,3),"gap_end":round(ns,3),"gap_seconds":round(gap,3),
                    "start":round(start,3),"end":round(ns,3),"prepare_at":round(prepare,3),
                    "text":str(s.get("instrumental_text") or "INSTRUMENTAL"),
                    "transition":str(s.get("instrumental_transition") or "topleftmusicalnotes"),
                    "source":"REAL_END_TO_NEXT_START","hidden_plus_2_seconds":False,
                })
        prev=nxt
    return out

def _lead_in_targets(sung_words,opening):
    if not sung_words:
        return []
    out=[]
    prev_end=float(opening.get("end") or 0.0)
    prev_id=None
    for w in sung_words:
        st=float(w["start_time"])
        gap=st-prev_end
        if gap>=3.0 and st-2.0>=float(opening.get("end") or 0.0)+.05:
            out.append({"target_id":str(w["id"]),"target_start":st,"prev_word_id":prev_id,"gap_seconds":gap})
        prev_end=float(w["end_time"])
        prev_id=str(w["id"])
    return out

def _inject_lead_ins(visual, targets, font, uppercase):
    meta=[]
    for serial,target in enumerate(targets,1):
        target_id=target["target_id"]
        li=None; wi=None; target_word=None
        for i,line in enumerate(visual):
            for j,w in enumerate(line):
                if str(w.get("id"))==target_id:
                    li=i; wi=j; target_word=w; break
            if li is not None: break
        if li is None or target_word is None:
            continue
        start=max(0.0,float(target["target_start"])-2.0)
        points=[start+j*.30 for j in range(4)]
        syms=["/>",">",">",">"]
        synth=[]
        for j,(txt,st) in enumerate(zip(syms,points)):
            en=points[j+1] if j+1<len(points) else float(target["target_start"])
            synth.append({
                "id":f"lead-{serial}-{j+1}","text":txt,
                "start_time":round(st,3),"end_time":round(max(st+.01,en),3),
                "spoken":False,"vocal_role":target_word.get("vocal_role"),
                "_v2_synthetic":True,"synthetic_kind":"NOMAD_LEAD_IN",
            })
        existing=list(visual[li])
        probe=synth+existing
        txt=" ".join((str(x.get("text") or "").strip().upper() if uppercase else str(x.get("text") or "").strip()) for x in probe)
        if width(txt,font)<=CDG_VISIBLE_WIDTH:
            visual[li]=probe
            placement="PREPENDED_TO_TARGET_LINE"
        else:
            visual.insert(li,synth)
            placement="SEPARATE_LINE_WIDTH_GUARD"
        meta.append({
            "id":f"lead-{serial}","target_word_id":target_id,
            "start":round(start,3),"end":round(float(target["target_start"]),3),
            "symbols":syms,"placement":placement,"source":"NOMAD_LEAD_IN_POLICY_END_AWARE",
        })
    return visual,meta

def _flat_line_words(lines):
    return [w for line in lines for w in (line.get("words") or [])]

def _singer_plan(tl,SettingsSinger):
    style=tl.get("style") or {}
    role_colors=style.get("role_active") or {}
    roles=[]
    for line in tl.get("lines") or []:
        role=_norm_role(line.get("render_role"))
        if role not in roles:
            roles.append(role)
    # cdgmaker upstream has room for three singer palettes. Prefer explicitly
    # assigned roles; if a song actually uses four, SIN ROL shares singer 1.
    preferred=[r for r in ("male","female","duet","none") if r in roles]
    kept=preferred[:3] or ["none"]
    mapping={r:i+1 for i,r in enumerate(kept)}
    fallback=mapping.get("none") or 1
    for r in roles:
        mapping.setdefault(r,fallback)
    singers=[
        SettingsSinger(
            inactive_fill=style.get("inactive_fill") or "#FFFFFF",
            inactive_stroke=style.get("inactive_stroke") or "#000000",
            active_fill=role_colors.get(r) or PANEL_ACTIVE_DEFAULT,
            active_stroke=style.get("active_stroke") or "#000000",
        )
        for r in kept
    ]
    text="\n".join(f"{mapping.get(_norm_role(line.get('render_role')),fallback)}|{line['text']}" for line in (tl.get("lines") or []))
    return mapping,singers,text

def _make_scheduler(project,tl,options):
    try:
        from vendor.nomad_cdgmaker.composer import KaraokeComposer
        from vendor.nomad_cdgmaker.config import Settings,SettingsLyric,SettingsSinger
    except Exception as e:
        raise EngineV2Error("No se pudo cargar Nomad para calcular filas V2: "+str(e)) from e
    layout=tl["layout"]
    sync=[]; ends=[]
    for line in tl["lines"]:
        for w in line["words"]:
            sync.append(int(round(float(w["start"])*100)))
            ends.append(int(round(float(w["end"])*100)))
    song=project.get("song") or {}
    singer_map,singers,lyric_text=_singer_plan(tl,SettingsSinger)
    lyric=SettingsLyric(
        sync=sync,end_sync=ends,text=lyric_text,
        line_tile_height=int(layout["line_tile_height"]),
        lines_per_page=int(layout["lines_per_screen"]),
        singer=1,row=int(layout["row"]),explicit_timeline=False,
    )
    cfg=Settings(
        title=str(song.get("title") or "CDG V2"),artist=str(song.get("artist") or "DJGABO"),
        file=Path("unused-v2-clock.wav"),font=Path(layout["font_path_server"]),
        title_screen_background=Path("unused-v2-bg.png"),outro_background=Path("unused-v2-bg.png"),
        outname="nomad-layout-preview",clear_mode=str(layout["clear_mode"]),sync_offset=0,
        highlight_bandwidth=int(options.get("highlight_bandwidth") or 4),
        draw_bandwidth=int(options.get("draw_bandwidth") or 1),
        background=NOMAD_BG,border=NOMAD_BG,
        font_size=int(layout["font_size"]),stroke_width=int(layout["stroke_width"]),stroke_type="octagon",
        instrumentals=[],singers=singers,lyrics=[lyric],intro_duration_seconds=0.0,
        first_syllable_buffer_seconds=0.0,outro_text_line1="",outro_text_line2="",
    )
    return KaraokeComposer(cfg,relative_dir=Path("."))

def _apply_nomad_rows(project,tl,options):
    kc=_make_scheduler(project,tl,options)
    if len(kc.lyrics)!=1 or len(kc.lyric_times)!=1:
        raise EngineV2Error("Nomad devolvió un layout de filas inesperado.")
    nlines=kc.lyrics[0].lines; ntimes=kc.lyric_times[0]
    if len(nlines)!=len(tl["lines"]) or len(ntimes.line_draw)!=len(tl["lines"]):
        raise EngineV2Error(
            f"Nomad rows mismatch: timeline={len(tl['lines'])}, nomad_lines={len(nlines)}, draws={len(ntimes.line_draw)}"
        )
    mode=str(tl["layout"]["clear_mode"])
    lpp=int(tl["layout"]["lines_per_screen"])
    duration=float(tl["duration"])
    opening_end=float((tl.get("opening") or {}).get("end") or 0.0)
    ending_start=float((tl.get("ending") or {}).get("start") or duration)
    page_remove={}
    if mode=="page":
        for i in range(len(tl["lines"])):
            np=((i//lpp)+1)*lpp
            if np<len(tl["lines"]):
                page_remove[i]=max(0.0,float(ntimes.line_draw[np])/300.0)
            else:
                page_remove[i]=ending_start if ending_start>0 else duration+.5
    for i,(item,nline) in enumerate(zip(tl["lines"],nlines)):
        draw_frame=int(ntimes.line_draw[i])
        if mode=="page":
            erase_frame=0
            remove=float(page_remove[i])
        else:
            erase_frame=int(ntimes.line_erase[i]) if ntimes.line_erase and i<len(ntimes.line_erase) else 0
            remove=(erase_frame/300.0) if erase_frame>0 else (ending_start if ending_start>0 else duration+.5)
        display=max(opening_end+.01,draw_frame/300.0)
        for inst in tl.get("instrumentals") or []:
            target=str(inst.get("next_word_id") or "")
            start_idx=next((j for j,l in enumerate(tl["lines"]) if target in (l.get("word_ids") or [])),None)
            if start_idx is not None and start_idx<=i<start_idx+lpp:
                display=max(display,float(inst["prepare_at"]))
        if remove<=display:
            remove=max(display+.05,float(item.get("sweep_end") or display)+.18)
        item["display_at"]=round(display,6)
        item["remove_at"]=round(remove,6)
        item["read_ahead_seconds"]=round(max(0.0,float(item["sweep_start"])-display),6)
        item["shortfall_seconds"]=0.0
        item["nomad"]={
            "clear_mode":mode,"line_draw_frame":draw_frame,"line_erase_frame":erase_frame,
            "line_draw":round(draw_frame/300.0,6),
            "line_erase":round(erase_frame/300.0,6) if erase_frame>0 else None,
            "x":int(nline.x),"y":int(nline.y),"width":int(nline.image.width),"height":int(nline.image.height),
            "line_index":int(nline.line_index),
            "page_index":int(nline.line_index)//lpp+1,
            "slot":int(nline.line_index)%lpp+1,
        }
        item["page_index"]=item["nomad"]["page_index"]; item["slot"]=item["nomad"]["slot"]
    tl["layout"]["row_scheduler"]="NOMAD_"+mode.upper()
    tl["layout"]["line_draw_erase_gap_frames"]=int(getattr(kc,"LINE_DRAW_ERASE_GAP",50))
    tl["layouts"]["cdg"]["timing_source"]="nomadkaraoke.cdgmaker."+mode.upper()
    tl["layouts"]["cdg"]["layout"]=tl["layout"]; tl["layouts"]["cdg"]["lines"]=tl["lines"]
    tl["render_metadata"]["cdg_row_scheduler"]="NOMAD_"+mode.upper()
    tl["render_metadata"]["cdg_row_scheduler_upstream_commit"]=UPSTREAM_COMMIT
    tl["policy"]["nomad_controls_cdg_row_draw_erase"]=True
    return tl

def build_timeline(project:dict, options:dict|None=None)->dict:
    if not isinstance(project,dict):
        raise EngineV2Error("Proyecto V2 invalido.")
    options=dict(options or {}); s=project.get("cdg_settings") or {}
    features=_feature_flags(options)
    style=_style_spec(project,options)
    uppercase=bool(options.get("uppercase",s.get("uppercase",True)))
    fs=max(12,min(28,int(options.get("font_size") or s.get("font_size") or 18)))
    lpp=max(2,min(8,int(options.get("lines_per_screen") or s.get("lines_per_page") or 6)))
    lth=max(2,min(4,int(options.get("line_tile_height") or s.get("line_tile_height") or 2)))
    fp=font_path(project,options); font=ImageFont.truetype(str(fp),fs)
    words,warnings=valid_words(project)
    all_words=_all_timed_words(project)

    opening_candidate=_opening_spec(project,all_words)
    opening=dict(opening_candidate)
    if not features["opening"]:
        opening.update({"enabled":False,"start":0.0,"end":0.0,"duration":0.0,"rule":"AB_DISABLED"})

    duration=num((project.get("song") or {}).get("duration"))
    last=max((w["end_time"] for w in words),default=0)
    if duration is None or duration<last:
        duration=last+3

    ending_candidate=_ending_spec(project,float(duration))
    ending=dict(ending_candidate)
    if not features["ending"]:
        ending.update({"enabled":False,"start":round(float(duration),3),"end":round(float(duration),3),"duration":0.0})

    instrumental_candidates=_instrumentals(project,words,opening_candidate)
    instrumentals=instrumental_candidates if features["instrumental"] else []

    visual,ww=visual_lines(project,font,uppercase); warnings.extend(ww)
    if not visual:
        raise EngineV2Error("No hay palabras cantadas con timing para CDG V2.")

    lead_targets=_lead_in_targets(words,opening)
    if features["lead_in"]:
        visual,lead_ins=_inject_lead_ins(visual,lead_targets,font,uppercase)
    else:
        lead_ins=[]

    lines=[]
    for i,ws in enumerate(visual):
        st=min(float(w["start_time"]) for w in ws); en=max(float(w["end_time"]) for w in ws)
        real=[w for w in ws if not w.get("_v2_synthetic")]
        vst=min((float(w["start_time"]) for w in real),default=st)
        ven=max((float(w["end_time"]) for w in real),default=en)
        role=_line_role(ws)
        item={
            "line_id":f"v2-line-{i+1}","visual_index":i,"page_index":i//lpp+1,"slot":i%lpp+1,
            "text":" ".join((str(w.get("text") or "").strip().upper() if uppercase else str(w.get("text") or "").strip()) for w in ws),
            "word_ids":[str(w["id"]) for w in ws],"display_at":0.0,"remove_at":round(float(duration)+.5,3),
            "sweep_start":round(st,3),"sweep_end":round(en,3),"voice_start":round(vst,3),"voice_end":round(ven,3),
            "read_ahead_seconds":0.0,"shortfall_seconds":0.0,"render_role":role,
            "words":[{
                "id":str(w["id"]),
                "text":(str(w.get("text") or "").strip().upper() if uppercase else str(w.get("text") or "").strip()),
                "start":round(float(w["start_time"]),3),"end":round(float(w["end_time"]),3),
                "role":_norm_role(w.get("vocal_role")),
                "synthetic":bool(w.get("_v2_synthetic")),
                "synthetic_kind":w.get("synthetic_kind"),
            } for w in ws],
        }
        lines.append(item)

    yoff=int(num(options.get("lyric_y_offset"),num(s.get("lyric_y_offset"),0)) or 0)
    maxrow=max(0,18-lpp*lth)
    base=max(0,(18-lpp*lth)//2)
    row=int(_clamp(base+yoff,0,maxrow))
    mode=_clear_mode(project,options)
    cdg_layout={
        "visible_width":280,"screen_width":300,"screen_height":216,"lines_per_screen":lpp,
        "font_size":fs,"font_family":str(s.get("font_family") or "impact"),"font_path_server":str(fp),
        "line_tile_height":lth,"row":row,"lyric_y_offset":yoff,
        "stroke_width":int(options.get("stroke_width") if options.get("stroke_width") is not None else s.get("stroke_width") or 1),
        "clear_mode":mode,"background":style["background"],"border":style["border"],
        "preview_scales":[1,2,4],"preview_horizontal_policy":"BROWSER_CENTERED_NOMAD_Y",
    }

    master_words=[{
        "id":w["id"],"text":str(w.get("text") or "").strip(),
        "start":round(float(w["start_time"]),3),"end":round(float(w["end_time"]),3),
        "role":_norm_role(w.get("vocal_role")),"spoken":bool(w.get("spoken",False))
    } for w in words]
    master_segments=[]
    for seg in project.get("segments") or []:
        if not isinstance(seg,dict): continue
        master_segments.append({
            "id":str(seg.get("id") or ""),"kind":str(seg.get("kind") or "lyrics"),"text":str(seg.get("text") or ""),
            "word_ids":[str(w.get("id") or "") for w in (seg.get("words") or []) if isinstance(w,dict)]
        })

    timeline={
        "schema":"djgabo.timeline.v2","schema_version":2,
        "engine":ENGINE_VERSION,"upstream":"nomadkaraoke/karaoke-gen","upstream_commit":UPSTREAM_COMMIT,
        "policy":{
            "elevenlabs_word_start_end_are_immutable":True,"preview_and_cdg_share_this_timeline":True,
            "future_mp4_must_share_this_timeline":True,"intro_delay_seconds":0,"sync_offset_seconds":0,
            "hidden_offsets":False,"word_start_end_still_immutable":True,
            "visual_layer_toggles_must_not_change_word_times":True,
            "instrumental_gap_uses_previous_end_to_next_start":True,
            "nomad_hidden_plus_2_seconds_disabled":True,"mp4_engine_status":"NOT_IMPLEMENTED",
        },
        "features":features,
        "audio":{"duration":round(float(duration),3),"source_file":str((project.get("song") or {}).get("audio_file") or "")},
        "song":project.get("song") or {},"duration":round(float(duration),3),
        "words":master_words,"segments":master_segments,
        "opening":opening,"instrumentals":instrumentals,"lead_ins":lead_ins,"ending":ending,
        "ab_candidates":{
            "opening":opening_candidate,
            "instrumentals":instrumental_candidates,
            "lead_in_targets":lead_targets,
            "ending":ending_candidate,
        },
        "style":style,
        "render_metadata":{
            "source":"ELEVENLABS_START_END","time_unit":"seconds","word_times_mutable":False,
            "feature_signature":"O%d-I%d-L%d-E%d"%(
                1 if features["opening"] else 0,1 if features["instrumental"] else 0,
                1 if features["lead_in"] else 0,1 if features["ending"] else 0
            ),
        },
        "layouts":{"cdg":{"layout":cdg_layout,"lines":lines},"mp4":None},
        "layout":cdg_layout,"lines":lines,
        "warnings":warnings,"source_word_count":len(words),
        "synthetic_lead_in_word_count":sum(len(x.get("symbols") or []) for x in lead_ins),
        "rendered_line_count":len(lines),
    }
    return _apply_nomad_rows(project,timeline,options)

def silent_wav(path:Path, seconds:float, rate:int=44100):
    frames=max(1,int((seconds+.02)*rate)); path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),"wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
        block=b"\0\0"*rate
        while frames:
            n=min(frames,rate); wf.writeframes(block[:n*2]); frames-=n

def _nomad_instrumentals(tl,SettingsInstrumental,bg):
    if not (tl.get("features") or {}).get("instrumental",True):
        return []
    style=tl.get("style") or {}
    out=[]
    for it in tl.get("instrumentals") or []:
        out.append(SettingsInstrumental(
            sync=int(round(float(it["start"])*100)),
            end_sync=int(round(float(it["end"])*100)),
            line_tile_height=int(tl["layout"]["line_tile_height"]),
            wait=False,text=str(it.get("text") or "INSTRUMENTAL"),
            text_align="center",text_placement="bottom middle",
            fill=style.get("instrumental_fill") or PANEL_ACTIVE_DEFAULT,
            stroke=None,background=style.get("background") or NOMAD_BG,
            image=bg,transition=str(it.get("transition") or "topleftmusicalnotes"),x=0,y=0,
        ))
    return out

def render_cdg(project:dict, output_dir:Path, options:dict|None=None)->dict:
    options=dict(options or {}); tl=build_timeline(project,options)
    output_dir=Path(output_dir).resolve(); output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"timeline_v2.json").write_text(json.dumps(tl,ensure_ascii=False,indent=2),encoding="utf-8")
    try:
        from vendor.nomad_cdgmaker.composer import KaraokeComposer
        from vendor.nomad_cdgmaker.config import Settings,SettingsLyric,SettingsSinger,SettingsInstrumental
    except Exception as e:
        raise EngineV2Error("No se pudo cargar Nomad cdgmaker V2: "+str(e)) from e

    sync=[]; ends=[]
    for line in tl["lines"]:
        for w in line["words"]:
            sync.append(int(round(float(w["start"])*100)))
            ends.append(int(round(float(w["end"])*100)))

    layout=tl["layout"]; song=project.get("song") or {}; style=tl.get("style") or {}; features=tl.get("features") or {}
    title=str(song.get("title") or "CDG V2"); artist=str(song.get("artist") or "DJGABO")
    outname=re.sub(r"[^A-Za-z0-9._-]+","_",f"{artist}-{title}-V2").strip("_") or "cdg-v2"

    with tempfile.TemporaryDirectory(prefix="djgabo-cdg-v2-") as td0:
        td=Path(td0); audio=td/"clock.wav"; silent_wav(audio,float(tl["duration"]))
        bg=td/"nomad-bg.png"; Image.new("RGB",(300,216),style.get("background") or NOMAD_BG).save(bg)
        singer_map,singers,lyric_text=_singer_plan(tl,SettingsSinger)
        lyric=SettingsLyric(
            sync=sync,end_sync=ends,text=lyric_text,
            line_tile_height=int(layout["line_tile_height"]),lines_per_page=int(layout["lines_per_screen"]),
            singer=1,row=int(layout["row"]),explicit_timeline=False,
        )
        opening=tl.get("opening") or {}; ending=tl.get("ending") or {}
        cfg=Settings(
            title=title,artist=artist,file=audio,font=Path(layout["font_path_server"]),
            title_screen_background=bg,outro_background=bg,outname=outname,
            clear_mode=str(layout["clear_mode"]),sync_offset=0,
            highlight_bandwidth=int(options.get("highlight_bandwidth") or 4),
            draw_bandwidth=int(options.get("draw_bandwidth") or 1),
            background=style.get("background") or NOMAD_BG,border=style.get("border") or NOMAD_BG,
            font_size=int(layout["font_size"]),stroke_width=int(layout["stroke_width"]),stroke_type="octagon",
            instrumentals=_nomad_instrumentals(tl,SettingsInstrumental,bg),
            singers=singers,lyrics=[lyric],
            title_color=style.get("title_color") or "#FFFFFF",
            artist_color=style.get("artist_color") or PANEL_ACTIVE_DEFAULT,
            title_screen_transition=str(opening.get("transition") or "centertexttoplogobottomtext"),
            title_artist_gap=10,title_top_padding=0,
            intro_duration_seconds=float(opening.get("duration") or 0.0) if features.get("opening",True) else 0.0,
            first_syllable_buffer_seconds=0.0,
            outro_enabled=bool(features.get("ending",True)),
            outro_start_sync=int(round(float(ending.get("start") or tl["duration"])*100)),
            outro_transition=str(ending.get("transition") or "centertexttoplogobottomtext"),
            outro_text_line1=str(ending.get("line1") or "") if features.get("ending",True) else "",
            outro_text_line2=str(ending.get("line2") or "") if features.get("ending",True) else "",
            outro_line1_line2_gap=30,
            outro_line1_color=style.get("outro_line1_color") or "#FFFFFF",
            outro_line2_color=style.get("outro_line2_color") or PANEL_ACTIVE_DEFAULT,
        )
        kc=KaraokeComposer(cfg,relative_dir=td)

        flat_timeline_words=[w for line in tl["lines"] for w in line["words"]]
        flat_syllables=[(line,syll) for lyr in kc.lyrics for line in lyr.lines for syll in line.syllables]
        if len(flat_timeline_words)!=len(flat_syllables):
            raise EngineV2Error(f"V2 compile mismatch: {len(flat_timeline_words)} events vs {len(flat_syllables)} syllables")

        compiled=[]; synthetic=[]
        for tw,(line,syll) in zip(flat_timeline_words,flat_syllables):
            bb=syll.mask.getbbox() or (0,0,0,0)
            row={
                "word_id":tw["id"],"text":tw["text"],
                "timeline_start":float(tw["start"]),"timeline_end":float(tw["end"]),
                "cdg_start_frame":int(syll.start_offset),"cdg_end_frame":int(syll.end_offset),
                "cdg_start":round(float(syll.start_offset)/300.0,6),
                "cdg_end":round(float(syll.end_offset)/300.0,6),
                "bbox":[int(line.x+bb[0]),int(line.y+bb[1]),int(line.x+bb[2]),int(line.y+bb[3])],
                "line_index":int(syll.line_index),"syllable_index":int(syll.syllable_index),
                "singer":int(line.singer),
                "active_fill_index":int(line.singer)<<2|2,
                "synthetic":bool(tw.get("synthetic")),
            }
            (synthetic if tw.get("synthetic") else compiled).append(row)

        compile_diag={
            "engine":ENGINE_VERSION,"upstream_commit":UPSTREAM_COMMIT,
            "feature_signature":(tl.get("render_metadata") or {}).get("feature_signature"),
            "features":features,"clear_mode":layout["clear_mode"],"background":style.get("background") or NOMAD_BG,
            "intro_delay_expected_frames":0,"sync_offset_frames":int(kc.sync_offset),
            "opening":opening,"instrumentals":tl.get("instrumentals") or [],
            "lead_ins":tl.get("lead_ins") or [],"ending":ending,
            "compiled_words":compiled,"compiled_synthetic":synthetic,
        }
        kc.compose()
        compile_diag["intro_delay_actual_frames"]=int(getattr(kc,"intro_delay",0))
        compile_diag["writer_packet_count"]=int(getattr(kc.writer,"packets_queued",len(getattr(kc.writer,"packets",[]))))
        (output_dir/"diagnostic_v2.json").write_text(json.dumps(compile_diag,ensure_ascii=False,indent=2),encoding="utf-8")

        zp=td/f"{outname}.zip"
        if not zp.is_file():
            raise EngineV2Error("Nomad cdgmaker no produjo ZIP.")
        with zipfile.ZipFile(zp,"r") as zf:
            data=zf.read(f"{outname}.cdg")

    out=output_dir/"output_v2.cdg"; tmp=output_dir/"output_v2.tmp"; tmp.write_bytes(data); tmp.replace(out)
    return {
        "ok":True,"engine":ENGINE_VERSION,"timeline":tl,
        "timeline_path":str(output_dir/"timeline_v2.json"),"cdg_path":str(out),
        "cdg_size":out.stat().st_size,"diagnostic_path":str(output_dir/"diagnostic_v2.json"),
        "warnings":tl.get("warnings") or [],
    }

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
