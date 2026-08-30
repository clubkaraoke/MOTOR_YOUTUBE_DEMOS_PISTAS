from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .dropbox_lab import DropboxLabClient
from .lab import LabAnalyzer
from .lab_queue import LabQueue, memory_percent


class LabWorker:
    """Worker secuencial para el LAB masivo.

    Procesa exactamente un CDG por vez. Si la RAM supera el umbral, espera
    antes de tomar otro trabajo.
    """

    def __init__(
        self,
        queue: LabQueue,
        *,
        max_memory_percent: float = 60.0,
        poll_seconds: float = 3.0,
        max_cdg_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.queue = queue
        self.max_memory_percent = max_memory_percent
        self.poll_seconds = poll_seconds
        self.max_cdg_bytes = max_cdg_bytes
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self.queue.reset_stale_processing()
        self._thread = threading.Thread(
            target=self._run,
            name="cdg-lab-worker",
            daemon=True,
        )
        self._thread.start()

    def stop_thread(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            current = dict(self._current) if self._current else None

        return {
            "enabled": self.queue.worker_enabled(),
            "thread_alive": bool(
                self._thread and self._thread.is_alive()
            ),
            "memory_percent": memory_percent(),
            "memory_limit_percent": self.max_memory_percent,
            "current": current,
            "counts": self.queue.counts(),
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.queue.worker_enabled():
                time.sleep(self.poll_seconds)
                continue

            if memory_percent() >= self.max_memory_percent:
                time.sleep(max(10.0, self.poll_seconds))
                continue

            client = DropboxLabClient()
            if not client.connected:
                time.sleep(max(10.0, self.poll_seconds))
                continue

            job = self.queue.claim_next()
            if not job:
                time.sleep(self.poll_seconds)
                continue

            with self._lock:
                self._current = {
                    "id": job["id"],
                    "name": job["name"],
                    "pack": job.get("pack"),
                    "dropbox_path": job["dropbox_path"],
                }

            try:
                self._process(job, client)
            except Exception as exc:
                self.queue.fail(
                    int(job["id"]),
                    f"{type(exc).__name__}: {exc}",
                )
            finally:
                with self._lock:
                    self._current = None

    def _process(
        self,
        job: dict[str, Any],
        client: DropboxLabClient,
    ) -> None:
        # Import tardío: evita cargar OpenCV/Tesseract cuando el LAB está
        # simplemente esperando.
        from .extractor import CDGLyricsExtractor

        with tempfile.TemporaryDirectory(prefix="cdg_lab_") as tmp:
            path = Path(tmp) / str(job["name"])
            size = client.download(
                str(job["dropbox_path"]),
                path,
            )

            if size <= 0:
                raise RuntimeError("Dropbox devolvió un CDG vacío")

            if size > self.max_cdg_bytes:
                raise RuntimeError(
                    f"CDG demasiado grande para LAB: {size} bytes"
                )

            extractor = CDGLyricsExtractor()
            result = extractor.extract(path)

        analyzer = LabAnalyzer()
        score = analyzer.analyze_result(result)

        result["lab"] = {
            "score": score.score,
            "flags": score.flags,
            "pages_per_minute": score.pages_per_minute,
            "max_page_gap": score.max_page_gap,
            "correction_rate": score.correction_rate,
            "low_confidence_lines": score.low_confidence_lines,
        }
        result["dropbox_path"] = str(job["dropbox_path"])
        result["pack"] = str(job.get("pack") or "")

        result_path = self.queue.save_result(
            job,
            result,
        )
        self.queue.finish(
            int(job["id"]),
            result_path,
            result,
            score.score,
        )
