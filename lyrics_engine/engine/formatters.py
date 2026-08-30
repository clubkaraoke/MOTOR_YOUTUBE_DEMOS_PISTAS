from __future__ import annotations


def lrc_timestamp(seconds: float) -> str:
    value = max(0.0, float(seconds))
    minutes = int(value // 60)
    remainder = value - minutes * 60
    return f"{minutes:02d}:{remainder:05.2f}"


def format_lrc(lines: list[dict]) -> str:
    """Genera LRC de línea usando el primer tiempo detectado de cada línea.

    V0.2: estos tiempos corresponden a aparición/estabilización visual.
    El timing por palabra/highlight será una fase posterior.
    """
    return "\n".join(
        f"[{lrc_timestamp(item.get('time', 0.0))}]"
        f"{item.get('text', '').strip()}"
        for item in lines
        if item.get("text", "").strip()
    )
