from __future__ import annotations

import itertools
import re
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from rapidfuzz import fuzz

from .cdg_decoder import CDGDecoder, PACKETS_PER_SECOND
from .formatters import format_lrc
from .text_corrector import TextCorrector


@dataclass
class OCRLineCandidate:
    time: float
    text: str
    confidence: float
    page: int


class CDGLyricsExtractor:
    """CDG -> páginas reales -> OCR -> letra.

    V0.6 mantiene la captura de página completa y añade OCR adaptativo:
    una lectura rápida spa+eng/PSM6 y, solo en páginas débiles, una segunda
    batería spa+eng/spa con PSM6/11. Después aplica diccionario y contexto
    repetido de la propia canción, sin consultar letras externas.

    Si un CDG no trae suficientes Clear Screen, se conserva el detector
    estable/por colores como fallback.
    """

    def __init__(
        self,
        sample_interval: float = 0.50,
        min_motion: float = 0.006,
        stable_threshold: float = 0.006,
        min_added_pixels: float = 0.012,
        min_occupied: float = 0.010,
        ignore_first_seconds: float = 5.0,
        candidate_cooldown: float = 1.7,
        duplicate_window: float = 22.0,
        min_line_confidence: float = 50.0,
    ) -> None:
        self.sample_interval = sample_interval
        self.min_motion = min_motion
        self.stable_threshold = stable_threshold
        self.min_added_pixels = min_added_pixels
        self.min_occupied = min_occupied
        self.ignore_first_seconds = ignore_first_seconds
        self.candidate_cooldown = candidate_cooldown
        self.duplicate_window = duplicate_window
        self.min_line_confidence = min_line_confidence
        self.text_corrector = TextCorrector()

    def _finalize_lines(
        self,
        raw_lines: list[dict],
    ) -> dict:
        raw_lyrics = "\n".join(
            item["text"]
            for item in raw_lines
        )

        corrected_lines, corrections = (
            self.text_corrector.correct_lines(
                raw_lines
            )
        )

        lyrics = "\n".join(
            item["text"]
            for item in corrected_lines
        )

        average_confidence = (
            round(
                sum(
                    float(item["confidence"])
                    for item in corrected_lines
                )
                / len(corrected_lines),
                2,
            )
            if corrected_lines
            else 0.0
        )

        # "BUENA" significa apta para una revisión rápida, no simplemente
        # que Tesseract superó un umbral bajo. 84% debe seguir en REVISAR.
        if (
            average_confidence >= 92
            and len(corrected_lines) >= 8
        ):
            quality = "BUENA"
        elif (
            average_confidence >= 80
            and len(corrected_lines) >= 5
        ):
            quality = "REVISAR"
        else:
            quality = "MALA"

        return {
            "lines": corrected_lines,
            "lines_detected": len(
                corrected_lines
            ),
            "lyrics": lyrics,
            "lyrics_raw": raw_lyrics,
            "lrc": format_lrc(
                corrected_lines
            ),
            "average_confidence": (
                average_confidence
            ),
            "quality": quality,
            "corrections": corrections,
            "corrections_count": len(
                corrections
            ),
        }

    @staticmethod
    def _full_shape_mask(
        indices: np.ndarray,
        background_index: int,
    ) -> np.ndarray:
        return (
            (indices != background_index)
            .astype(np.uint8)
            * 255
        )

    @staticmethod
    def _visual_change(
        a: np.ndarray | None,
        b: np.ndarray,
    ) -> float:
        if a is None:
            return 1.0
        return (
            float(np.count_nonzero(a != b))
            / float(b.size)
        )

    @staticmethod
    def _mask_text_score(mask: np.ndarray) -> float:
        occupied = (
            float(np.count_nonzero(mask))
            / float(mask.size)
        )
        if occupied < 0.002 or occupied > 0.36:
            return -1e9

        count, _, stats, _ = cv2.connectedComponentsWithStats(
            mask,
            connectivity=8,
        )

        useful = 0
        very_large = 0
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            if 3 <= area <= 900 and 1 <= h <= 40:
                useful += 1
            if area > 2500:
                very_large += 1

        # Texto CDG produce muchos componentes pequeños/medianos.
        # Dibujos grandes y fondos reciben penalización.
        return useful - very_large * 12 - occupied * 25

    @classmethod
    def _candidate_masks(
        cls,
        indices: np.ndarray,
        background_index: int,
    ) -> list[tuple[str, np.ndarray]]:
        flat = indices.reshape(-1)
        counts = Counter(int(v) for v in flat)
        total = float(flat.size)

        candidates: list[tuple[str, np.ndarray]] = []

        full = cls._full_shape_mask(
            indices,
            background_index,
        )
        candidates.append(("all_non_background", full))

        useful_colors: list[int] = []
        for color, count in counts.most_common():
            if color == background_index:
                continue
            ratio = count / total
            if 0.0015 <= ratio <= 0.28:
                useful_colors.append(color)
            if len(useful_colors) >= 5:
                break

        for color in useful_colors:
            mask = (
                (indices == color)
                .astype(np.uint8)
                * 255
            )
            candidates.append((f"color_{color}", mask))

        # Fill + highlight u outline suelen ocupar dos colores.
        for a, b in itertools.combinations(
            useful_colors[:4],
            2,
        ):
            mask = (
                ((indices == a) | (indices == b))
                .astype(np.uint8)
                * 255
            )
            candidates.append((f"colors_{a}_{b}", mask))

        scored: list[
            tuple[float, str, np.ndarray]
        ] = []
        seen: set[bytes] = set()

        for name, raw in candidates:
            mask = cv2.morphologyEx(
                raw,
                cv2.MORPH_CLOSE,
                np.ones((2, 2), np.uint8),
            )
            key = mask.tobytes()
            if key in seen:
                continue
            seen.add(key)

            score = cls._mask_text_score(mask)
            if score > -1e8:
                scored.append((score, name, mask))

        scored.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        # Máximo 3 OCR por pantalla para no multiplicar demasiado el coste.
        return [
            (name, mask)
            for _, name, mask in scored[:3]
        ]

    @staticmethod
    def _prepare_for_ocr(
        mask: np.ndarray,
        scale: int = 6,
    ) -> np.ndarray:
        # El pixel-art CDG se conserva mejor con nearest. En la prueba real
        # "Así soy yo", 6x mejoró varios caracteres frente al perfil anterior.
        scaled = cv2.resize(
            mask,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_NEAREST,
        )
        return 255 - scaled

    @staticmethod
    def _normalize_line(value: str) -> str:
        value = value.replace("|", "I")
        value = re.sub(r"[\t ]+", " ", value)
        value = re.sub(
            r"^[^\wÁÉÍÓÚÜÑáéíóúüñ¿¡]+",
            "",
            value,
        )
        value = re.sub(
            r"[^\wÁÉÍÓÚÜÑáéíóúüñ¿¡'.,!?():;\- ]+$",
            "",
            value,
        )
        return value.strip()

    @classmethod
    def _valid_line(
        cls,
        value: str,
    ) -> str | None:
        line = cls._normalize_line(value)

        if len(line) < 2:
            return None

        if not re.search(
            r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]",
            line,
        ):
            return None

        letters = re.findall(
            r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+",
            line,
        )
        if not letters:
            return None

        # Una "palabra" enorme sin espacios suele ser varios glifos
        # pegados por OCR, como en la prueba que dio 45%.
        if max(map(len, letters)) > 24:
            return None

        return line

    @staticmethod
    def _looks_like_non_lyric(
        line: str,
    ) -> bool:
        low = line.casefold()
        blocked = (
            "www.",
            "http",
            "karaoke",
            "producciones",
            "production",
            "ediciones",
            "copyright",
            "todos los derechos",
            "instrumental",
            "whatsapp",
            "demo gratis",
            "solicita demo",
            "video karaoke",
            "convertimos tu",
            "al estilo de",
            "presenta:",
            "presenta ",
        )
        return any(
            token in low
            for token in blocked
        )

    @classmethod
    def _is_metadata_screen(
        cls,
        lines: list[tuple[str, float]],
    ) -> bool:
        if not lines:
            return False

        lows = [
            text.casefold()
            for text, _ in lines
        ]

        hard_tokens = (
            "al estilo de",
            "karaoke",
            "www.",
            "whatsapp",
            "producciones",
            "ediciones",
            "copyright",
        )
        return any(
            token in line
            for line in lows
            for token in hard_tokens
        )

    def _ocr(
        self,
        mask: np.ndarray,
        scale: int = 6,
        lang: str = "spa+eng",
        psm: int = 6,
    ) -> list[tuple[str, float]]:
        prepared = self._prepare_for_ocr(mask, scale=scale)

        # La producción CDG usa mayormente mayúsculas. Restringir símbolos
        # evita lecturas imposibles como ¥ en lugar de Y.
        whitelist = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "ÁÉÍÓÚÜÑ"
            "0123456789"
            " ,.!?¿¡:-'"
        )
        config = (
            f'--oem 3 --psm {psm} '
            f'-c tessedit_char_whitelist="{whitelist}" '
            '-c preserve_interword_spaces=1'
        )

        try:
            data = pytesseract.image_to_data(
                prepared,
                lang=lang,
                config=config,
                output_type=pytesseract.Output.DICT,
            )
        except pytesseract.TesseractError:
            data = pytesseract.image_to_data(
                prepared,
                lang="eng",
                config=config,
                output_type=pytesseract.Output.DICT,
            )

        groups: dict[
            tuple[int, int, int],
            list[str],
        ] = {}
        confidences: dict[
            tuple[int, int, int],
            list[float],
        ] = {}

        for i, raw_word in enumerate(
            data.get("text", [])
        ):
            word = (raw_word or "").strip()
            if not word:
                continue

            try:
                confidence = float(
                    data["conf"][i]
                )
            except (TypeError, ValueError):
                confidence = -1.0

            if confidence < 0:
                continue

            key = (
                int(data["block_num"][i]),
                int(data["par_num"][i]),
                int(data["line_num"][i]),
            )
            groups.setdefault(
                key,
                [],
            ).append(word)
            confidences.setdefault(
                key,
                [],
            ).append(confidence)

        lines: list[tuple[str, float]] = []

        for key in sorted(groups):
            line = self._valid_line(
                " ".join(groups[key])
            )
            if not line:
                continue

            values = confidences.get(
                key,
                [],
            )
            confidence = (
                sum(values) / len(values)
                if values
                else 0.0
            )

            if confidence >= self.min_line_confidence:
                lines.append(
                    (
                        line,
                        round(confidence, 2),
                    )
                )

        return lines

    @staticmethod
    def _average_confidence(
        lines: list[tuple[str, float]],
    ) -> float:
        if not lines:
            return 0.0
        return sum(
            confidence
            for _, confidence in lines
        ) / len(lines)

    def _best_ocr(
        self,
        indices: np.ndarray,
        background_index: int,
    ) -> tuple[
        str,
        list[tuple[str, float]],
        list[dict],
    ]:
        # La página completa es siempre la primera opción. En la prueba real
        # el error grande vino de permitir que ganara una máscara de un solo
        # color (solo amarillo o solo blanco).
        full_mask = self._full_shape_mask(
            indices,
            background_index,
        )
        full_lines = self._ocr(
            full_mask,
            scale=6,
        )
        full_avg = self._average_confidence(
            full_lines
        )
        attempts = [
            {
                "mask": "all_non_background",
                "average_confidence": round(
                    full_avg,
                    2,
                ),
                "lines": len(full_lines),
            }
        ]

        if full_avg >= 72.0 and full_lines:
            return (
                "all_non_background",
                full_lines,
                attempts,
            )

        best_name = "all_non_background"
        best_lines = full_lines
        best_score = (
            full_avg
            + min(len(full_lines), 6) * 2.5
        )

        for name, mask in self._candidate_masks(
            indices,
            background_index,
        ):
            if name == "all_non_background":
                continue

            lines = self._ocr(
                mask,
                scale=6,
            )
            avg = self._average_confidence(
                lines
            )
            score = (
                avg
                + min(len(lines), 6) * 2.5
            )

            attempts.append(
                {
                    "mask": name,
                    "average_confidence": round(
                        avg,
                        2,
                    ),
                    "lines": len(lines),
                }
            )

            # Una máscara parcial debe mejorar claramente la lectura.
            if score > best_score + 8.0:
                best_score = score
                best_name = name
                best_lines = lines

        return (
            best_name,
            best_lines,
            attempts,
        )

    @staticmethod
    def _comparison_key(
        value: str,
    ) -> str:
        value = unicodedata.normalize(
            "NFKD",
            value,
        )
        value = "".join(
            ch
            for ch in value
            if not unicodedata.combining(ch)
        )
        value = re.sub(
            r"\W+",
            "",
            value,
            flags=re.UNICODE,
        ).casefold()
        return re.sub(
            r"(.)\1{2,}",
            r"\1",
            value,
        )

    @classmethod
    def _same_line(
        cls,
        a: str,
        b: str,
    ) -> bool:
        a2 = cls._comparison_key(a)
        b2 = cls._comparison_key(b)

        return bool(
            a2
            and b2
            and fuzz.ratio(a2, b2) >= 88
        )

    @classmethod
    def _screen_overlap(
        cls,
        previous: list[str],
        current: list[str],
    ) -> float:
        if not previous or not current:
            return 0.0

        matched = 0
        for line in current:
            if any(
                cls._same_line(line, old)
                for old in previous
            ):
                matched += 1

        return (
            matched
            / max(
                1,
                min(
                    len(previous),
                    len(current),
                ),
            )
        )

    def _ocr_native_page(
        self,
        indices: np.ndarray,
        background_index: int,
    ) -> tuple[
        list[tuple[str, float]],
        int,
        str,
        int,
    ]:
        mask = self._full_shape_mask(
            indices,
            background_index,
        )
        occupied = (
            float(np.count_nonzero(mask))
            / float(mask.size)
        )
        if occupied < self.min_occupied:
            return [], 6, "empty", 0

        candidates: list[
            tuple[
                float,
                list[tuple[str, float]],
                int,
                str,
            ]
        ] = []

        def add_candidate(
            *,
            scale: int,
            lang: str,
            psm: int,
        ) -> None:
            lines = self._ocr(
                mask,
                scale=scale,
                lang=lang,
                psm=psm,
            )
            avg = self._average_confidence(
                lines
            )

            # La cantidad de líneas importa ligeramente: una página que
            # recupera una línea extra con confianza similar suele ser mejor.
            score = (
                avg
                + min(len(lines), 6) * 1.25
            )
            profile = (
                f"{lang}/psm{psm}/x{scale}"
            )
            candidates.append(
                (
                    score,
                    lines,
                    scale,
                    profile,
                )
            )

        # Lectura principal: una sola llamada en la mayoría de páginas.
        add_candidate(
            scale=6,
            lang="spa+eng",
            psm=6,
        )

        primary_lines = candidates[0][1]
        primary_avg = self._average_confidence(
            primary_lines
        )

        # Solo las páginas realmente dudosas pagan el coste de OCR adicional.
        # En el CDG de prueba esto ayuda especialmente a páginas donde PSM11
        # recupera líneas que PSM6 agrupa o pierde.
        if primary_avg < 82.0:
            add_candidate(
                scale=6,
                lang="spa+eng",
                psm=11,
            )
            add_candidate(
                scale=6,
                lang="spa",
                psm=6,
            )
            add_candidate(
                scale=6,
                lang="spa",
                psm=11,
            )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )
        (
            _,
            best_lines,
            best_scale,
            best_profile,
        ) = candidates[0]

        return (
            best_lines,
            best_scale,
            best_profile,
            len(candidates),
        )

    def _native_page_snapshots(
        self,
        data: bytes,
    ) -> list[dict]:
        total_packets = len(data) // 24
        decoder = CDGDecoder()

        pages: list[dict] = []
        page_start = 0.0
        last_boundary = -999.0

        for i in range(total_packets):
            packet = data[
                i * 24 : (i + 1) * 24
            ]
            info = decoder.inspect_packet(
                packet
            )
            second = (
                i / PACKETS_PER_SECOND
            )

            is_boundary = (
                info is not None
                and info.is_memory_preset
                and (
                    info.memory_repeat or 0
                ) == 0
                and (
                    second - last_boundary
                ) >= 0.50
            )

            # IMPORTANTE: capturamos ANTES de procesar el borrado.
            if is_boundary:
                page_duration = (
                    second - page_start
                )

                if page_duration >= 0.60:
                    pages.append(
                        {
                            "start": page_start,
                            "end": second,
                            "indices": np.asarray(
                                decoder.visible_indices(),
                                dtype=np.uint8,
                            ),
                            "background_index": (
                                decoder.memory_color
                                & 0x0F
                            ),
                        }
                    )

                page_start = second
                last_boundary = second

            decoder.packet(packet)

        final_second = (
            total_packets
            / PACKETS_PER_SECOND
        )

        if (
            final_second - page_start
        ) >= 0.60:
            pages.append(
                {
                    "start": page_start,
                    "end": final_second,
                    "indices": np.asarray(
                        decoder.visible_indices(),
                        dtype=np.uint8,
                    ),
                    "background_index": (
                        decoder.memory_color
                        & 0x0F
                    ),
                }
            )

        return pages

    def _extract_native_pages(
        self,
        path: Path,
        data: bytes,
        duration: float,
    ) -> dict | None:
        pages = self._native_page_snapshots(
            data
        )

        # Con pocas páginas nativas no confiamos en esta estrategia.
        if len(pages) < 4:
            return None

        final_lines: list[dict] = []
        raw_screens: list[dict] = []
        accepted_pages = 0
        frames_ocr = 0
        ocr_fallback_pages = 0

        for (
            page_index,
            page,
        ) in enumerate(
            pages,
            start=1,
        ):
            (
                lines,
                scale,
                ocr_profile,
                ocr_calls,
            ) = self._ocr_native_page(
                page["indices"],
                page["background_index"],
            )
            frames_ocr += ocr_calls
            if ocr_calls > 1:
                ocr_fallback_pages += 1

            avg = self._average_confidence(
                lines
            )
            rejected_as_metadata = (
                self._is_metadata_screen(
                    lines
                )
            )

            # Portada inicial de baja confianza.
            if (
                page["end"] <= 10.0
                and avg < 70.0
            ):
                rejected_as_metadata = True

            cleaned = [
                (text, confidence)
                for text, confidence in lines
                if not self._looks_like_non_lyric(
                    text
                )
            ]

            raw_screens.append(
                {
                    "time": round(
                        page["start"],
                        3,
                    ),
                    "end": round(
                        page["end"],
                        3,
                    ),
                    "page": page_index,
                    "ocr_scale": scale,
                    "ocr_profile": ocr_profile,
                    "average_confidence": round(
                        avg,
                        2,
                    ),
                    "rejected_as_metadata": (
                        rejected_as_metadata
                    ),
                    "lines": [
                        {
                            "text": text,
                            "confidence": confidence,
                        }
                        for (
                            text,
                            confidence,
                        ) in lines
                    ],
                }
            )

            if (
                rejected_as_metadata
                or not cleaned
            ):
                continue

            accepted_pages += 1

            for (
                text,
                confidence,
            ) in cleaned:
                final_lines.append(
                    {
                        "time": round(
                            page["start"],
                            3,
                        ),
                        "best_time": round(
                            page["start"],
                            3,
                        ),
                        "page": page_index,
                        "text": text,
                        "confidence": confidence,
                    }
                )

        finalized = self._finalize_lines(
            final_lines
        )

        return {
            "filename": path.name,
            "engine_version": "0.6.0",
            "strategy": (
                "native_page_adaptive_ocr"
            ),
            "duration_seconds": round(
                duration,
                2,
            ),
            "frames_sampled": len(pages),
            "frames_ocr": frames_ocr,
            "ocr_fallback_pages": ocr_fallback_pages,
            "page_events": [
                {
                    "time": round(
                        page["start"],
                        3,
                    ),
                    "page": index,
                    "type": (
                        "memory_preset_page"
                    ),
                }
                for (
                    index,
                    page,
                ) in enumerate(
                    pages,
                    start=1,
                )
            ],
            "pages_detected": len(pages),
            "pages_with_lyrics": (
                accepted_pages
            ),
            "lines_detected": finalized[
                "lines_detected"
            ],
            "average_confidence": finalized[
                "average_confidence"
            ],
            "quality": finalized[
                "quality"
            ],
            "lyrics": finalized[
                "lyrics"
            ],
            "lyrics_raw": finalized[
                "lyrics_raw"
            ],
            "lrc": finalized[
                "lrc"
            ],
            "lines": finalized[
                "lines"
            ],
            "corrections_count": finalized[
                "corrections_count"
            ],
            "corrections": finalized[
                "corrections"
            ],
            "raw_screens": raw_screens,
        }

    def _extract_fallback(
        self,
        path: Path,
        data: bytes,
        duration: float,
    ) -> dict:
        total_packets = len(data) // 24

        decoder = CDGDecoder()
        sample_packets = max(
            1,
            int(
                self.sample_interval
                * PACKETS_PER_SECOND
            ),
        )

        previous_mask: np.ndarray | None = None
        accepted_mask: np.ndarray | None = None

        dirty = False
        low_screen_samples = 0
        last_candidate_time = -999.0
        last_native_clear = -999.0

        page = 0
        page_events: list[dict] = []
        previous_screen_lines: list[str] = []

        candidates: list[
            OCRLineCandidate
        ] = []
        raw_screens: list[dict] = []

        frames_sampled = 0
        frames_ocr = 0

        for i in range(total_packets):
            packet = data[
                i * 24 : (i + 1) * 24
            ]

            info = decoder.inspect_packet(
                packet
            )
            second_exact = (
                (i + 1)
                / PACKETS_PER_SECOND
            )

            if (
                info
                and info.is_memory_preset
            ):
                repeat = (
                    info.memory_repeat
                    or 0
                )

                if (
                    repeat == 0
                    and second_exact
                    >= self.ignore_first_seconds
                    and (
                        second_exact
                        - last_native_clear
                    ) >= 0.75
                ):
                    page = max(
                        1,
                        page + 1,
                    )
                    page_events.append(
                        {
                            "time": round(
                                second_exact,
                                3,
                            ),
                            "page": page,
                            "type": "memory_preset",
                        }
                    )
                    accepted_mask = None
                    previous_mask = None
                    previous_screen_lines = []
                    dirty = False
                    low_screen_samples = 0
                    last_native_clear = (
                        second_exact
                    )

            decoder.packet(packet)

            if (
                (i + 1)
                % sample_packets
            ):
                continue

            second = second_exact
            indices = np.asarray(
                decoder.visible_indices(),
                dtype=np.uint8,
            )

            mask = self._full_shape_mask(
                indices,
                decoder.memory_color & 0x0F,
            )
            frames_sampled += 1

            change = self._visual_change(
                previous_mask,
                mask,
            )
            previous_mask = mask

            if (
                second
                < self.ignore_first_seconds
            ):
                continue

            occupied = (
                float(
                    np.count_nonzero(mask)
                )
                / float(mask.size)
            )

            if occupied < self.min_occupied:
                low_screen_samples += 1

                if low_screen_samples >= 2:
                    accepted_mask = (
                        np.zeros_like(mask)
                    )
                    previous_screen_lines = []
                    dirty = False

                continue

            low_screen_samples = 0

            if change >= self.min_motion:
                dirty = True
                continue

            if (
                change
                >= self.stable_threshold
                or not dirty
            ):
                continue

            if (
                second
                - last_candidate_time
                < self.candidate_cooldown
            ):
                continue

            if accepted_mask is None:
                added_ratio = occupied
                removed_ratio = 0.0
            else:
                added_ratio = (
                    float(
                        np.count_nonzero(
                            (mask > 0)
                            & (
                                accepted_mask
                                == 0
                            )
                        )
                    )
                    / float(mask.size)
                )
                removed_ratio = (
                    float(
                        np.count_nonzero(
                            (mask == 0)
                            & (
                                accepted_mask
                                > 0
                            )
                        )
                    )
                    / float(mask.size)
                )

            if (
                accepted_mask is not None
                and added_ratio
                < self.min_added_pixels
            ):
                dirty = False
                continue

            (
                selected_mask,
                ocr_lines,
                attempts,
            ) = self._best_ocr(
                indices,
                decoder.memory_color & 0x0F,
            )
            frames_ocr += 1

            rejected_as_metadata = (
                self._is_metadata_screen(
                    ocr_lines
                )
            )

            current_texts = [
                text
                for text, _ in ocr_lines
                if not self._looks_like_non_lyric(
                    text
                )
            ]

            inferred_new_page = False
            overlap = 0.0

            if current_texts:
                if page == 0:
                    page = 1
                    page_events.append(
                        {
                            "time": round(
                                second,
                                3,
                            ),
                            "page": page,
                            "type": "first_lyrics",
                        }
                    )

                overlap = self._screen_overlap(
                    previous_screen_lines,
                    current_texts,
                )

                # Si no hubo Clear Screen pero cambió casi todo el texto,
                # inferimos nueva página por reescritura de tiles.
                if (
                    previous_screen_lines
                    and overlap < 0.20
                    and len(current_texts) >= 2
                    and (
                        second
                        - last_native_clear
                    ) > 1.0
                ):
                    page += 1
                    inferred_new_page = True
                    page_events.append(
                        {
                            "time": round(
                                second,
                                3,
                            ),
                            "page": page,
                            "type": "inferred_tile_rewrite",
                        }
                    )

            raw_screens.append(
                {
                    "time": round(
                        second,
                        3,
                    ),
                    "page": page,
                    "occupied_ratio": round(
                        occupied,
                        4,
                    ),
                    "added_ratio": round(
                        added_ratio,
                        4,
                    ),
                    "removed_ratio": round(
                        removed_ratio,
                        4,
                    ),
                    "selected_mask": (
                        selected_mask
                    ),
                    "mask_attempts": attempts,
                    "screen_overlap": round(
                        overlap,
                        3,
                    ),
                    "inferred_new_page": (
                        inferred_new_page
                    ),
                    "rejected_as_metadata": (
                        rejected_as_metadata
                    ),
                    "lines": [
                        {
                            "text": text,
                            "confidence": confidence,
                        }
                        for (
                            text,
                            confidence,
                        ) in ocr_lines
                    ],
                }
            )

            if not rejected_as_metadata:
                for (
                    text,
                    confidence,
                ) in ocr_lines:
                    if self._looks_like_non_lyric(
                        text
                    ):
                        continue

                    candidates.append(
                        OCRLineCandidate(
                            time=round(
                                second,
                                3,
                            ),
                            text=text,
                            confidence=confidence,
                            page=page,
                        )
                    )

            if current_texts:
                previous_screen_lines = (
                    current_texts
                )

            accepted_mask = mask.copy()
            last_candidate_time = second
            dirty = False

        recent: deque[
            tuple[float, int]
        ] = deque()
        final_lines: list[dict] = []

        for candidate in candidates:
            while (
                recent
                and candidate.time
                - recent[0][0]
                > self.duplicate_window
            ):
                recent.popleft()

            duplicate_index: int | None = None

            for _, index in recent:
                if self._same_line(
                    candidate.text,
                    final_lines[index]["text"],
                ):
                    duplicate_index = index
                    break

            if duplicate_index is not None:
                current = final_lines[
                    duplicate_index
                ]

                if (
                    candidate.confidence
                    > current["confidence"] + 1.0
                ):
                    current["text"] = (
                        candidate.text
                    )
                    current["confidence"] = (
                        candidate.confidence
                    )
                    current["best_time"] = (
                        candidate.time
                    )
                    current["page"] = (
                        candidate.page
                    )
                continue

            final_lines.append(
                {
                    "time": candidate.time,
                    "best_time": (
                        candidate.time
                    ),
                    "page": candidate.page,
                    "text": candidate.text,
                    "confidence": (
                        candidate.confidence
                    ),
                }
            )
            recent.append(
                (
                    candidate.time,
                    len(final_lines) - 1,
                )
            )

        finalized = self._finalize_lines(
            final_lines
        )

        return {
            "filename": path.name,
            "engine_version": "0.6.0",
            "strategy": "stable_frame_fallback",
            "duration_seconds": round(
                duration,
                2,
            ),
            "frames_sampled": frames_sampled,
            "frames_ocr": frames_ocr,
            "page_events": page_events,
            "pages_detected": max(
                page,
                len(
                    {
                        event["page"]
                        for event
                        in page_events
                    }
                ),
            ),
            "lines_detected": finalized[
                "lines_detected"
            ],
            "average_confidence": finalized[
                "average_confidence"
            ],
            "quality": finalized[
                "quality"
            ],
            "lyrics": finalized[
                "lyrics"
            ],
            "lyrics_raw": finalized[
                "lyrics_raw"
            ],
            "lrc": finalized[
                "lrc"
            ],
            "lines": finalized[
                "lines"
            ],
            "corrections_count": finalized[
                "corrections_count"
            ],
            "corrections": finalized[
                "corrections"
            ],
            "raw_screens": raw_screens,
        }


    @staticmethod
    def _max_event_gap(
        result: dict,
    ) -> float:
        duration = float(
            result.get("duration_seconds", 0.0)
            or 0.0
        )
        events = sorted(
            float(item.get("time", 0.0))
            for item in result.get("page_events", [])
            if isinstance(item, dict)
        )

        if not events:
            return duration

        gaps: list[float] = []
        previous = 0.0

        for value in events:
            if value > previous:
                gaps.append(value - previous)
            previous = value

        if duration > previous:
            gaps.append(duration - previous)

        return max(gaps) if gaps else 0.0

    @classmethod
    def _coverage_metrics(
        cls,
        result: dict,
    ) -> dict:
        duration = float(
            result.get("duration_seconds", 0.0)
            or 0.0
        )
        pages = int(
            result.get("pages_detected", 0)
            or 0
        )
        lines = int(
            result.get("lines_detected", 0)
            or 0
        )
        confidence = float(
            result.get("average_confidence", 0.0)
            or 0.0
        )
        minutes = duration / 60.0
        pages_per_minute = (
            pages / minutes
            if minutes > 0
            else 0.0
        )
        max_gap = cls._max_event_gap(result)

        return {
            "pages": pages,
            "lines": lines,
            "confidence": round(confidence, 2),
            "pages_per_minute": round(
                pages_per_minute,
                2,
            ),
            "max_page_gap": round(max_gap, 2),
        }

    @classmethod
    def _native_needs_hybrid(
        cls,
        result: dict,
    ) -> bool:
        metrics = cls._coverage_metrics(result)
        duration = float(
            result.get("duration_seconds", 0.0)
            or 0.0
        )

        if duration < 90.0:
            return False

        return (
            metrics["pages_per_minute"] < 3.0
            or metrics["max_page_gap"] >= 30.0
        )

    @staticmethod
    def _coverage_score(
        metrics: dict,
    ) -> float:
        confidence = float(
            metrics.get("confidence", 0.0)
            or 0.0
        )
        lines = int(
            metrics.get("lines", 0)
            or 0
        )
        pages_per_minute = float(
            metrics.get("pages_per_minute", 0.0)
            or 0.0
        )
        max_gap = float(
            metrics.get("max_page_gap", 0.0)
            or 0.0
        )

        score = confidence
        score += min(lines, 80) * 0.12
        score += min(pages_per_minute, 8.0) * 1.5

        if max_gap >= 45.0:
            score -= 12.0
        elif max_gap >= 30.0:
            score -= 8.0
        elif max_gap >= 20.0:
            score -= 3.0

        return round(score, 2)

    @classmethod
    def _select_hybrid_result(
        cls,
        native: dict,
        fallback: dict,
    ) -> dict:
        native_metrics = cls._coverage_metrics(
            native
        )
        fallback_metrics = cls._coverage_metrics(
            fallback
        )
        native_score = cls._coverage_score(
            native_metrics
        )
        fallback_score = cls._coverage_score(
            fallback_metrics
        )

        native_lines = int(
            native_metrics["lines"]
        )
        fallback_lines = int(
            fallback_metrics["lines"]
        )
        native_conf = float(
            native_metrics["confidence"]
        )
        fallback_conf = float(
            fallback_metrics["confidence"]
        )

        fallback_has_coverage_gain = (
            fallback_lines
            >= max(
                native_lines + 5,
                int(native_lines * 1.15),
            )
            and fallback_conf
            >= native_conf - 8.0
        )

        fallback_has_page_gain = (
            fallback_metrics["pages"]
            >= max(
                native_metrics["pages"] + 3,
                int(
                    native_metrics["pages"]
                    * 1.35
                ),
            )
            and fallback_lines
            >= int(native_lines * 0.95)
            and fallback_conf
            >= native_conf - 6.0
        )

        choose_fallback = (
            fallback_score
            >= native_score + 3.0
            and (
                fallback_has_coverage_gain
                or fallback_has_page_gain
            )
        )

        chosen = (
            fallback
            if choose_fallback
            else native
        )
        chosen = dict(chosen)
        chosen["strategy"] = (
            "hybrid_fallback_selected"
            if choose_fallback
            else "hybrid_native_selected"
        )
        chosen["hybrid_trigger"] = (
            "sparse_native_pages"
        )
        chosen["strategy_candidates"] = {
            "native": {
                **native_metrics,
                "selection_score": native_score,
            },
            "fallback": {
                **fallback_metrics,
                "selection_score": fallback_score,
            },
        }
        return chosen

    def extract(
        self,
        cdg_path: str | Path,
    ) -> dict:
        path = Path(cdg_path)
        data = path.read_bytes()

        total_packets = len(data) // 24
        duration = (
            total_packets
            / PACKETS_PER_SECOND
        )

        native = self._extract_native_pages(
            path,
            data,
            duration,
        )

        if native is None:
            fallback = self._extract_fallback(
                path,
                data,
                duration,
            )
            fallback["engine_version"] = "0.9.0"
            return fallback

        if not self._native_needs_hybrid(
            native
        ):
            native["engine_version"] = "0.9.0"
            native["hybrid_trigger"] = None
            native["strategy_candidates"] = {
                "native": {
                    **self._coverage_metrics(
                        native
                    ),
                    "selection_score": (
                        self._coverage_score(
                            self._coverage_metrics(
                                native
                            )
                        )
                    ),
                }
            }
            return native

        fallback = self._extract_fallback(
            path,
            data,
            duration,
        )
        selected = self._select_hybrid_result(
            native,
            fallback,
        )
        selected["engine_version"] = "0.9.0"
        return selected
