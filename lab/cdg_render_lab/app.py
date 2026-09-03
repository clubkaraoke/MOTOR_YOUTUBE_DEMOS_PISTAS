from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import qrcode
import requests
from flask import Flask, Response, abort, jsonify, request, send_file

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("CDG_RENDER_LAB_DATA", "/var/lib/djgabo-cdg-render-lab"))
SESSIONS = DATA / "sessions"
RENDERER = Path(os.getenv("CDG_RENDER_LAB_RENDERER", str(ROOT.parent / "renderer")))
PUBLIC_BASE = os.getenv("CDG_RENDER_LAB_PUBLIC_BASE", "https://panel.kitkaraoke.com/cdg-render-lab").rstrip("/")
SCRIBE_URL = os.getenv("CDG_RENDER_LAB_SCRIBE_URL", "http://127.0.0.1:8097/api/elevenlabs/transcribe")
MAX_UPLOAD = int(os.getenv("CDG_RENDER_LAB_MAX_UPLOAD", str(800 * 1024 * 1024)))
ALLOWED_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}

DATA.mkdir(parents=True, exist_ok=True)
SESSIONS.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD


def _safe_sid(raw: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,80}", raw or ""):
        abort(404)
    return raw


def _dir(sid: str) -> Path:
    sid = _safe_sid(sid)
    p = SESSIONS / sid
    p.mkdir(parents=True, exist_ok=True)
    return p


def _meta_path(sid: str) -> Path:
    return _dir(sid) / "meta.json"


def _load_meta(sid: str) -> dict:
    p = _meta_path(sid)
    if not p.is_file():
        abort(404)
    return json.loads(p.read_text(encoding="utf-8"))


def _save_meta(sid: str, meta: dict) -> None:
    p = _meta_path(sid)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _project_path(sid: str) -> Path:
    return _dir(sid) / "project.json"


def _audio_path(sid: str, meta: dict | None = None) -> Path | None:
    meta = meta or _load_meta(sid)
    name = str(meta.get("audio_name") or "")
    if not name:
        return None
    p = _dir(sid) / name
    return p if p.is_file() else None


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    out = subprocess.check_output(cmd, text=True, timeout=60).strip()
    return round(float(out), 6)


def _clean_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip(" .")
    return stem[:180] or "audio.wav"


def _save_upload(sid: str, fileobj) -> dict:
    meta = _load_meta(sid)
    original = _clean_filename(fileobj.filename or "audio.wav")
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Formato no soportado. Usa WAV, MP3, M4A, AAC, OGG o FLAC.")
    d = _dir(sid)
    for p in d.iterdir():
        if p.is_file() and p.name not in {"meta.json", "project.json", "scribe_raw.json"}:
            if p.suffix.lower() in ALLOWED_EXT:
                p.unlink(missing_ok=True)
    dest = d / ("audio" + ext)
    fileobj.save(dest)
    if not dest.is_file() or dest.stat().st_size < 100:
        raise ValueError("El audio llegó vacío.")
    duration = _ffprobe_duration(dest)
    meta.update({
        "audio_name": dest.name,
        "audio_original_name": original,
        "audio_size": dest.stat().st_size,
        "duration": duration,
        "audio_uploaded_at": time.time(),
        "scribe_status": "pending",
        "render_status": "pending",
    })
    _save_meta(sid, meta)
    return meta


def _visual_units(text: str) -> float:
    total = 0.0
    for ch in str(text or "").upper():
        if ch in "MWÁÉÓÚQG":
            total += 1.20
        elif ch in "IÍLJT1.,;:!¡?¿":
            total += 0.58
        elif ch == " ":
            total += 0.55
        else:
            total += 1.0
    return total


