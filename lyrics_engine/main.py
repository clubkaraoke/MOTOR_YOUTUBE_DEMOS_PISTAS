from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from engine import CDGLyricsExtractor
from engine.text_corrector import TextCorrector

BASE_DIR = Path(__file__).resolve().parent
MAX_CDG_BYTES = 20 * 1024 * 1024

app = FastAPI(
    title="CDG Lyrics Engine",
    version="0.6.0",
)


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict:
    corrector = TextCorrector()
    return {
        "ok": True,
        "engine": "CDG_LYRICS_ENGINE",
        "version": "0.6.0",
        "lexicon_words": len(corrector.freq),
        "lexicon_con": corrector.frequency("con"),
    }


@app.post("/api/extract")
async def extract_lyrics(file: UploadFile = File(...)) -> dict:
    filename = file.filename or "archivo.cdg"

    if not filename.lower().endswith(".cdg"):
        raise HTTPException(status_code=400, detail="Debes subir un archivo .cdg")

    payload = await file.read()

    if not payload:
        raise HTTPException(status_code=400, detail="El archivo está vacío")

    if len(payload) > MAX_CDG_BYTES:
        raise HTTPException(
            status_code=413,
            detail="El CDG supera el límite de 20 MB para esta prueba",
        )

    if len(payload) < 24:
        raise HTTPException(status_code=400, detail="CDG inválido o demasiado pequeño")

    with tempfile.TemporaryDirectory(prefix="cdg_lyrics_") as tmp:
        path = Path(tmp) / filename
        path.write_bytes(payload)

        extractor = CDGLyricsExtractor()
        try:
            result = extractor.extract(path)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo procesar el CDG: {exc}",
            ) from exc

    return result
