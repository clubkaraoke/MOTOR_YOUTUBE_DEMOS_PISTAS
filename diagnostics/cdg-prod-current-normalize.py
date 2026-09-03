"""
Normalizador: convierte el JSON del editor en la configuración de cdgmaker.

Este es el ÚNICO módulo que conoce a la vez el vocabulario del editor
(palabras con tiempos) y el del motor CD+G (sílabas, filas, páginas,
centésimas de segundo). El editor nunca produce nada con forma de CDG y
cdgmaker nunca ve un archivo del editor.

Responsabilidades:
  1. Rellenar los finales implícitos  (fin = inicio de la siguiente, acotado)
  2. Aplicar la compensación de latencia
  3. Partir las líneas que no caben, con las métricas reales de la fuente
  4. Insertar la cuenta atrás antes de cada entrada larga
  5. Detectar los instrumentales
  6. Avisar de las palabras que no caben en el presupuesto de paquetes
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import ImageFont

# Geometría CD+G. 300x216 px, pero los bordes exteriores no se ven en
# la mayoría de reproductores: la zona útil es de 288x192.
# 280, no 288: es el valor que usa cdgmaker para avisar de "line too wide".
# Si cortamos a 288, las líneas de 281-288 px llegan al render y se salen.
CDG_VISIBLE_WIDTH = 280
CDG_TILE_HEIGHT = 12
CDG_ROWS = 18
CDG_FPS = 300  # paquetes por segundo — el muro duro


class NormalizeError(ValueError):
    pass


@dataclass
class Warning_:
    word_id: str
    text: str
    kind: str
    detail: str


@dataclass
class Normalized:
    """Todo lo que el renderer necesita, ya masticado."""
    title: str
    artist: str
    sync: list[int]                 # centésimas de segundo, una por sílaba
    syllable_modes: list[int]        # 0 default, 1 mujer, 2 hombre, 3 duo, 4 estático
    text: str                       # líneas visuales separadas por \n
    line_tile_height: int
    lines_per_page: int
    row: int
    instrumentals: list[dict]
    duration: float
    render_timeline: dict
    render_pages: dict
    warnings: list[Warning_] = field(default_factory=list)


# --------------------------------------------------------------------------
# carga y validación
# --------------------------------------------------------------------------

def load(path: str | Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("version") != 1:
        raise NormalizeError(f"Versión de proyecto desconocida: {doc.get('version')!r}")
    if not doc.get("segments"):
        raise NormalizeError("El proyecto no tiene ninguna línea de letra.")
    return doc


def check_complete(doc: dict) -> None:
    """Puerta temprana: ninguna palabra sin tiempo puede llegar al render.

    Sin esto, cdgmaker falla dentro de la composición con un error que no
    dice nada útil. Aquí falla nombrando la primera línea afectada.
    """
    missing = []
    for seg in doc["segments"]:
        for w in seg.get("words", []):
            if w.get("spoken"):
                continue
            if w.get("start_time") is None:
                missing.append((seg.get("text", ""), w["text"]))
    if missing:
        first_line, first_word = missing[0]
        raise NormalizeError(
            f"{len(missing)} palabra(s) sin marcar. La primera es «{first_word}» "
            f"en la línea «{first_line}». Marca la canción completa antes de generar el CDG."
        )


# --------------------------------------------------------------------------
# el trabajo
# --------------------------------------------------------------------------

def flatten(doc: dict) -> list[dict]:
    """Lista plana de palabras con su segmento de origen."""
    out = []
    for si, seg in enumerate(doc["segments"]):
        for w in seg.get("words", []):
            if w.get("start_time") is not None:
                out.append({**w, "_seg": si})
    return out


def fill_ends(words: list[dict], max_word: float) -> None:
    """Completa sólo END faltantes; los END editados en el panel son autoridad.

    Antes el renderer volvía a pegar cada palabra a la siguiente marca salvo
    que estuviera `locked`, borrando silencios reales. Ahora cualquier END
    válido del proyecto se conserva.
    """
    for i, w in enumerate(words):
        existing = w.get("end_time")
        if existing is not None and existing > w["start_time"]:
            continue
        cap = w["start_time"] + max_word
        nxt = words[i + 1]["start_time"] if i + 1 < len(words) else None
        w["end_time"] = min(nxt, cap) if nxt is not None else cap
        if w["end_time"] <= w["start_time"]:
            w["end_time"] = w["start_time"] + 0.05


def apply_calibration(words: list[dict], cal_ms: float) -> None:
    """Resta el retraso humano medido. Nunca por debajo de cero."""
    if not cal_ms:
        return
    d = cal_ms / 1000.0
    for w in words:
        w["start_time"] = max(0.0, w["start_time"] - d)
        if w.get("end_time") is not None:
            w["end_time"] = max(w["start_time"] + 0.05, w["end_time"] - d)


def resolve_font_path(style: dict) -> Path:
    raw = Path(str(style.get("font", r"C:/Windows/Fonts/impact.ttf")))
    if raw.is_file(): return raw
    fb = Path(str(style.get("font_fallback", r"C:/Windows/Fonts/arial.ttf")))
    if fb.is_file(): return fb
    for candidate in (Path(r"C:/Windows/Fonts/arialbd.ttf"), Path(r"C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf")):
        if candidate.is_file(): return candidate
    raise NormalizeError("No encuentro Impact ni una fuente alternativa del sistema.")


def center_last_page(visual: list[list[dict]], lines_per_page: int) -> list[list[dict]]:
    lpp=max(2,min(8,int(lines_per_page)))
    if not visual: return visual
    rem=len(visual)%lpp
    if rem==0: return visual
    start=len(visual)-rem; top=(lpp-rem)//2; bottom=lpp-rem-top
    return visual[:start]+([[]]*top)+visual[start:]+([[]]*bottom)


def text_width(s: str, font: ImageFont.FreeTypeFont) -> float:
    """Anchura como suma de avances por carácter, sin kerning.

    El editor mide igual, con una tabla de avances extraída de esta misma
    fuente. Usar `font.getlength(s)` aquí daría hasta un 5 % menos porque
    aplica kerning, y entonces el editor y el render partirían las líneas en
    sitios distintos. Sumar avances es ligeramente conservador —alguna línea
    se parte cuando habría cabido por un pelo— pero nunca se desborda y las
    dos vistas coinciden siempre.
    """
    return sum((22 if c == "§" else font.getlength(c)) for c in s)


def wrap_lines(doc: dict, font: ImageFont.FreeTypeFont, uppercase: bool) -> list[list[dict]]:
    """Parte cada línea de letra en tantas líneas visuales como haga falta."""
    visual: list[list[dict]] = []
    for seg in doc["segments"]:
        if seg.get("kind") == "break":
            if visual and visual[-1]:
                visual.append([])
            continue
        words = [w for w in seg.get("words", []) if not w.get("spoken")]
        if not words:
            continue
        cur: list[dict] = []
        for w in words:
            probe = cur + [w]
            text = " ".join((x["text"].upper() if uppercase else x["text"]) for x in probe)
            if cur and text_width(text, font) > CDG_VISIBLE_WIDTH:
                visual.append(cur)
                cur = [w]
            else:
                cur = probe
        if cur:
            visual.append(cur)
    while visual and not visual[-1]:
        visual.pop()
    return visual


def smart_page_breaks(visual: list[list[dict]], gap_seconds: float = 2.0) -> list[list[dict]]:
    out=[]; prev_line=None; explicit_break=False
    for line in visual:
        if not line:
            if out and out[-1]: out.append([])
            prev_line=None; explicit_break=True; continue
        if prev_line is not None and not explicit_break:
            prev_end=max(float(w.get("end_time") or w["start_time"]) for w in prev_line)
            start=float(line[0]["start_time"])
            if start-prev_end>=float(gap_seconds) and out and out[-1]: out.append([])
        out.append(line); prev_line=line; explicit_break=False
    while out and not out[-1]: out.pop()
    return out


def center_stanza_pages(visual: list[list[dict]], lines_per_page: int) -> list[list[dict]]:
    lpp=max(2,min(8,int(lines_per_page)))
    blocks=[]; block=[]
    for line in visual:
        if not line:
            if block:
                blocks.append(block); block=[]
            continue
        block.append(line)
    if block: blocks.append(block)
    out=[]
    for block in blocks:
        while out and len(out)%lpp: out.append([])
        for i in range(0,len(block),lpp):
            while out and len(out)%lpp: out.append([])
            chunk=block[i:i+lpp];top=(lpp-len(chunk))//2;bottom=lpp-len(chunk)-top
            out.extend([[] for _ in range(top)]);out.extend(chunk);out.extend([[] for _ in range(bottom)])
    while out and not out[-1]: out.pop()
    return out


# DJGABO_AUTHORITATIVE_PAGES_NORMALIZER_V1
def resolve_render_pages(doc: dict, style: dict, font: ImageFont.FreeTypeFont, uppercase: bool) -> tuple[list[list[dict]], dict]:
    """Reconstruye EXACTAMENTE las páginas que exportó la pestaña Karaoke.

    Si el proyecto es antiguo y todavía no trae render_pages, usamos el mismo
    criterio del preview: saltos explícitos + wrap por ancho + bloques de LPP.
    Ya no se crean páginas nuevas sólo porque exista una pausa temporal.
    """
    lpp=max(2,min(8,int(style.get("lines_per_page",6))))
    raw=doc.get("render_pages")
    by_id={
        str(w.get("id")):w
        for seg in doc.get("segments",[])
        for w in seg.get("words",[])
        if w.get("id") is not None
    }

    if isinstance(raw,dict) and isinstance(raw.get("pages"),list):
        if str(raw.get("version") or "") != "CDG_RENDER_PAGES_V2":
            raise NormalizeError(
                "Este proyecto conserva paginado V1. Recarga el editor (Ctrl+F5) "
                "y vuelve a Crear CDG para regenerar las páginas V2."
            )
        declared=int(raw.get("lines_per_page") or lpp)
        if declared!=lpp:
            raise NormalizeError(
                f"render_pages fue creado con {declared} líneas/página pero el render pide {lpp}."
            )
        visual=[]
        seen=[]
        for pi,page in enumerate(raw["pages"]):
            lines=page.get("lines") if isinstance(page,dict) else None
            if not isinstance(lines,list) or len(lines)!=lpp:
                raise NormalizeError(f"render_pages página {pi+1}: esperaba exactamente {lpp} slots.")
            for li,spec in enumerate(lines):
                ids=(spec or {}).get("word_ids") if isinstance(spec,dict) else None
                ids=[] if ids is None else ids
                if not isinstance(ids,list):
                    raise NormalizeError(f"render_pages página {pi+1} línea {li+1}: word_ids inválido.")
                line=[]
                for wid in ids:
                    w=by_id.get(str(wid))
                    if w is None:
                        raise NormalizeError(f"render_pages referencia una palabra inexistente: {wid}.")
                    if w.get("spoken"):
                        raise NormalizeError(f"render_pages incluyó HABLADO en una línea cantada: {wid}.")
                    if w.get("start_time") is None:
                        raise NormalizeError(f"render_pages incluyó una palabra sin timing: {wid}.")
                    line.append(w); seen.append(str(wid))
                visual.append(line)

        expected=[
            str(w.get("id"))
            for seg in doc.get("segments",[])
            for w in seg.get("words",[])
            if not w.get("spoken") and w.get("start_time") is not None
        ]
        if seen!=expected:
            # El orden también es autoridad: no basta con que estén las mismas palabras.
            raise NormalizeError(
                "render_pages no coincide con el orden actual de las palabras. "
                "Vuelve a abrir/guardar el proyecto para regenerar las páginas."
            )
        meta=dict(raw)
        meta["source"]="KARAOKE_PREVIEW"
        meta["clear_mode"]="page"
        meta["instrumental_boundaries_locked"]=True
        meta["authoritative"]=True
        return visual,meta

    visual=wrap_lines(doc,font,uppercase)
    visual=center_stanza_pages(visual,lpp)
    visual=center_last_page(visual,lpp)
    pages=[]
    for pi in range(0,len(visual),lpp):
        chunk=visual[pi:pi+lpp]
        if len(chunk)<lpp: chunk=chunk+[[] for _ in range(lpp-len(chunk))]
        pages.append({
            "page":len(pages)+1,
            "lines":[
                {"slot":slot+1,
                 "word_ids":[str(w.get("id")) for w in line],
                 "text":" ".join((w["text"].upper() if uppercase else w["text"]) for w in line)}
                for slot,line in enumerate(chunk)
            ],
        })
    return visual,{
        "version":"CDG_RENDER_PAGES_V2",
        "source":"NORMALIZER_FALLBACK_PREVIEW_COMPAT",
        "lines_per_page":lpp,
        "clear_mode":"page",
        "instrumental_boundaries_locked":False,
        "authoritative":False,
        "pages":pages,
    }


def wipe_spans(visual: list[list[dict]], tail: float = 0.45) -> None:
    """Anota la duración REAL del barrido de cada palabra, la que usa cdgmaker.

    Descubierto leyendo el compositor: nuestro `end_time` no llega al render.
    cdgmaker toma el fin de una sílaba del siguiente punto de sincronización,
    y la última de cada línea visual dura `tail` segundos o hasta que empieza
    la línea siguiente, lo que ocurra antes.

    Nuestro `end_time` sigue siendo útil —el editor y el preview lo enseñan, y
    de él salen los instrumentales— pero para juzgar si un barrido cabe en el
    presupuesto de paquetes hay que usar este otro número.
    """
    flat = [(li, wi) for li, line in enumerate(visual) for wi in range(len(line))]
    for k, (li, wi) in enumerate(flat):
        w = visual[li][wi]
        explicit_end = w.get("end_time")
        if explicit_end is not None and explicit_end > w["start_time"]:
            end = explicit_end
        elif wi + 1 < len(visual[li]):
            end = visual[li][wi + 1]["start_time"]
        else:
            end = w["start_time"] + tail
            nxt = next((visual[l][i]["start_time"] for l, i in flat[k + 1:]), None)
            if nxt is not None:
                end = min(end, nxt)
        w["_wipe"] = max(0.01, end - w["start_time"])


# DJGABO_INSTRUMENTAL_PAGE_BOUNDARY_NORMALIZER_V2
def build_instrumentals(visual: list[list[dict]], style: dict,
                        spoken_intervals: list[tuple[float, float]] | None = None,
                        voice_gaps: list[tuple[float, float]] | None = None) -> list[list[dict]]:
    """Inserta cada INSTRUMENTAL como página completa SIN tocar slots de letra.

    V1 recorría línea por línea. Si una página cantada empezaba con un slot
    vacío por centrado, copiaba ese vacío y recién después insertaba la página
    instrumental. Eso corría las líneas y mezclaba dos páginas.
    """
    label = style.get("instrumental_label", "INSTRUMENTAL")
    dot = "§"
    n_dots = int(style.get("instrumental_dots", 4))
    span = float(style.get("instrumental_span_seconds", 6.0))
    lead = float(style.get("instrumental_lead_seconds", 4.0))
    min_gap = float(style.get("instrumental_min_gap", 6.0))
    spoken_min = float(style.get("spoken_instrumental_min_seconds", 6.0))
    spoken_lead = float(style.get("spoken_instrumental_lead_seconds", 4.0))
    spoken_join = float(style.get("spoken_block_join_seconds", 0.75))
    lpp = max(2, min(8, int(style.get("lines_per_page", 8))))
    spoken_intervals = spoken_intervals or []
    voice_gaps = voice_gaps or []

    merged_spoken: list[list[float]] = []
    for sa, sb in sorted(spoken_intervals):
        if not merged_spoken or sa - merged_spoken[-1][1] > spoken_join:
            merged_spoken.append([sa, sb])
        else:
            merged_spoken[-1][1] = max(merged_spoken[-1][1], sb)

    def decision(base: float, start: float):
        gap=start-base
        overlaps=[
            (max(sa,base),min(sb,start))
            for sa,sb in merged_spoken
            if sa<start and sb>base
        ]
        overlaps=[(a,b) for a,b in overlaps if b>a]
        voice_overlaps=[
            (max(va,base),min(vb,start))
            for va,vb in voice_gaps
            if va<start and vb>base
        ]
        voice_overlaps=[(a,b) for a,b in voice_overlaps if b>a]
        has_spoken=bool(overlaps)
        has_untranscribed_voice=bool(voice_overlaps)
        long_spoken=has_spoken and gap>=spoken_min
        regular_gap=gap>=min_gap and not has_spoken and not has_untranscribed_voice
        return long_spoken,regular_gap,gap

    def make_instrumental(base: float, start: float, n: int):
        long_spoken,regular_gap,_=decision(base,start)
        if not (label and n_dots>0 and (long_spoken or regular_gap)):
            return None
        use_lead=spoken_lead if long_spoken else lead
        label_slot=.55
        avail=(start-use_lead)-(base+.4)
        use_label=avail>=1.0+label_slot
        use_span=min(span,avail-(label_slot if use_label else 0.0))
        min_span=.6 if long_spoken else 1.0
        if use_span<min_span:
            return None

        dots_end=start-use_lead
        step=use_span/n_dots
        dots_start=dots_end-use_span
        spacer_at=base+.3
        label_at=dots_start-label_slot

        page=[]
        dot_row=lpp//2
        top=max(0,dot_row-1)
        bottom=max(0,lpp-2-top)
        page.extend([[] for _ in range(top)])
        if use_label and label_at>spacer_at+.15:
            page.append([
                {"id":f"in{n}s","text":"_","_inst":True,"_silent":True,
                 "_label":True,"start_time":spacer_at,"end_time":label_at},
                {"id":f"in{n}","text":label,"_inst":True,"_label":True,
                 "start_time":label_at,"end_time":dots_start-.05},
            ])
        else:
            page.append([{
                "id":f"in{n}","text":label,"_inst":True,"_label":True,
                "start_time":spacer_at,
                "end_time":max(spacer_at+.4,dots_start-.05),
            }])
        page.append([
            {"id":f"dot{n}_{i}","text":dot,"_inst":True,"_dotline":True,
             "start_time":dots_start+i*step,
             "end_time":dots_start+(i+1)*step}
            for i in range(n_dots)
        ])
        page.extend([[] for _ in range(bottom)])
        if len(page)!=lpp:
            raise NormalizeError(f"Página instrumental inválida: {len(page)} slots, esperaba {lpp}.")
        return page

    out:list[list[dict]]=[]
    prev_end:float|None=None
    inst_n=0

    for pos in range(0,len(visual),lpp):
        page=list(visual[pos:pos+lpp])
        if len(page)<lpp:
            page += [[] for _ in range(lpp-len(page))]
        content=[line for line in page if line]
        if not content:
            out.extend(page)
            continue

        first=content[0]
        first_start=float(first[0]["start_time"])
        base=(max(0.0,float(style.get("intro_duration_seconds",0.0) or 0.0)+.25)
              if prev_end is None else prev_end)

        inst_page=make_instrumental(base,first_start,inst_n)
        if inst_page is not None:
            out.extend(inst_page)
            inst_n+=1

        scan_end=max(w["end_time"] for w in first)
        for line in content[1:]:
            st=float(line[0]["start_time"])
            long_spoken,regular_gap,_=decision(scan_end,st)
            if long_spoken or regular_gap:
                raise NormalizeError(
                    "Paginado V1 detectado: hay un INSTRUMENTAL dentro de una "
                    "página de letra. Recarga el editor (Ctrl+F5) para generar "
                    "CDG_RENDER_PAGES_V2 antes de renderizar."
                )
            scan_end=max(w["end_time"] for w in line)

        out.extend(page)
        prev_end=max(w["end_time"] for w in content[-1])

    return out


def check_packet_budget(visual: list[list[dict]], font: ImageFont.FreeTypeFont,
                        line_tile_height: int, highlight_bw: int, draw_bw: int,
                        uppercase: bool) -> list[Warning_]:
    """Predice qué palabras no caben en los 300 paquetes/s.

    cdgmaker avisa de esto durante la composición, pero hacerlo aquí permite
    devolverle a la operadora una lista de word_id sobre los que puede hacer
    clic, antes de esperar al render completo.
    """
    warns: list[Warning_] = []
    group = (draw_bw + highlight_bw) * line_tile_height   # frames por grupo de columnas
    for line in visual:
        for w in line:
            if w.get("_inst"):
                continue
            text = w["text"].upper() if uppercase else w["text"]
            width_px = max(1, int(text_width(text, font)))
            frames = w.get("_wipe", w["end_time"] - w["start_time"]) * CDG_FPS
            columns = int(frames // group) * highlight_bw
            tiles_wide = math.ceil(width_px / 6)
            if columns < tiles_wide:
                warns.append(Warning_(
                    word_id=w.get("id", "?"), text=w["text"], kind="too_fast",
                    detail=(f"necesita {tiles_wide} pasos de barrido y sólo caben {max(columns,1)}; "
                            f"el CDG la barre en {int(w.get('_wipe', 0) * 1000)} ms"),
                ))
    return warns


# DJGABO_RENDER_TIMELINE_NORMALIZER_V1
def _first_real_voice(doc: dict) -> float | None:
    vals = [
        float(w["start_time"])
        for seg in doc.get("segments", [])
        for w in seg.get("words", [])
        if w.get("start_time") is not None
    ]
    return min(vals) if vals else None


def _first_sung_voice(doc: dict) -> float | None:
    vals = [
        float(w["start_time"])
        for seg in doc.get("segments", [])
        for w in seg.get("words", [])
        if not w.get("spoken") and w.get("start_time") is not None
    ]
    return min(vals) if vals else None


def _computed_render_timeline(doc: dict, style: dict) -> dict:
    first_real = _first_real_voice(doc)
    first_sung = _first_sung_voice(doc)
    normal = float(style.get("intro_duration_seconds", 6.0))
    short = float(style.get("intro_short_duration_seconds", 3.0))
    buffer_s = float(style.get("first_syllable_buffer_seconds", 3.0))
    short_needs = short + buffer_s
    normal_needs = normal + buffer_s

    if first_real is None:
        duration, rule = normal, "SIN_VOZ_REAL"
    elif first_real < short_needs:
        duration, rule = 0.0, "AUTO_SKIP_NO_CABE"
    elif first_real < normal_needs:
        duration, rule = short, "AUTO_SHORT_FITS"
    else:
        duration, rule = normal, "AUTO_NORMAL_FITS"

    return {
        "version": "CDG_RENDER_TIMELINE_V1",
        "clock_origin_seconds": 0.0,
        "first_real_voice_seconds": first_real,
        "first_sung_vocal_seconds": first_sung,
        "opening": {
            "enabled": duration > 0,
            "render_screen": duration > 0,
            "start_seconds": 0.0,
            "duration_seconds": duration,
            "end_seconds": duration,
            "rule": rule,
            "first_syllable_buffer_seconds": buffer_s,
        },
        "policy": {
            "json_is_source_of_truth": False,
            "synthetic_events_affect_opening": False,
            "composer_intro_delay_seconds": 0.0,
            "preserve_original_audio_clock": True,
        },
    }


def resolve_render_timeline(doc: dict, style: dict) -> dict:
    """El JSON del editor manda. Sólo calculamos fallback para proyectos antiguos."""
    raw = doc.get("render_timeline")
    if not isinstance(raw, dict) or not isinstance(raw.get("opening"), dict):
        return _computed_render_timeline(doc, style)

    opening = dict(raw["opening"])
    try:
        duration = max(0.0, float(opening.get("duration_seconds", 0.0)))
    except (TypeError, ValueError):
        raise NormalizeError("render_timeline.opening.duration_seconds no es numérico.")

    first_real = raw.get("first_real_voice_seconds")
    if first_real is None:
        first_real = _first_real_voice(doc)
    else:
        first_real = float(first_real)
    first_sung = raw.get("first_sung_vocal_seconds")
    if first_sung is None:
        first_sung = _first_sung_voice(doc)
    else:
        first_sung = float(first_sung)

    buffer_s = float(opening.get(
        "first_syllable_buffer_seconds",
        style.get("first_syllable_buffer_seconds", 3.0),
    ))
    # Si el editor dice que hay opening, verificamos que realmente quepa antes
    # de la zona de preparación de la primera voz. No inventamos retrasos.
    if first_real is not None and duration > 0 and duration + buffer_s > first_real + 1e-6:
        raise NormalizeError(
            "El JSON de render pide un Opening que invade la primera voz real: "
            f"opening={duration:.3f}s + buffer={buffer_s:.3f}s > voz={first_real:.3f}s."
        )

    timeline = {
        "version": str(raw.get("version") or "CDG_RENDER_TIMELINE_V1"),
        "clock_origin_seconds": 0.0,
        "first_real_voice_seconds": first_real,
        "first_sung_vocal_seconds": first_sung,
        "opening": {
            "enabled": bool(opening.get("enabled", duration > 0)) and duration > 0,
            "render_screen": bool(opening.get("render_screen", duration > 0)) and duration > 0,
            "start_seconds": 0.0,
            "duration_seconds": duration,
            "end_seconds": duration,
            "rule": str(opening.get("rule") or "JSON_EXPLICITO"),
            "first_syllable_buffer_seconds": buffer_s,
        },
        "policy": {
            "json_is_source_of_truth": True,
            "synthetic_events_affect_opening": False,
            "composer_intro_delay_seconds": 0.0,
            "preserve_original_audio_clock": True,
        },
    }
    return timeline


def decide_intro(doc: dict, style: dict) -> float:
    """Compatibilidad: devuelve la decisión final, ya resuelta por el JSON."""
    return float(resolve_render_timeline(doc, style)["opening"]["duration_seconds"])


def normalize(doc: dict, style: dict) -> Normalized:
    check_complete(doc)
    style = dict(style)
    render_timeline = resolve_render_timeline(doc, style)
    style["intro_duration_seconds"] = float(render_timeline["opening"]["duration_seconds"])

    font_path = resolve_font_path(style)
    font = ImageFont.truetype(str(font_path), style["font_size"])
    upper = bool(style.get("uppercase", True))

    words = flatten(doc)
    words.sort(key=lambda w: w["start_time"])
    apply_calibration(words, doc.get("calibration_ms", 0))
    fill_ends(words, style["max_word_seconds"])

    # reescribir los tiempos ya normalizados en el documento
    by_id = {w["id"]: w for w in words}
    for seg in doc["segments"]:
        for w in seg.get("words", []):
            src = by_id.get(w["id"])
            if src:
                w["start_time"], w["end_time"] = src["start_time"], src["end_time"]

    spoken_intervals = []
    for seg in doc["segments"]:
        for w in seg.get("words", []):
            if w.get("spoken") and w.get("start_time") is not None:
                st = float(w["start_time"])
                en = float(w.get("end_time") or (st + style["max_word_seconds"]))
                spoken_intervals.append((st, max(st + 0.05, en)))

    voice_gaps = []
    for g in ((doc.get("ai") or {}).get("voice_gaps") or []):
        try:
            va, vb = float(g.get("start")), float(g.get("end"))
        except Exception:
            continue
        if vb > va:
            voice_gaps.append((va, vb))

    # La composición de páginas viene del preview/JSON. No se vuelve a
    # reagrupar por pausas de 2 s: esa era la causa de que Preview y CDG
    # mostraran distintas combinaciones de líneas.
    visual, render_pages = resolve_render_pages(doc, style, font, upper)
    visual = build_instrumentals(visual, style, spoken_intervals, voice_gaps)
    visual = center_last_page(visual, style["lines_per_page"])
    instrumentals: list[dict] = []

    wipe_spans(visual)
    warns = check_packet_budget(
        visual, font, style["line_tile_height"],
        style["highlight_bandwidth"], style["draw_bandwidth"], upper,
    )

    # texto y sync en el formato que espera cdgmaker:
    #   - una línea visual por \n, "~" para las líneas vacías
    #   - la lista de sync lleva exactamente un entero por sílaba, en orden
    text_lines, sync, modes = [], [], []
    for li, line in enumerate(visual):
        if not line:
            text_lines.append("~")
            continue
        parts = []
        # Cada palabra normal se separa mediante una sílaba muda `_`. Así el
        # siguiente punto de sync puede ser su END real, no necesariamente el
        # START de la siguiente palabra. El texto visible sigue siendo idéntico.
        if line[0].get("_inst"):
            for w in line:
                t = w["text"].upper() if upper else w["text"]
                parts.append(t.replace(" ", "_"))
                sync.append(int(round(w["start_time"] * 100)))
                modes.append(4 if w.get("_label") else 0)
            body = " ".join(parts)
        else:
            chain = []
            for wi, w in enumerate(line):
                t = (w["text"].upper() if upper else w["text"]).replace(" ", "_")
                chain.append(t)
                sync.append(int(round(w["start_time"] * 100)))
                role = w.get("vocal_role")
                mode = 1 if role == "female" else 2 if role == "male" else 3 if role == "duet" else 0
                modes.append(mode)
                end = w.get("end_time")
                # Un END explícito sólo puede convertirse en sílaba muda si no
                # invade el START siguiente. Si hay solapamiento, la UI ya lo
                # advierte; para "Generar igualmente" dejamos que el siguiente
                # START cierre el barrido en vez de crear sync desordenado.
                next_start = None
                if wi + 1 < len(line):
                    next_start = line[wi + 1]["start_time"]
                else:
                    for nl in visual[li + 1:]:
                        if nl:
                            next_start = nl[0]["start_time"]
                            break
                if (end is not None and end > w["start_time"] and
                    (next_start is None or end <= next_start)):
                    chain.append("_")
                    sync.append(int(round(end * 100)))
                    modes.append(mode)
            body = "/".join(chain)
        if line[0].get("_label"):
            body = "2|" + body
        elif line[0].get("_dotline"):
            body = "3|" + body
        # El rótulo del instrumental va con el cantante 2, cuyos colores activo
        # e inactivo son idénticos: así NO barre. Se dibuja, se queda quieto y
        # se borra. Es lo que pedía "no ejecutar sweep sobre INSTRUMENTAL", y
        # sale gratis: sigue habiendo un solo bloque [[lyrics]], de modo que el
        # presupuesto de paquetes no se reparte.
        text_lines.append(body)

    # Invariante barata que atrapa cualquier bloque mal colocado: cdgmaker
    # empareja sílabas consecutivas, así que un sync fuera de orden produce
    # barridos de duración negativa y avisos incomprensibles.
    for i in range(1, len(sync)):
        if sync[i] < sync[i - 1]:
            raise NormalizeError(
                f"Puntos de sincronización desordenados en la posición {i}: "
                f"{sync[i-1]} seguido de {sync[i]}. Es un fallo del normalizador, "
                f"no del proyecto."
            )

    rows_used = style["lines_per_page"] * style["line_tile_height"]
    row = max(1, (CDG_ROWS - rows_used) // 2 + int(style.get("lyric_y_offset", 0)))
    row = max(1, min(CDG_ROWS - rows_used, row))

    return Normalized(
        title=doc["song"].get("title", "Sin título"),
        artist=doc["song"].get("artist", ""),
        sync=sync,
        syllable_modes=modes,
        text="\n".join(text_lines),
        line_tile_height=style["line_tile_height"],
        lines_per_page=style["lines_per_page"],
        row=row,
        instrumentals=instrumentals,
        duration=doc["song"].get("duration", 0.0),
        render_timeline=render_timeline,
        render_pages=render_pages,
        warnings=warns,
    )


# --------------------------------------------------------------------------
# salida hacia cdgmaker
# --------------------------------------------------------------------------

def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_toml(n: Normalized, style: dict, audio: Path, outname: str, assets: Path) -> str:
    """cdgmaker se configura por TOML. Lo generamos aquí y no lo tocamos a mano."""
    intro_title = "" if style.get("intro_precomposed") else n.title
    intro_artist = "" if style.get("intro_precomposed") else n.artist
    outro1 = "" if style.get("outro_precomposed") else style["outro_line1"]
    outro2 = "" if style.get("outro_precomposed") else style["outro_line2"]
    L = [
        f"title = {_q(intro_title)}",
        f"artist = {_q(intro_artist)}",
        f"file = {_q(audio)}",
        f"outname = {_q(outname)}",
        f"font = {_q(resolve_font_path(style))}",
        f"font_size = {style['font_size']}",
        f"stroke_width = {style['stroke_width']}",
        f"stroke_type = {_q(style['stroke_type'])}",
        f"clear_mode = {_q(style['clear_mode'])}",
        f"sync_offset = {style['sync_offset']}",
        f"highlight_bandwidth = {style['highlight_bandwidth']}",
        f"draw_bandwidth = {style['draw_bandwidth']}",
        f"background = {_q(style['background'])}",
        f"border = {_q(style['border'])}",
        f"title_screen_background = {_q(assets / style['title_background'])}",
        f"outro_background = {_q(assets / style['outro_background'])}",
        f"title_color = {_q(style['title_color'])}",
        f"artist_color = {_q(style['artist_color'])}",
        f"title_screen_transition = {_q(style['title_transition'])}",
        f"outro_transition = {_q(style['outro_transition'])}",
        f"outro_text_line1 = {_q(outro1)}",
        f"outro_text_line2 = {_q(outro2)}",
        f"intro_duration_seconds = {style['intro_duration_seconds']}",
        # Separación entre título y artista, y desplazamiento vertical del
        # bloque. cdgmaker lo centra en la pantalla, así que con un artista
        # largo (dos líneas) el bloque baja y pisa la marca de la plantilla.
        # Estos dos números lo suben y lo aprietan.
        f"title_artist_gap = {style.get('title_artist_gap', 14)}",
        f"title_top_padding = {style.get('title_top_padding', -12)}",
        f"first_syllable_buffer_seconds = {style['first_syllable_buffer_seconds']}",
        "",
        "[[singers]]",
        f"inactive_fill = {_q(style['text_color'])}",
        f"inactive_stroke = {_q(style['stroke_color'])}",
        f"active_fill = {_q(style.get('highlight_default', style['highlight_color']))}",
        f"active_stroke = {_q(style['stroke_color'])}",
        "",
        "[[singers]]",
        f"inactive_fill = {_q(style['text_color'])}",
        f"inactive_stroke = {_q(style['stroke_color'])}",
        f"active_fill = {_q(style.get('highlight_female', '#FF4FA3'))}",
        f"active_stroke = {_q(style['stroke_color'])}",
        "",
        "[[singers]]",
        f"inactive_fill = {_q(style['text_color'])}",
        f"inactive_stroke = {_q(style['stroke_color'])}",
        f"active_fill = {_q(style.get('highlight_male', '#32B7FF'))}",
        f"active_stroke = {_q(style['stroke_color'])}",
        "",
        "[[singers]]",
        f"inactive_fill = {_q(style['text_color'])}",
        f"inactive_stroke = {_q(style['stroke_color'])}",
        f"active_fill = {_q(style.get('highlight_duet', '#7ED957'))}",
        f"active_stroke = {_q(style['stroke_color'])}",
        "",
    ]
    for inst in n.instrumentals:
        L += [
            "[[instrumentals]]",
            f"sync = {inst['sync']}",
            f"wait = {str(inst['wait']).lower()}",
            f"text = {_q(inst['text'])}",
            f"line_tile_height = {inst['line_tile_height']}",
            f"text_placement = {_q('middle')}",
            f"fill = {_q(style['text_color'])}",
        ]
        if inst.get("image"):
            ip = Path(inst["image"])
            if not ip.is_absolute():
                ip = Path(__file__).parent / ip
            L.append(f"image = {_q(ip)}")
        L.append("")
    L += [
        "[[lyrics]]",
        "singer = 1",
        f"row = {n.row}",
        f"line_tile_height = {n.line_tile_height}",
        f"lines_per_page = {n.lines_per_page}",
        f"sync = [{', '.join(str(s) for s in n.sync)}]",
        f"syllable_modes = [{', '.join(str(m) for m in n.syllable_modes)}]",
        # sin salto final: TOML conservaría una línea vacía de más y cdgmaker
        # la contaría como una línea de letra fantasma
        'text = """',
        n.text + '\\',
        '"""',
        "",
    ]
    return "\n".join(L)