def _balance_phrase(tokens: list[dict], max_units: float = 29.0) -> list[list[dict]]:
    toks = [dict(t) for t in tokens if str(t.get("text") or "").strip()]
    if not toks:
        return []
    widths = [_visual_units(t.get("text")) for t in toks]
    spaces = .55
    total = sum(widths) + spaces * max(0, len(toks) - 1)
    n_lines = min(max(1, int(math.ceil(total / max_units))), len(toks))
    prefix = [0.0]
    for i, w in enumerate(widths):
        prefix.append(prefix[-1] + w + (spaces if i else 0.0))
    target = total / n_lines
    inf = 10**18
    dp = [[inf] * (len(toks) + 1) for _ in range(n_lines + 1)]
    back = [[None] * (len(toks) + 1) for _ in range(n_lines + 1)]
    dp[0][0] = 0.0

    def span(i: int, j: int) -> float:
        val = prefix[j] - prefix[i]
        if i > 0:
            val -= spaces
        return val

    for ln in range(1, n_lines + 1):
        for j in range(ln, len(toks) + 1):
            for i in range(ln - 1, j):
                w = span(i, j)
                if w > max_units * 1.12:
                    continue
                count = j - i
                penalty = (w - target) ** 2
                if count == 1 and len(toks) > 2:
                    penalty += 28.0
                last = str(toks[j - 1].get("text") or "")
                if re.search(r"[,;:]$", last):
                    penalty -= 2.5
                if re.search(r"[.!?…][\"”’']?$", last):
                    penalty -= 4.0
                score = dp[ln - 1][i] + penalty
                if score < dp[ln][j]:
                    dp[ln][j] = score
                    back[ln][j] = i
    if not math.isfinite(dp[n_lines][len(toks)]) or dp[n_lines][len(toks)] >= inf / 2:
        out, cur = [], []
        for t in toks:
            probe = cur + [t]
            txt = " ".join(str(x.get("text") or "").strip() for x in probe)
            if cur and _visual_units(txt) > max_units:
                out.append(cur)
                cur = [t]
            else:
                cur = probe
        if cur:
            out.append(cur)
        return out
    cuts, j = [], len(toks)
    for ln in range(n_lines, 0, -1):
        i = back[ln][j]
        if i is None:
            return [toks]
        cuts.append((i, j))
        j = i
    cuts.reverse()
    return [toks[i:j] for i, j in cuts]


def _segment_scribe_words(items: list[dict]) -> list[tuple[str, list[dict]]]:
    clean = [dict(w) for w in (items or []) if str(w.get("text") or "").strip()]
    if not clean:
        return []
    groups, phrase = [], []
    phrase_gap = .90
    page_gap = 2.0

    def flush(page_break: bool = False):
        nonlocal phrase
        if phrase:
            for line in _balance_phrase(phrase):
                groups.append(("line", line))
            phrase = []
        if page_break and groups and groups[-1][0] != "break":
            groups.append(("break", []))

    for idx, item in enumerate(clean):
        phrase.append(item)
        txt = str(item.get("text") or "").strip()
        nxt = clean[idx + 1] if idx + 1 < len(clean) else None
        gap = 0.0
        if nxt is not None:
            try:
                gap = max(0.0, float(nxt.get("start")) - float(item.get("end")))
            except Exception:
                gap = 0.0
        strong = bool(re.search(r"[.!?…][\"”’']?$", txt))
        if nxt is None or strong or gap >= phrase_gap:
            flush(page_break=(gap >= page_gap))
    flush(False)
    while groups and groups[-1][0] == "break":
        groups.pop()
    return groups


