from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .text_corrector import TextCorrector


@dataclass
class LabFileScore:
    filename: str
    score: float
    flags: list[str]
    quality: str
    confidence: float
    pages: int
    pages_per_minute: float
    max_page_gap: float
    corrections: int
    correction_rate: float
    low_confidence_lines: int


class LabAnalyzer:
    """Analiza resultados de muchos CDG sin volver a ejecutar OCR.

    El LAB separa dos conceptos:
    1) resultado de una canción;
    2) conocimiento acumulado del lote.

    Las correcciones aprendidas nunca se aplican automáticamente aquí.
    Primero se acumulan como evidencia con conteo, confianza y tipo.
    """

    def __init__(
        self,
        corrector: TextCorrector | None = None,
    ) -> None:
        self.corrector = corrector or TextCorrector()

    @staticmethod
    def _page_gaps(result: dict[str, Any]) -> list[float]:
        events = [
            float(item.get("time", 0.0))
            for item in result.get("page_events", [])
            if isinstance(item, dict)
        ]
        duration = float(
            result.get("duration_seconds", 0.0)
            or 0.0
        )

        if not events:
            return [duration] if duration > 0 else []

        ordered = sorted(
            max(0.0, value)
            for value in events
        )

        gaps: list[float] = []
        previous = 0.0

        for value in ordered:
            if value > previous:
                gaps.append(value - previous)
            previous = value

        if duration > previous:
            gaps.append(duration - previous)

        return gaps

    def analyze_result(
        self,
        result: dict[str, Any],
    ) -> LabFileScore:
        filename = str(
            result.get("filename", "archivo.cdg")
        )
        confidence = float(
            result.get("average_confidence", 0.0)
            or 0.0
        )
        duration = float(
            result.get("duration_seconds", 0.0)
            or 0.0
        )
        pages = int(
            result.get("pages_detected", 0)
            or 0
        )
        corrections = int(
            result.get("corrections_count", 0)
            or 0
        )
        lines = [
            item
            for item in result.get("lines", [])
            if isinstance(item, dict)
        ]
        line_count = max(
            int(
                result.get("lines_detected", 0)
                or 0
            ),
            len(lines),
        )
        low_confidence_lines = sum(
            1
            for item in lines
            if float(
                item.get("confidence", 0.0)
                or 0.0
            ) < 80.0
        )
        correction_rate = (
            corrections / line_count
            if line_count
            else 0.0
        )
        minutes = duration / 60.0
        pages_per_minute = (
            pages / minutes
            if minutes > 0
            else 0.0
        )
        gaps = self._page_gaps(result)
        max_page_gap = max(gaps) if gaps else 0.0

        flags: list[str] = []

        if pages_per_minute < 3.0 and duration >= 90.0:
            flags.append("LOW_PAGE_DENSITY")

        if max_page_gap >= 30.0:
            flags.append("LARGE_PAGE_GAP")

        if line_count and (
            low_confidence_lines / line_count
        ) >= 0.20:
            flags.append("LOW_CONFIDENCE_LINES")

        if correction_rate >= 0.50:
            flags.append("HIGH_CORRECTION_RATE")

        fallback_pages = int(
            result.get("ocr_fallback_pages", 0)
            or 0
        )
        if pages and (
            fallback_pages / pages
        ) >= 0.35:
            flags.append("OCR_FALLBACK_HEAVY")

        suspicious_split = False

        for change in result.get(
            "corrections",
            [],
        ):
            if not isinstance(change, dict):
                continue

            if change.get("type") != "split":
                continue

            source = str(
                change.get("from", "")
            )
            target = str(
                change.get("to", "")
            )

            # Si la palabra original existe en el léxico y la separación
            # produce 3+ piezas, la marcamos para revisión del LAB.
            if (
                self.corrector.frequency(source)
                >= 300
                or len(target.split()) >= 3
            ):
                suspicious_split = True
                break

        if suspicious_split:
            flags.append("SUSPICIOUS_SPLIT")

        score = confidence

        penalties = {
            "LOW_PAGE_DENSITY": 8.0,
            "LARGE_PAGE_GAP": 7.0,
            "LOW_CONFIDENCE_LINES": 8.0,
            "HIGH_CORRECTION_RATE": 5.0,
            "OCR_FALLBACK_HEAVY": 4.0,
            "SUSPICIOUS_SPLIT": 6.0,
        }

        for flag in flags:
            score -= penalties.get(flag, 0.0)

        score = round(
            max(0.0, min(100.0, score)),
            2,
        )

        return LabFileScore(
            filename=filename,
            score=score,
            flags=flags,
            quality=str(
                result.get("quality", "MALA")
            ),
            confidence=round(confidence, 2),
            pages=pages,
            pages_per_minute=round(
                pages_per_minute,
                2,
            ),
            max_page_gap=round(
                max_page_gap,
                2,
            ),
            corrections=corrections,
            correction_rate=round(
                correction_rate,
                3,
            ),
            low_confidence_lines=(
                low_confidence_lines
            ),
        )

    def learned_corrections(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        stats: dict[
            tuple[str, str, str],
            dict[str, Any],
        ] = {}

        for result in results:
            lines = result.get("lines", [])

            for change in result.get(
                "corrections",
                [],
            ):
                if not isinstance(change, dict):
                    continue

                source = str(
                    change.get("from", "")
                ).strip()
                target = str(
                    change.get("to", "")
                ).strip()
                kind = str(
                    change.get("type", "auto")
                )

                if (
                    not source
                    or not target
                    or source == target
                ):
                    continue

                line_index = change.get(
                    "line_index"
                )
                line_confidence = 0.0

                if (
                    isinstance(line_index, int)
                    and 0 <= line_index < len(lines)
                    and isinstance(
                        lines[line_index],
                        dict,
                    )
                ):
                    line_confidence = float(
                        lines[line_index].get(
                            "confidence",
                            0.0,
                        )
                        or 0.0
                    )

                key = (
                    source.upper(),
                    target.upper(),
                    kind,
                )
                item = stats.setdefault(
                    key,
                    {
                        "from": source.upper(),
                        "to": target.upper(),
                        "type": kind,
                        "count": 0,
                        "confidence_sum": 0.0,
                        "files": set(),
                    },
                )
                item["count"] += 1
                item["confidence_sum"] += (
                    line_confidence
                )
                item["files"].add(
                    str(
                        result.get(
                            "filename",
                            "archivo.cdg",
                        )
                    )
                )

        learned: list[dict[str, Any]] = []

        for item in stats.values():
            count = int(item["count"])
            files = len(item["files"])
            average_confidence = (
                item["confidence_sum"] / count
                if count
                else 0.0
            )

            # LAB: una regla empieza a ser interesante con repetición real.
            # Todavía NO significa que vaya a aplicarse automáticamente.
            status = "OBSERVAR"

            if (
                count >= 5
                and files >= 3
                and average_confidence >= 80.0
            ):
                status = "FUERTE"
            elif (
                count >= 2
                and files >= 2
            ):
                status = "CANDIDATA"

            learned.append(
                {
                    "from": item["from"],
                    "to": item["to"],
                    "type": item["type"],
                    "count": count,
                    "files": files,
                    "average_line_confidence": round(
                        average_confidence,
                        2,
                    ),
                    "status": status,
                }
            )

        learned.sort(
            key=lambda item: (
                item["status"] == "FUERTE",
                item["files"],
                item["count"],
                item["average_line_confidence"],
            ),
            reverse=True,
        )

        return learned

    def summarize(
        self,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        file_scores = [
            self.analyze_result(result)
            for result in results
        ]

        quality_counts = Counter(
            item.quality
            for item in file_scores
        )
        flag_counts = Counter(
            flag
            for item in file_scores
            for flag in item.flags
        )
        strategy_counts = Counter(
            str(
                result.get(
                    "strategy",
                    "unknown",
                )
            )
            for result in results
        )

        ordered = sorted(
            file_scores,
            key=lambda item: (
                item.score,
                item.confidence,
            ),
        )

        average_score = (
            round(
                sum(
                    item.score
                    for item in file_scores
                )
                / len(file_scores),
                2,
            )
            if file_scores
            else 0.0
        )

        return {
            "lab_version": "1.0",
            "files_analyzed": len(results),
            "average_lab_score": average_score,
            "quality_counts": dict(
                quality_counts
            ),
            "flag_counts": dict(
                flag_counts
            ),
            "strategy_counts": dict(
                strategy_counts
            ),
            "learned_corrections": (
                self.learned_corrections(
                    results
                )
            ),
            "worst_files": [
                {
                    "filename": item.filename,
                    "lab_score": item.score,
                    "ocr_confidence": (
                        item.confidence
                    ),
                    "quality": item.quality,
                    "flags": item.flags,
                    "pages": item.pages,
                    "pages_per_minute": (
                        item.pages_per_minute
                    ),
                    "max_page_gap": (
                        item.max_page_gap
                    ),
                    "corrections": (
                        item.corrections
                    ),
                    "correction_rate": (
                        item.correction_rate
                    ),
                    "low_confidence_lines": (
                        item.low_confidence_lines
                    ),
                }
                for item in ordered[:50]
            ],
        }
