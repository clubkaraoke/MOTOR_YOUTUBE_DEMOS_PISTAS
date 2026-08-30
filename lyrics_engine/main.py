from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from engine import CDGLyricsExtractor
from engine.dropbox_lab import DropboxLabClient, DropboxLabError
from engine.lab import LabAnalyzer
from engine.lab_queue import LabQueue
from engine.lab_worker import LabWorker
from engine.text_corrector import TextCorrector

BASE_DIR = Path(__file__).resolve().parent
MAX_CDG_BYTES = 20 * 1024 * 1024

PACK_PATHS = {
    7: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -7",
    8: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top HIts PERU -8",
    9: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -9",
    10: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -10",
    11: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -11",
    12: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -12",
    13: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -13",
    14: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -14",
    15: "/djgabo berrocal/1.- Pack Karaoke PRO MASTER/4.- Pack Karaoke TOP PERÚ (1)/Pack Top Hits PERU -15",
}

app = FastAPI(
    title="CDG Lyrics Engine",
    version="0.8.0-lab",
)

lab_queue = LabQueue()
lab_worker = LabWorker(lab_queue)
lab_worker.start_thread()


def container_memory_limit_bytes() -> int:
    for candidate in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw or raw == "max":
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return 0


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")


@app.get("/lab", response_class=HTMLResponse)
def lab_home() -> str:
    return (BASE_DIR / "web" / "lab.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict:
    corrector = TextCorrector()
    dropbox = DropboxLabClient()
    return {
        "ok": True,
        "engine": "CDG_LYRICS_ENGINE",
        "version": "0.8.0-lab",
        "lexicon_words": len(corrector.freq),
        "lexicon_con": corrector.frequency("con"),
        "dropbox_configured": dropbox.configured,
        "dropbox_connected": dropbox.connected,
        "lab_queue": lab_queue.counts(),
        "lab_worker_memory_limit_percent": lab_worker.max_memory_percent,
        "container_memory_limit_bytes": container_memory_limit_bytes(),
    }


@app.post("/api/lab/analyze")
def analyze_lab_batch(
    results: list[dict] = Body(...),
) -> dict:
    if not results:
        raise HTTPException(
            status_code=400,
            detail="El LAB necesita al menos un resultado CDG",
        )

    if len(results) > 500:
        raise HTTPException(
            status_code=413,
            detail="Máximo 500 resultados por análisis LAB",
        )

    analyzer = LabAnalyzer()

    try:
        return analyzer.summarize(results)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo analizar el lote: {exc}",
        ) from exc


@app.get("/api/lab/dropbox/status")
def dropbox_lab_status() -> dict:
    client = DropboxLabClient()
    return {
        "dropbox": client.status(),
        "worker": lab_worker.status(),
        "packs": [
            {"number": number, "path": path}
            for number, path in PACK_PATHS.items()
        ],
        "recent": lab_queue.recent(25),
        "recent_done": lab_queue.recent_done(10),
        "worst": lab_queue.worst_done(20),
    }


@app.get("/api/lab/dropbox/result/{job_id}")
def dropbox_lab_result(job_id: int) -> dict:
    result = lab_queue.load_job_result(job_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Resultado LAB no encontrado",
        )

    return {
        "job": result.get("_job", {}),
        "filename": result.get("filename", ""),
        "duration_seconds": result.get("duration_seconds", 0),
        "strategy": result.get("strategy", ""),
        "quality": result.get("quality", ""),
        "average_confidence": result.get("average_confidence", 0),
        "pages_detected": result.get("pages_detected", 0),
        "lines_detected": result.get("lines_detected", 0),
        "corrections_count": result.get("corrections_count", 0),
        "lyrics": result.get("lyrics", ""),
        "lines": result.get("lines", []),
        "corrections": result.get("corrections", [])[:120],
        "lab": result.get("lab", {}),
    }


@app.post("/api/lab/dropbox/connect")
def dropbox_lab_connect() -> dict:
    client = DropboxLabClient()
    try:
        return {"authorization_url": client.authorization_url()}
    except DropboxLabError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/lab/dropbox/callback")