def _project_from_scribe(meta: dict, words: list[dict], raw: dict) -> dict:
    segments = []
    wi = 0
    si = 0
    for kind, tokens in _segment_scribe_words(words):
        if kind == "break":
            if segments and segments[-1].get("kind") != "break":
                segments.append({"id": f"s{si:04d}", "kind": "break", "text": "", "words": []})
                si += 1
            continue
        line_words = []
        for item in tokens:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            a, b = item.get("start"), item.get("end")
            line_words.append({
                "id": f"w{wi:04d}",
                "text": text,
                "start_time": round(float(a), 6) if a is not None else None,
                "end_time": round(float(b), 6) if b is not None else None,
                "spoken": False,
                "locked": False,
                "vocal_role": None,
                "ai_confidence": float(item.get("confidence") or 0),
                "ai_status": str(item.get("qa_status") or "green"),
                "ai_match_type": str(item.get("match_type") or "scribe_raw"),
            })
            wi += 1
        if line_words:
            segments.append({
                "id": f"s{si:04d}",
                "kind": "lyric",
                "text": " ".join(w["text"] for w in line_words),
                "words": line_words,
            })
            si += 1
    while segments and segments[-1].get("kind") == "break":
        segments.pop()
    return {
        "version": 1,
        "lab_version": "CDG_RENDER_LAB_V1",
        "song": {
            "artist": str(meta.get("artist") or ""),
            "title": str(meta.get("title") or ""),
            "audio_file": str(meta.get("audio_original_name") or meta.get("audio_name") or ""),
            "duration": float(meta.get("duration") or 0),
        },
        "calibration_ms": 0,
        "segments": segments,
        "ai": {
            "engine": "elevenlabs-scribe-v2",
            "source_mode": "scribe_only",
            "format_version": 3,
            "line_mode": "balanced_visual_v3",
            "scribe_word_count": len(words),
            "raw_text": str((raw.get("scribe") or {}).get("text") or raw.get("text") or ""),
            "generated_at": time.time(),
        },
        "lab_settings": {
            "lines_per_screen": 6,
            "mode": "page",
            "read_ahead_seconds": 3.0,
            "safe_clear_gap_seconds": 4.0,
        },
    }


def _line_specs(project: dict) -> list[dict]:
    out = []
    for seg in project.get("segments") or []:
        if seg.get("kind") == "break":
            out.append({"break": True})
            continue
        words = [w for w in (seg.get("words") or []) if not w.get("spoken") and w.get("start_time") is not None]
        if not words:
            continue
        out.append({
            "break": False,
            "word_ids": [str(w.get("id")) for w in words],
            "text": " ".join(str(w.get("text") or "") for w in words),
            "sweep_start": min(float(w["start_time"]) for w in words),
            "sweep_end": max(float(w.get("end_time") or w["start_time"]) for w in words),
        })
    return out


def _pages_from_lines(project: dict, lpp: int) -> tuple[list[dict], list[dict]]:
    raw = _line_specs(project)
    pages = []
    chronological = []
    cur = []

    def flush():
        nonlocal cur
        if not cur:
            return
        while len(cur) < lpp:
            cur.append(None)
        page_no = len(pages) + 1
        specs = []
        for slot, line in enumerate(cur[:lpp], 1):
            if line is None:
                specs.append({"slot": slot, "word_ids": [], "text": ""})
            else:
                item = dict(line)
                item["page"] = page_no
                item["slot"] = slot
                chronological.append(item)
                specs.append({"slot": slot, "word_ids": item["word_ids"], "text": item["text"]})
        pages.append({"page": page_no, "lines": specs})
        cur = []

    for item in raw:
        if item.get("break"):
            flush()
            continue
        cur.append(item)
        if len(cur) >= lpp:
            flush()
    flush()
    return pages, chronological


