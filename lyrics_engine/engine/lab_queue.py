from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LabQueue:
    """Cola persistente para procesar miles de CDG sin cargarlos en RAM."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(
            os.getenv("CDG_LAB_DATA_DIR", "/data/cdg_lab")
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir = self.base_dir / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "lab.sqlite3"
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dropbox_path TEXT NOT NULL UNIQUE,
                    dropbox_id TEXT,
                    name TEXT NOT NULL,
                    pack TEXT,
                    size INTEGER NOT NULL DEFAULT 0,
                    modified TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT,
                    result_path TEXT,
                    lab_score REAL,
                    ocr_confidence REAL,
                    quality TEXT,
                    pages INTEGER,
                    lines INTEGER,
                    corrections INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status, id);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def set_worker_enabled(self, enabled: bool) -> None:
        self.set_setting("worker_enabled", "1" if enabled else "0")

    def worker_enabled(self) -> bool:
        return self.get_setting("worker_enabled", "0") == "1"

    def set_run_limit(self, limit: int | None) -> None:
        value = -1 if limit is None else max(0, int(limit))
        self.set_setting("run_remaining", str(value))

    def run_remaining(self) -> int:
        try:
            return int(self.get_setting("run_remaining", "-1"))
        except ValueError:
            return -1

    def consume_run_slot(self) -> int:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM settings WHERE key='run_remaining'"
            ).fetchone()
            remaining = int(row["value"]) if row else -1

            if remaining > 0:
                remaining -= 1
                conn.execute(
                    """
                    INSERT INTO settings(key, value)
                    VALUES ('run_remaining', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (str(remaining),),
                )

            if remaining == 0:
                conn.execute(
                    """
                    INSERT INTO settings(key, value)
                    VALUES ('worker_enabled', '0')
                    ON CONFLICT(key) DO UPDATE SET value='0'
                    """
                )

            conn.commit()
        return remaining

    def add_jobs(self, entries: list[dict[str, Any]], pack: str) -> dict[str, int]:
        inserted = 0
        existing = 0
        now = utc_now()

        with self._lock, self._connect() as conn:
            for entry in entries:
                path = str(
                    entry.get("path_display")
                    or entry.get("path_lower")
                    or ""
                )
                name = str(entry.get("name") or Path(path).name)
                if not path or not name.lower().endswith(".cdg"):
                    continue

                before = conn.total_changes
                conn.execute(
                    """
                    INSERT INTO jobs(
                        dropbox_path, dropbox_id, name, pack, size,
                        modified, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                    ON CONFLICT(dropbox_path) DO UPDATE SET
                        dropbox_id=excluded.dropbox_id,
                        name=excluded.name,
                        pack=excluded.pack,
                        size=excluded.size,
                        modified=excluded.modified,
                        updated_at=excluded.updated_at
                    """,
                    (
                        path,
                        str(entry.get("id") or ""),
                        name,
                        pack,
                        int(entry.get("size") or 0),
                        str(entry.get("server_modified") or ""),
                        now,
                        now,
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
                else:
                    existing += 1

        return {"inserted_or_updated": inserted, "existing": existing}

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]

        result = {str(row["status"]): int(row["n"]) for row in rows}
        result["TOTAL"] = int(total)
        return result

    def claim_next(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status='PENDING'
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()

            if row is None:
                conn.commit()
                return None

            now = utc_now()
            conn.execute(
                """
                UPDATE jobs
                SET status='PROCESSING',
                    attempts=attempts+1,
                    started_at=?,
                    updated_at=?,
                    error=NULL
                WHERE id=?
                """,
                (now, now, int(row["id"])),
            )
            conn.commit()

        return dict(row)

    def reset_stale_processing(self) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status='PENDING',
                    updated_at=?,
                    error='Recuperado después de reinicio del worker'
                WHERE status='PROCESSING'
                """,
                (now,),
            )
        return int(cursor.rowcount or 0)

    def _result_file(self, dropbox_path: str) -> Path:
        digest = hashlib.sha256(
            dropbox_path.encode("utf-8")
        ).hexdigest()[:24]
        return self.results_dir / f"{digest}.json"

    def save_result(self, job: dict[str, Any], result: dict[str, Any]) -> Path:
        path = self._result_file(str(job["dropbox_path"]))
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def finish(
        self,
        job_id: int,
        result_path: Path,
        result: dict[str, Any],
        lab_score: float,
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status='DONE',
                    finished_at=?,
                    updated_at=?,
                    result_path=?,
                    lab_score=?,
                    ocr_confidence=?,
                    quality=?,
                    pages=?,
                    lines=?,
                    corrections=?,
                    error=NULL
                WHERE id=?
                """,
                (
                    now,
                    now,
                    str(result_path),
                    float(lab_score),
                    float(result.get("average_confidence", 0.0) or 0.0),
                    str(result.get("quality", "")),
                    int(result.get("pages_detected", 0) or 0),
                    int(result.get("lines_detected", 0) or 0),
                    int(result.get("corrections_count", 0) or 0),
                    int(job_id),
                ),
            )

    def fail(self, job_id: int, error: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status='ERROR',
                    finished_at=?,
                    updated_at=?,
                    error=?
                WHERE id=?
                """,
                (now, now, error[:1500], int(job_id)),
            )

    def retry_errors(self) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status='PENDING',
                    updated_at=?,
                    error=NULL,
                    started_at=NULL,
                    finished_at=NULL
                WHERE status='ERROR'
                """,
                (now,),
            )
        return int(cursor.rowcount or 0)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, pack, status, attempts, updated_at,
                       error, lab_score, ocr_confidence, quality,
                       pages, lines, corrections, dropbox_path
                FROM jobs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_done(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, pack, status, finished_at,
                       lab_score, ocr_confidence, quality,
                       pages, lines, corrections, dropbox_path
                FROM jobs
                WHERE status='DONE'
                ORDER BY finished_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_job_result(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, pack, result_path, lab_score,
                       ocr_confidence, quality, pages, lines,
                       corrections, finished_at
                FROM jobs
                WHERE id=? AND status='DONE' AND result_path IS NOT NULL
                """,
                (int(job_id),),
            ).fetchone()

        if row is None:
            return None

        try:
            payload = json.loads(
                Path(str(row["result_path"])).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None

        payload["_job"] = dict(row)
        return payload

    def worst_done(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, pack, lab_score, ocr_confidence,
                       quality, pages, lines, corrections, dropbox_path
                FROM jobs
                WHERE status='DONE'
                ORDER BY COALESCE(lab_score, 0) ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_done_results(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT result_path
                FROM jobs
                WHERE status='DONE' AND result_path IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            try:
                results.append(
                    json.loads(
                        Path(str(row["result_path"])).read_text(
                            encoding="utf-8"
                        )
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue
        return results


def memory_percent() -> float:
    """RAM usada aproximada, sin dependencias externas."""
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])

        total = float(values["MemTotal"])
        available = float(values.get("MemAvailable", values["MemFree"]))
        if total <= 0:
            return 0.0
        return round((1.0 - available / total) * 100.0, 2)
    except Exception:
        return 0.0
