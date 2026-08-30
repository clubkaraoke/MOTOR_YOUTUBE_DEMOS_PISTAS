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


@dataclass
class OCRLineCandidate:
    time: float
    text: str
    confidence: float
    page: int


class CDGLyricsExtractor:
    """CDG -> páginas reales -> OCR -> letra.

    V0.4 prioriza capturar la pantalla COMPLETA justo antes de cada
    Memory Preset / Clear Screen. Así Tesseract recibe la página terminada
    (blanco + amarillo) y no estados parciales del resaltado karaoke.

    Si un CDG no trae suficientes Clear Screen, se conserva el detector
    estable/por colores de V0.3 como fallback.
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
            '--oem 3 --psm 6 '
            f'-c tessedit_char_whitelist="{whitelist}" '
            '-c preserve_interword_spaces=1'
        )

        try:
            data = pytesseract.image_to_data(
                prepared,
                lang="spa+eng",
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
            return [], 6

        primary = self._ocr(
            mask,
            scale=6,
        )
        primary_avg = self._average_confidence(
            primary
        )

        # Solo gastamos un segundo OCR si la lectura principal salió débil.
        if primary_avg >= 72.0 or len(primary) >= 3:
            return primary, 6

        fallback = self._ocr(
            mask,
            scale=5,
        )
        fallback_avg = self._average_confidence(
            fallback
        )

        if fallback_avg > primary_avg + 4.0:
            return fallback, 5

        return primary, 6

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

        for (
            page_index,
            page,
        ) in enumerate(
            pages,
            start=1,
        ):
            lines, scale = (
                self._ocr_native_page(
                    page["indices"],
                    page["background_index"],
                )
            )
            frames_ocr += 1

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

        lyrics = "\n".join(
            item["text"]
            for item in final_lines
        )

        average_confidence = (
            round(
                sum(
                    float(
                        item["confidence"]
                    )
                    for item in final_lines
                )
                / len(final_lines),
                2,
            )
            if final_lines
            else 0.0
        )

        if (
            average_confidence >= 82
            and len(final_lines) >= 8
        ):
            quality = "BUENA"
        elif (
            average_confidence >= 68
            and len(final_lines) >= 5
        ):
            quality = "REVISAR"
        else:
            quality = "MALA"

        return {
            "filename": path.name,
            "engine_version": "0.4.0",
            "strategy": (
                "native_page_final_frame"
            ),
            "duration_seconds": round(
                duration,
                2,
            ),
            "frames_sampled": len(pages),
            "frames_ocr": frames_ocr,
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
            "lines_detected": len(
                final_lines
            ),
            "average_confidence": (
                average_confidence
            ),
            "quality": quality,
            "lyrics": lyrics,
            "lrc": format_lrc(
                final_lines
            ),
            "lines": final_lines,
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

        lyrics = "\n".join(
            item["text"]
            for item in final_lines
        )

        average_confidence = (
            round(
                sum(
                    float(
                        item["confidence"]
                    )
                    for item in final_lines
                )
                / len(final_lines),
                2,
            )
            if final_lines
            else 0.0
        )

        if (
            average_confidence >= 75
            and len(final_lines) >= 6
        ):
            quality = "BUENA"
        elif (
            average_confidence >= 60
            and len(final_lines) >= 4
        ):
            quality = "REVISAR"
        else:
            quality = "MALA"

        return {
            "filename": path.name,
            "engine_version": "0.4.0",
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
            "lines_detected": len(
                final_lines
            ),
            "average_confidence": (
                average_confidence
            ),
            "quality": quality,
            "lyrics": lyrics,
            "lrc": format_lrc(
                final_lines
            ),
            "lines": final_lines,
            "raw_screens": raw_screens,
        }


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
        if native is not None:
            return native

        return self._extract_fallback(
            path,
            data,
            duration,
        )