def _build_render_authority(project: dict, settings: dict) -> tuple[dict, dict, dict]:
    lpp = max(2, min(8, int(settings.get("lines_per_screen") or 6)))
    mode = str(settings.get("mode") or "page").lower()
    if mode not in {"page", "overwrite"}:
        mode = "page"
    lead = max(0.0, min(12.0, float(settings.get("read_ahead_seconds") or 3.0)))
    safe_gap = max(1.0, min(12.0, float(settings.get("safe_clear_gap_seconds") or 4.0)))
    pages, lines = _pages_from_lines(project, lpp)

    render_pages = {
        "version": "CDG_RENDER_PAGES_V2",
        "source": "CDG_RENDER_LAB",
        "lines_per_page": lpp,
        "clear_mode": "page" if mode == "page" else "delayed",
        "instrumental_boundaries_locked": False,
        "pages": pages,
    }

    events = []
    clears = []
    duration = float((project.get("song") or {}).get("duration") or 0)
    post_hold = .18

    if mode == "page":
        by_page = {}
        for line in lines:
            by_page.setdefault(line["page"], []).append(line)
        prev_last_end = None
        for page_no in sorted(by_page):
            block = by_page[page_no]
            first_start = block[0]["sweep_start"]
            last_end = max(x["sweep_end"] for x in block)
            if prev_last_end is None:
                display = max(0.0, first_start - lead)
            else:
                clear_at = max(prev_last_end + .08, first_start - lead)
                clears.append({"at": round(clear_at, 3), "reason": "PAGE_CHANGE", "page": page_no})
                display = clear_at + .35
            next_page = by_page.get(page_no + 1)
            remove = duration
            if next_page:
                next_first = next_page[0]["sweep_start"]
                remove = max(last_end + .08, next_first - lead)
            for line in block:
                events.append({
                    "line_id": "line:" + ":".join(line["word_ids"]),
                    "visual_index": len(events),
                    "page": page_no,
                    "slot": line["slot"],
                    "word_ids": line["word_ids"],
                    "text": line["text"],
                    "sweep_start": round(line["sweep_start"], 3),
                    "sweep_end": round(line["sweep_end"], 3),
                    "preferred_display_at": round(max(0.0, line["sweep_start"] - lead), 3),
                    "display_at": round(display, 3),
                    "remove_at": round(max(remove, line["sweep_end"] + post_hold), 3),
                    "read_ahead_seconds": round(max(0.0, line["sweep_start"] - display), 3),
                })
            prev_last_end = last_end
    else:
        last_by_slot = {}
        last_event_by_slot = {}
        for idx, line in enumerate(lines):
            slot = ((idx % lpp) + 1)
            display = max(0.0, line["sweep_start"] - lead)
            prev_end = last_by_slot.get(slot)
            if prev_end is not None:
                display = max(display, prev_end + post_hold)
                prev_ev = last_event_by_slot[slot]
                prev_ev["remove_at"] = round(display, 3)
            ev = {
                "line_id": "line:" + ":".join(line["word_ids"]),
                "visual_index": idx,
                "page": line["page"],
                "slot": slot,
                "word_ids": line["word_ids"],
                "text": line["text"],
                "sweep_start": round(line["sweep_start"], 3),
                "sweep_end": round(line["sweep_end"], 3),
                "preferred_display_at": round(max(0.0, line["sweep_start"] - lead), 3),
                "display_at": round(display, 3),
                "remove_at": round(duration, 3),
                "read_ahead_seconds": round(max(0.0, line["sweep_start"] - display), 3),
            }
            events.append(ev)
            last_by_slot[slot] = line["sweep_end"]
            last_event_by_slot[slot] = ev
        # Clears sólo en pausas largas y seguras.
        for a, b in zip(lines, lines[1:]):
            gap = b["sweep_start"] - a["sweep_end"]
            if gap >= safe_gap:
                clears.append({
                    "at": round(a["sweep_end"] + .20, 3),
                    "reason": "SAFE_GAP",
                    "gap_seconds": round(gap, 3),
                })

    render_plan = {
        "version": "CDG_RENDER_PLAN_V1",
        "source": "CDG_RENDER_LAB",
        "mode": "PAGE_BY_PAGE" if mode == "page" else "SMART_OVERWRITE",
        "lines_per_screen": lpp,
        "read_ahead_seconds": lead,
        "post_sweep_hold_seconds": post_hold,
        "safe_clear_gap_seconds": safe_gap,
        "policy": {
            "musical_word_timings_are_immutable": True,
            "preview_and_renderer_share_plan": True,
            "lab_only": True,
        },
        "clear_events": clears,
        "instrumental_intervals": [],
        "lines": events,
    }

    render_timeline = {
        "version": "CDG_RENDER_TIMELINE_V1",
        "clock_origin_seconds": 0.0,
        "first_real_voice_seconds": events[0]["sweep_start"] if events else None,
        "first_sung_vocal_seconds": events[0]["sweep_start"] if events else None,
        "opening": {
            "enabled": False,
            "render_screen": False,
            "start_seconds": 0.0,
            "duration_seconds": 0.0,
            "end_seconds": 0.0,
            "rule": "LAB_NO_OPENING",
            "first_syllable_buffer_seconds": 0.0,
        },
        "policy": {
            "json_is_source_of_truth": True,
            "synthetic_events_affect_opening": False,
            "composer_intro_delay_seconds": 0.0,
            "preserve_original_audio_clock": True,
        },
    }
    return render_pages, render_plan, render_timeline


