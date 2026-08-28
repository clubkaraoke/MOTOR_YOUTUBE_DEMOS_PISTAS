import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError
from rapidfuzz.fuzz import WRatio

from app.core.config import get_settings
from app.services.google_drive import GoogleStorageError, sheets_service


@dataclass(frozen=True)
class CoverEntry:
    artist: str
    title: str
    cover_url: str
    original_filename: str


@dataclass(frozen=True)
class CoverMatch:
    entry: CoverEntry
    score: float


def normalize_music_text(value: str | None) -> str:
    value = Path(value or "").stem
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"\b(karaoke|instrumental|pista\s+musical)\b", " ", value)
    value = re.sub(r"\b(ft|feat|featuring)\.?\b", " ", value)
    value = re.sub(r"\b(coro|coros)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


class CoverProvider:
    def __init__(self):
        self.settings = get_settings()
        self._entries: list[CoverEntry] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def refresh(self, force: bool = False) -> list[CoverEntry]:
        with self._lock:
            if not force and self._entries and time.monotonic() - self._loaded_at < self.settings.cover_cache_seconds:
                return self._entries
            if self.settings.google_mode == "mock":
                catalog = self.settings.assets_dir / "cover_catalog.json"
                rows = json.loads(catalog.read_text(encoding="utf-8")) if catalog.is_file() else []
                self._entries = [CoverEntry(str(r.get("artist", "")), str(r.get("title", "")),
                                                  str(r.get("cover_url", "")), str(r.get("original_filename", "")))
                                 for r in rows]
            else:
                try:
                    response = sheets_service().spreadsheets().values().batchGet(
                        spreadsheetId=self.settings.cover_spreadsheet_id,
                        ranges=[f"'{self.settings.cover_sheet_name}'!A2:C", f"'{self.settings.cover_sheet_name}'!I2:I"],
                        valueRenderOption="FORMATTED_VALUE",
                    ).execute()
                except Exception as exc:
                    raise GoogleStorageError(f"No se pudo actualizar el catálogo de covers: {exc}") from exc
                ranges = response.get("valueRanges", [])
                core = ranges[0].get("values", []) if ranges else []
                originals = ranges[1].get("values", []) if len(ranges) > 1 else []
                entries: list[CoverEntry] = []
                for index, row in enumerate(core):
                    padded = list(row) + [""] * (3 - len(row))
                    original = originals[index][0] if index < len(originals) and originals[index] else ""
                    if padded[2]:
                        entries.append(CoverEntry(str(padded[0]), str(padded[1]), str(padded[2]), str(original)))
                self._entries = entries
            self._loaded_at = time.monotonic()
            return self._entries

    def find(self, artist: str | None, title: str | None, original_filename: str) -> CoverMatch | None:
        entries = self.refresh()
        query_artist = normalize_music_text(artist)
        query_title = normalize_music_text(title)
        query_file = normalize_music_text(original_filename)
        best: CoverMatch | None = None
        for entry in entries:
            artist_score = WRatio(query_artist, normalize_music_text(entry.artist)) if query_artist else 0
            title_score = WRatio(query_title, normalize_music_text(entry.title)) if query_title else 0
            file_score = WRatio(query_file, normalize_music_text(entry.original_filename))
            score = (artist_score * .35 + title_score * .45 + file_score * .20) if query_artist and query_title else file_score
            candidate = CoverMatch(entry, float(score))
            if best is None or candidate.score > best.score:
                best = candidate
        if not best or best.score < self.settings.cover_match_threshold:
            return None
        return best

    def download(self, url: str, destination: Path) -> Path:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("La URL del cover no es HTTP/HTTPS")
        try:
            # Cover hosts are public CDNs. Ignoring inherited desktop proxy
            # variables prevents a stale local proxy from blocking Cloudinary
            # and Imgur while leaving Google API authentication untouched.
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    url,
                    timeout=(10, 30),
                    allow_redirects=True,
                    stream=True,
                    headers={"User-Agent": "DJGABO-Engine/2"},
                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not content_type.startswith("image/"):
                    raise ValueError(f"El cover no devolvió una imagen ({content_type or 'sin MIME'})")
                data = bytearray()
                for chunk in response.iter_content(1024 * 128):
                    data.extend(chunk)
                    if len(data) > 20 * 1024 * 1024:
                        raise ValueError("El cover supera 20 MB")
            destination.parent.mkdir(parents=True, exist_ok=True)
            raw = destination.with_suffix(".download")
            raw.write_bytes(data)
            with Image.open(raw) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError(f"Formato de cover no permitido: {image.format}")
                image.convert("RGB").save(destination, "PNG", optimize=True)
            raw.unlink(missing_ok=True)
            return destination
        except (requests.RequestException, UnidentifiedImageError, OSError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            destination.with_suffix(".download").unlink(missing_ok=True)
            raise ValueError(f"Cover inválido o inaccesible: {exc}") from exc


cover_provider = CoverProvider()
