#!/usr/bin/env python3
"""
Genera un paquete .cdg + .mp3 a partir del JSON del editor.

    python render.py cancion.timings.json cancion.mp3 -o salida/

Salidas en el directorio indicado:
    <slug>.cdg      gráficos CD+G
    <slug>.mp3      audio (recortado/desplazado por cdgmaker si hace falta)
    <slug>.zip      los dos juntos, listo para el reproductor
    <slug>.avisos.json   palabras que no caben en el presupuesto de paquetes

Con --preview añade además <slug>.mp4 para revisar sin abrir un reproductor.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))

import normalize as N  # noqa: E402


def slugify(s: str) -> str:
    """Nombre de archivo seguro: sólo ASCII.

    Muchos reproductores CD+G, sobre todo los de hardware, no leen nombres con
    tildes ni eñes: o no encuentran el archivo o muestran basura. Se convierten
    a su letra base en vez de borrarlas, para que «corazón» no acabe en
    «coraz».
    """
    s = unicodedata.normalize("NFKD", (s or "").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ñ", "n").replace("ç", "c")
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    return re.sub(r"[\s_-]+", "-", s).strip("-") or "cancion"


class WarningCatcher(logging.Handler):
    """cdgmaker informa de las sílabas imposibles por el logger.

    Las capturamos para devolvérselas a la interfaz en vez de dejarlas
    enterradas en la consola.
    """

    PATTERN = re.compile(
        r"Not enough time to highlight lyric (\d+) line (\d+) syllable (\d+).*?"
        r"Ideal duration is (-?\d+) column\(s\); actual duration is (-?\d+) column.*?"
        r"Syllable text: (.*)",
        re.S,
    )
    BRACED = re.compile(r"\{(.*?)\}", re.S)

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.items: list[dict] = []

    def emit(self, record):
        msg = record.getMessage()
        m = self.PATTERN.search(msg)
        if m:
            # La sílaba muda de los instrumentales (`_`) no dibuja nada, así que
            # el compositor la marca siempre. Es ruido: la filtramos para no
            # sacar una alarma falsa en cada canción con instrumental.
            braced = self.BRACED.search(m.group(6))
            if braced and not braced.group(1).strip():
                return
            self.items.append({
                "kind": "too_fast",
                "line": int(m.group(2)),
                "syllable": int(m.group(3)),
                "ideal_columns": int(m.group(4)),
                "actual_columns": int(m.group(5)),
                "text": (braced.group(1) if braced else "").strip(),
            })
        elif "too wide" in msg:
            self.items.append({"kind": "too_wide", "detail": msg.splitlines()[-1].strip()})



def _hex_rgb(value: str, default=(255, 255, 255)):
    try:
        v = str(value).lstrip("#")
        if len(v) == 6:
            return tuple(int(v[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        pass
    return default


def _wrap_font(text: str, font, max_width: int, max_lines: int):
    words = (text or "").split()
    if not words:
        return []
    probe = Image.new("RGB", (1, 1)); d = ImageDraw.Draw(probe); d.fontmode = "1"
    lines, cur = [], []
    for word in words:
        candidate = " ".join(cur + [word])
        bb = d.textbbox((0,0), candidate, font=font)
        if cur and (bb[2]-bb[0]) > max_width:
            lines.append(" ".join(cur)); cur=[word]
        else:
            cur.append(word)
    if cur: lines.append(" ".join(cur))
    return lines if len(lines) <= max_lines else None


def _draw_fit(draw, text: str, font_path: Path, box, max_size: int, min_size: int, max_lines: int, fill):
    x1,y1,x2,y2 = box; bw=x2-x1; bh=y2-y1
    chosen=None
    for size in range(max_size, min_size-1, -1):
        font=ImageFont.truetype(str(font_path), size)
        lines=_wrap_font(text, font, bw, max_lines)
        if not lines: continue
        bbs=[draw.textbbox((0,0), line, font=font) for line in lines]
        heights=[bb[3]-bb[1] for bb in bbs]
        total=sum(heights)+(len(lines)-1)*2
        if total <= bh and all(bb[2]-bb[0] <= bw for bb in bbs):
            chosen=(font,lines,bbs,heights,total); break
    if chosen is None:
        font=ImageFont.truetype(str(font_path), min_size)
        lines=_wrap_font(text, font, bw, max_lines) or [(text or "")[:28]]
        bbs=[draw.textbbox((0,0), line, font=font) for line in lines]
        heights=[bb[3]-bb[1] for bb in bbs]; total=sum(heights)+(len(lines)-1)*2
        chosen=(font,lines,bbs,heights,total)
    font,lines,bbs,heights,total=chosen
    y=y1+max(0,(bh-total)//2)
    for line,bb,h in zip(lines,bbs,heights):
        w=bb[2]-bb[0]
        x=x1+(bw-w)//2-bb[0]
        draw.text((x, y-bb[1]), line, font=font, fill=fill)
        y += h+2


def compose_intro_background(style: dict, title: str, artist: str, tmp: Path) -> Path:
    """Opening beta R17: negro sólido + título + respiración + Al estilo de: + artista."""
    img = Image.new("RGB", (300, 216), (0, 0, 0))
    draw = ImageDraw.Draw(img); draw.fontmode = "1"
    font_path = N.resolve_font_path(style)
    title_box = (8, 28, 292, 70)
    subtitle_box = (64, 86, 236, 104)
    artist_box = (16, 116, 284, 154)
    _draw_fit(draw, (title or "").upper(), font_path, title_box, 28, 15, 2, (255,255,255))
    _draw_fit(draw, "Al estilo de:", font_path, subtitle_box, 11, 10, 1, (255,255,255))
    _draw_fit(draw, artist or "", font_path, artist_box, 20, 14, 2, _hex_rgb(style.get("artist_color"), (242,183,5)))
    out = tmp / "intro_beta_negro.png"
    img.save(out)
    return out

def compose_outro_background(style: dict, tmp: Path) -> Path:
    raw = Path(str(style.get("outro_background", "assets/outro.png")))
    if not raw.is_absolute(): raw = HERE / raw
    img = Image.open(raw).convert("RGB").resize((300,216), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(img); draw.fontmode="1"
    font_path=N.resolve_font_path(style); size=int(style.get("outro_size",18)); xoff=int(style.get("outro_x",0)); yoff=int(style.get("outro_y",0))
    font=ImageFont.truetype(str(font_path), size)
    lines=[str(style.get("outro_line1","")),str(style.get("outro_line2",""))]
    ys=[92+yoff,122+yoff]
    fills=[_hex_rgb(style.get("text_color"),(255,255,255)),_hex_rgb(style.get("highlight_default"),(242,169,0))]
    for text,y,fill in zip(lines,ys,fills):
        bb=draw.textbbox((0,0),text,font=font); w=bb[2]-bb[0]; x=150-w//2+xoff
        draw.text((x,y-bb[1]),text,font=font,fill=fill,stroke_width=int(style.get("stroke_width",1)),stroke_fill=(0,0,0))
    out=tmp/"outro_precompuesta.png"; img.save(out); return out

def dj_progress(pct: int, message: str):
    # Marcadores consumidos por server.py para una barra de progreso REAL por etapas.
    print(f"DJGABO_PROGRESS:{max(0,min(100,int(pct)))}:{message}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera un karaoke CD+G desde el JSON del editor.")
    ap.add_argument("timings", type=Path, help="archivo .timings.json exportado por el editor")
    ap.add_argument("audio", type=Path, help="el mismo audio que se usó para sincronizar")
    ap.add_argument("-o", "--out", type=Path, default=Path("salida"), help="directorio de salida")
    ap.add_argument("-s", "--style", type=Path, default=HERE / "style.json")
    ap.add_argument("--preview", action="store_true", help="generar también un MP4 de revisión")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )
    log = logging.getLogger("render")

    if not args.audio.is_file():
        log.error("No encuentro el audio: %s", args.audio)
        return 2

    style = json.loads(args.style.read_text(encoding="utf-8"))
    dj_progress(4, "Leyendo estilo y proyecto…")

    # ---- normalización ----------------------------------------------------
    try:
        doc = N.load(args.timings)
        norm = N.normalize(doc, style)
        dj_progress(14, "Timings validados…")
    except N.NormalizeError as e:
        log.error("%s", e)
        return 1

    n_lines = norm.text.count("\n") + 1
    log.info("%d sílabas · %d líneas visuales · %d instrumental(es)",
             len(norm.sync), n_lines, len(norm.instrumentals))

    for w in norm.warnings:
        log.warning("palabra «%s» (%s): %s", w.text, w.word_id, w.detail)

    # ---- composición ------------------------------------------------------
    from cdgmaker.composer import KaraokeComposer  # noqa: E402

    outname = f"{slugify(norm.artist)}--{slugify(norm.title)}" if norm.artist else slugify(norm.title)
    args.out.mkdir(parents=True, exist_ok=True)

    catcher = WarningCatcher()
    comp_log = logging.getLogger("cdgmaker.composer")
    comp_log.addHandler(catcher)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio_local = tmp / args.audio.name
        shutil.copy(args.audio, audio_local)

        # La portada se compone aquí, en cajas seguras, para evitar que cdgmaker
        # vuelva a centrar el título/artista encima del logo DJGABO.
        # DJGABO_RENDER_TIMELINE_RENDERER_V1
        # La decisión ya viene resuelta por el JSON/normalizador. No se vuelve
        # a interpretar la primera sílaba ni se deja que un sintético cambie el reloj.
        style_run = dict(style)
        style_run["intro_duration_seconds"] = float(norm.render_timeline["opening"]["duration_seconds"])
        style_run["intro_mode"] = "always" if style_run["intro_duration_seconds"] > 0 else "never"
        # DJGABO_AUTHORITATIVE_PAGES_RENDERER_V1
        # No mezclar filas de página anterior/nueva: el compositor limpia por página.
        style_run["clear_mode"] = "page"
        log.info(
            "render timeline: first_real_voice=%s · opening=%ss · rule=%s · intro_delay=0",
            norm.render_timeline.get("first_real_voice_seconds"),
            style_run["intro_duration_seconds"],
            norm.render_timeline["opening"].get("rule"),
        )
        try:
            intro = compose_intro_background(style_run, norm.title, norm.artist, tmp)
            style_run["title_background"] = str(intro)
            style_run["intro_precomposed"] = True
        except Exception as e:
            log.warning("No pude precomponer la intro; uso la plantilla normal: %s", e)
        try:
            outro = compose_outro_background(style_run, tmp)
            style_run["outro_background"] = str(outro)
            style_run["outro_precomposed"] = True
        except Exception as e:
            log.warning("No pude precomponer el ending; uso el ending normal: %s", e)

        toml = N.to_toml(norm, style_run, Path(args.audio.name), outname, HERE)
        (tmp / "config.toml").write_text(toml, encoding="utf-8")
        if args.verbose:
            (args.out / f"{outname}.toml").write_text(toml, encoding="utf-8")

        dj_progress(24, "Preparando compositor CDG…")
        comp = KaraokeComposer.from_file(tmp / "config.toml", logger=comp_log)
        dj_progress(32, "Componiendo barrido CDG…")
        comp.compose()
        dj_progress(86, "CDG compuesto · empaquetando…")
        if args.preview:
            log.info("generando MP4 de revisión (esto tarda)…")
            comp.create_mp4(height=480, fps=30)

        # cdgmaker empaqueta el .cdg y el .mp3 dentro de un zip; los dejamos
        # también sueltos porque muchos reproductores esperan los dos archivos.
        import zipfile
        for z in tmp.glob(f"{outname}*.zip"):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(tmp)

        produced = []
        for p in sorted(tmp.iterdir()):
            if p.suffix.lower() in (".cdg", ".mp3", ".zip", ".mp4") and p.name.startswith(outname):
                shutil.copy(p, args.out / p.name)
                produced.append(args.out / p.name)
        dj_progress(96, "Verificando archivo CDG…")

    report = {
        "song": {"artist": norm.artist, "title": norm.title},
        "syllables": len(norm.sync),
        "visual_lines": n_lines,
        "pre_render_warnings": [vars(w) for w in norm.warnings],
        "composer_warnings": catcher.items,
        "render_timeline": norm.render_timeline,
        "render_pages": norm.render_pages,
    }
    (args.out / f"{outname}.avisos.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    total = len(norm.warnings) + len(catcher.items)
    for p in produced:
        log.info("→ %s", p)
    if total:
        log.warning("%d aviso(s). Revisa %s y corrige esas palabras en el editor.",
                    total, args.out / f"{outname}.avisos.json")
    else:
        log.info("Sin avisos. El barrido cabe entero en el presupuesto de paquetes.")
    dj_progress(100, "CDG terminado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