@app.get("/")
def index():
    return send_file(ROOT / "index.html")


@app.get("/healthz")
def healthz():
    return jsonify(
        ok=True,
        service="cdg-render-lab",
        version="1",
        data=str(DATA),
        renderer=RENDERER.is_dir(),
        scribe_url=SCRIBE_URL,
    )


@app.post("/api/session/new")
def new_session():
    sid = secrets.token_urlsafe(18)
    token = secrets.token_urlsafe(24)
    meta = {
        "sid": sid,
        "upload_token": token,
        "created_at": time.time(),
        "artist": "",
        "title": "",
        "audio_name": "",
        "scribe_status": "idle",
        "render_status": "idle",
    }
    _save_meta(sid, meta)
    return jsonify(
        ok=True,
        sid=sid,
        qr_url=f"api/qr/{sid}.png",
        phone_url=f"{PUBLIC_BASE}/upload/{sid}?t={token}",
    )


@app.get("/api/session/<sid>")
def session_status(sid):
    meta = _load_meta(sid)
    safe = {k: v for k, v in meta.items() if k != "upload_token"}
    safe["has_project"] = _project_path(sid).is_file()
    safe["audio_url"] = f"media/{sid}" if _audio_path(sid, meta) else None
    return jsonify(ok=True, session=safe)


@app.post("/api/session/<sid>/meta")
def session_meta(sid):
    meta = _load_meta(sid)
    data = request.get_json(silent=True) or {}
    meta["artist"] = str(data.get("artist") or "")[:160]
    meta["title"] = str(data.get("title") or "")[:200]
    _save_meta(sid, meta)
    return jsonify(ok=True)


@app.get("/api/qr/<sid>.png")
def qr_image(sid):
    meta = _load_meta(sid)
    url = f"{PUBLIC_BASE}/upload/{sid}?t={meta['upload_token']}"
    img = qrcode.make(url)
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "no-store"})


@app.get("/upload/<sid>")
def phone_upload(sid):
    meta = _load_meta(sid)
    token = request.args.get("t", "")
    if not secrets.compare_digest(token, str(meta.get("upload_token") or "")):
        abort(403)
    html = f"""<!doctype html>
<html lang="es"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Subir audio · CDG Render Lab</title>
<style>
body{{font-family:system-ui;background:#0d1016;color:#eef2f7;margin:0;padding:24px}}
.card{{max-width:520px;margin:40px auto;background:#171c25;border:1px solid #2a3342;border-radius:18px;padding:22px}}
button,input{{font:inherit}} input[type=file]{{width:100%;padding:16px 0}}
button{{width:100%;padding:15px;border:0;border-radius:12px;background:#f2a900;color:#111;font-weight:800}}
#st{{margin-top:14px;color:#b8c4d6;white-space:pre-wrap}}
</style>
<div class="card"><h2>CDG Render Lab</h2><p>Selecciona el audio. Se enviará únicamente al laboratorio aislado.</p>
<input id="f" type="file" accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac">
<button id="b">SUBIR AUDIO</button><div id="st"></div></div>
<script>
const sid={json.dumps(sid)},tok={json.dumps(token)};
b.onclick=async()=>{{if(!f.files[0])return st.textContent='Selecciona un audio.';
b.disabled=true;st.textContent='Subiendo…';const fd=new FormData();fd.append('audio',f.files[0]);
try{{const r=await fetch('../../api/qr-upload/'+sid+'/'+tok,{{method:'POST',body:fd}});const d=await r.json();
if(!r.ok||!d.ok)throw new Error(d.error||'Error');st.textContent='✓ Audio recibido. Ya puedes volver a la PC.';}}
catch(e){{st.textContent='Error: '+e.message;b.disabled=false;}}}}
</script></html>"""
    return Response(html, mimetype="text/html")


