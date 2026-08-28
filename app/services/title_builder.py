import re
from pathlib import Path


YOUTUBE_TITLE_LIMIT = 100


def _compact_words(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    words = value.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join(kept + [word])
        if len(candidate) + 1 > limit:
            break
        kept.append(word)
    return (" ".join(kept).rstrip(" ,-|") + "…") if kept else value[: max(1, limit - 1)].rstrip() + "…"


def build_youtube_title(filename: str, artist: str | None = None, title: str | None = None) -> str:
    stem = Path(filename).stem
    has_chorus = bool(re.search(r"\(\s*coros?\s*\)", stem, flags=re.I))
    base = re.sub(r"\(\s*coros?\s*\)", " ", stem, flags=re.I)
    base = " ".join(base.split()).strip(" -_,")
    if not base and artist and title:
        base = f"{artist} - {title}"
    suffix = " + COROS | Pista Musical" if has_chorus else " | Pista Musical"
    candidate = f"{base}{suffix}"
    if len(candidate) <= YOUTUBE_TITLE_LIMIT:
        return candidate
    # KARAOKE is preserved when the source title fits, and is the first
    # redundant commercial word removed when space is needed.
    compact_base = re.sub(r"\bkaraoke\b", " ", base, flags=re.I)
    compact_base = " ".join(compact_base.split()).strip(" -_,")
    compact_candidate = f"{compact_base}{suffix}"
    if len(compact_candidate) <= YOUTUBE_TITLE_LIMIT:
        return compact_candidate
    available = YOUTUBE_TITLE_LIMIT - len(suffix)
    return f"{_compact_words(compact_base, available)}{suffix}"[:YOUTUBE_TITLE_LIMIT]