def dropbox_lab_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    client = DropboxLabClient()
    try:
        client.exchange_code(code, state)
    except DropboxLabError as exc:
        return HTMLResponse(
            status_code=400,
            content=(
                "<h2>No se pudo conectar Dropbox</h2>"
                f"<pre>{str(exc)}</pre>"
                '<p><a href="/cdg-lyrics/lab">Volver al LAB</a></p>'
            ),
        )

    return RedirectResponse(
        url="/cdg-lyrics/lab?dropbox=connected",
        status_code=302,
    )


@app.post("/api/lab/dropbox/index")
def dropbox_lab_index(
    payload: dict = Body(default={}),
) -> dict:
    client = DropboxLabClient()

    if not client.connected:
        raise HTTPException(
            status_code=409,
            detail="Dropbox todavía no está conectado",
        )

    requested = payload.get("packs") or list(PACK_PATHS)
    try:
        selected = sorted({int(value) for value in requested})
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Lista de packs inválida") from exc

    unknown = [value for value in selected if value not in PACK_PATHS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Packs no configurados: {unknown}",
        )

    reports = []
    total_found = 0

    for number in selected:
        path = PACK_PATHS[number]
        try:
            entries = client.list_cdgs(path)
        except DropboxLabError as exc:
            reports.append(
                {
                    "pack": number,
                    "path": path,
                    "ok": False,
                    "error": str(exc),
                    "cdg_found": 0,
                }
            )
            continue

        total_found += len(entries)
        queue_report = lab_queue.add_jobs(
            entries,
            pack=f"Pack Top Hits PERU -{number}",
        )
        reports.append(
            {
                "pack": number,
                "path": path,
                "ok": True,
                "cdg_found": len(entries),
                **queue_report,
            }
        )

    return {
        "ok": any(item["ok"] for item in reports),
        "total_cdg_found": total_found,
        "counts": lab_queue.counts(),
        "packs": reports,
    }


@app.post("/api/lab/dropbox/worker/start")
def dropbox_worker_start() -> dict:
    client = DropboxLabClient()
    if not client.connected:
        raise HTTPException(
            status_code=409,
            detail="Conecta Dropbox antes de iniciar la cola",
        )
    lab_queue.set_run_limit(None)
    lab_queue.set_worker_enabled(True)
    lab_worker.start_thread()
    return lab_worker.status()


@app.post("/api/lab/dropbox/worker/start-limited")
def dropbox_worker_start_limited(
    payload: dict = Body(default={}),
) -> dict:
    client = DropboxLabClient()
    if not client.connected:
        raise HTTPException(
            status_code=409,
            detail="Conecta Dropbox antes de iniciar la cola",
        )

    try:
        max_jobs = int(payload.get("max_jobs", 5))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="max_jobs debe ser un número entero",
        ) from exc

    if not 1 <= max_jobs <= 50:
        raise HTTPException(
            status_code=400,
            detail="max_jobs debe estar entre 1 y 50",
        )

    counts = lab_queue.counts()
    if int(counts.get("PENDING", 0)) <= 0:
        raise HTTPException(
            status_code=409,
            detail="No hay CDG pendientes en la cola",
        )

    lab_queue.set_run_limit(max_jobs)
    lab_queue.set_worker_enabled(True)
    lab_worker.start_thread()
    return lab_worker.status()


@app.post("/api/lab/dropbox/worker/pause")
def dropbox_worker_pause() -> dict:
    lab_queue.set_worker_enabled(False)
    return lab_worker.status()


@app.post("/api/lab/dropbox/retry-errors")
def dropbox_retry_errors() -> dict:
    count = lab_queue.retry_errors()
    return {
        "retried": count,
        "counts": lab_queue.counts(),
    }


@app.get("/api/lab/dropbox/summary")
def dropbox_lab_summary() -> dict:
    results = lab_queue.load_done_results(500)
    analyzer = LabAnalyzer()
    summary = analyzer.summarize(results) if results else {
        "lab_version": "1.0",
        "files_analyzed": 0,
        "average_lab_score": 0,
        "quality_counts": {},
        "flag_counts": {},
        "strategy_counts": {},
        "learned_corrections": [],
        "worst_files": [],
    }
    summary["queue_counts"] = lab_queue.counts()
    summary["worst_persistent"] = lab_queue.worst_done(50)
    return summary


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