@app.post("/api/qr-upload/<sid>/<token>")
def qr_upload(sid, token):
    meta = _load_meta(sid)
    if not secrets.compare_digest(token, str(meta.get("upload_token") or "")):
        return jsonify(ok=False, error="QR vencido o inválido."), 403
    try:
        f = request.files.get("audio")
        if not f or not f.filename:
            raise ValueError("Falta el audio.")
        meta = _save_upload(sid, f)
        return jsonify(ok=True, duration=meta.get("duration"), name=meta.get("audio_original_name"))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.post("/api/upload/<sid>")
def direct_upload(sid):
    try:
        f = request.files.get("audio")
        if not f or not f.filename:
            raise ValueError("Falta el audio.")
        meta = _save_upload(sid, f)
        return jsonify(ok=True, duration=meta.get("duration"), name=meta.get("audio_original_name"))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.get("/media/<sid>")
def media(sid):
    meta = _load_meta(sid)
    p = _audio_path(sid, meta)
    if not p:
        abort(404)
    return send_file(p, conditional=True)


@app.post("/api/scribe/<sid>")
def scribe(sid):
    meta = _load_meta(sid)
    audio = _audio_path(sid, meta)
    if not audio:
        return jsonify(ok=False, error="Primero sube un audio."), 400
    data = request.get_json(silent=True) or {}
    meta["artist"] = str(data.get("artist") or meta.get("artist") or "")[:160]
    meta["title"] = str(data.get("title") or meta.get("title") or "")[:200]
    meta["scribe_status"] = "running"
    _save_meta(sid, meta)
    try:
        mime = "audio/wav" if audio.suffix.lower() == ".wav" else "audio/mpeg"
        with audio.open("rb") as fh:
            rr = requests.post(
                SCRIBE_URL,
                files={"audio": (meta.get("audio_original_name") or audio.name, fh, mime)},
                data={"lyrics": "", "language_code": "spa"},
                timeout=(30, 1200),
            )
        if not rr.ok:
            try:
                detail = rr.json().get("detail") or rr.text[:1000]
            except Exception:
                detail = rr.text[:1000]
            raise ValueError("ElevenLabs: " + str(detail))
        raw = rr.json()
        words = raw.get("words") or []
        if not words:
            raise ValueError("Scribe no devolvió palabras con tiempos.")
        project = _project_from_scribe(meta, words, raw)
        (_dir(sid) / "scribe_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        _project_path(sid).write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        meta["scribe_status"] = "done"
        meta["scribe_words"] = len(words)
        _save_meta(sid, meta)
        return jsonify(ok=True, words=len(words), project=project)
    except Exception as e:
        meta["scribe_status"] = "error"
        meta["scribe_error"] = str(e)
        _save_meta(sid, meta)
        return jsonify(ok=False, error=str(e)), 500


@app.get("/api/project/<sid>")
def get_project(sid):
    p = _project_path(sid)
    if not p.is_file():
        return jsonify(ok=False, error="Todavía no hay JSON."), 404
    return send_file(p, mimetype="application/json")


@app.get("/api/scribe-raw/<sid>")
def get_scribe_raw(sid):
    p = _dir(sid) / "scribe_raw.json"
    if not p.is_file():
        return jsonify(ok=False, error="Todavía no hay Scribe RAW."), 404
    return send_file(p, mimetype="application/json")


@app.post("/api/project/<sid>")
def save_project(sid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("version") != 1:
        return jsonify(ok=False, error="JSON de proyecto inválido."), 400
    _project_path(sid).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(ok=True)


@app.post("/api/plan/<sid>")
def plan(sid):
    p = _project_path(sid)
    if not p.is_file():
        return jsonify(ok=False, error="Primero sincroniza con ElevenLabs."), 400
    project = json.loads(p.read_text(encoding="utf-8"))
    settings = request.get_json(silent=True) or {}
    render_pages, render_plan, render_timeline = _build_render_authority(project, settings)
    project["lab_settings"] = {
        "lines_per_screen": render_plan["lines_per_screen"],
        "mode": "page" if render_plan["mode"] == "PAGE_BY_PAGE" else "overwrite",
        "read_ahead_seconds": render_plan["read_ahead_seconds"],
        "safe_clear_gap_seconds": render_plan["safe_clear_gap_seconds"],
    }
    project["cdg_settings"] = {
        "lines_per_page": render_plan["lines_per_screen"],
        "intro_mode": "never",
        "intro_duration_seconds": 0,
        "intro_short_duration_seconds": 0,
    }
    project["render_pages"] = render_pages
    project["render_plan"] = render_plan
    project["render_timeline"] = render_timeline
    _project_path(sid).write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(ok=True, project=project, render_plan=render_plan)


@app.post("/api/render/<sid>")
def render(sid):
    meta = _load_meta(sid)
    audio = _audio_path(sid, meta)
    project_file = _project_path(sid)
    if not audio or not project_file.is_file():
        return jsonify(ok=False, error="Falta audio o JSON."), 400
    settings = request.get_json(silent=True) or {}
    project = json.loads(project_file.read_text(encoding="utf-8"))
    render_pages, render_plan, render_timeline = _build_render_authority(project, settings)
    project["render_pages"] = render_pages
    project["render_plan"] = render_plan
    project["render_timeline"] = render_timeline
    project["cdg_settings"] = {
        "lines_per_page": render_plan["lines_per_screen"],
        "intro_mode": "never",
        "intro_duration_seconds": 0,
        "intro_short_duration_seconds": 0,
    }
    project_file.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

    if not (RENDERER / "render.py").is_file():
        return jsonify(ok=False, error="Renderer del laboratorio no instalado."), 500

    out = _dir(sid) / "render"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    style = json.loads((RENDERER / "style.json").read_text(encoding="utf-8"))
    style["lines_per_page"] = render_plan["lines_per_screen"]
    style["intro_duration_seconds"] = 0.0
    style["intro_short_duration_seconds"] = 0.0
    style_path = _dir(sid) / "style-run.json"
    style_path.write_text(json.dumps(style, ensure_ascii=False, indent=2), encoding="utf-8")

    meta["render_status"] = "running"
    _save_meta(sid, meta)
    cmd = [
        os.getenv("PYTHON", os.sys.executable),
        str(RENDERER / "render.py"),
        str(project_file), str(audio),
        "-o", str(out), "-s", str(style_path),
    ]
    proc = subprocess.run(cmd, cwd=str(RENDERER), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=1800)
    log = proc.stdout or ""
    (_dir(sid) / "render.log").write_text(log, encoding="utf-8")
    if proc.returncode != 0:
        meta["render_status"] = "error"
        meta["render_error"] = log[-2500:]
        _save_meta(sid, meta)
        return jsonify(ok=False, error="Renderer CDG: " + log[-1800:]), 500
    cdgs = sorted(out.glob("*.cdg"))
    if not cdgs:
        meta["render_status"] = "error"
        _save_meta(sid, meta)
        return jsonify(ok=False, error="El renderer terminó sin generar CDG."), 500
    cdg = cdgs[0]
    meta["render_status"] = "done"
    meta["render_file"] = cdg.name
    meta["render_size"] = cdg.stat().st_size
    _save_meta(sid, meta)
    return jsonify(
        ok=True,
        file=cdg.name,
        size=cdg.stat().st_size,
        download_url=f"download/{sid}/{cdg.name}",
        log_tail=log[-1600:],
        render_plan=render_plan,
    )


@app.get("/download/<sid>/<name>")
def download_render(sid, name):
    d = _dir(sid) / "render"
    p = d / Path(name).name
    if not p.is_file() or p.parent != d:
        abort(404)
    return send_file(p, as_attachment=True, download_name=p.name)


@app.get("/api/render-log/<sid>")
def render_log(sid):
    p = _dir(sid) / "render.log"
    if not p.is_file():
        return Response("", mimetype="text/plain")
    return send_file(p, mimetype="text/plain")


@app.errorhandler(413)
def too_large(_):
    return jsonify(ok=False, error="El audio supera el límite permitido del laboratorio."), 413
