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
    """CDG -> pantalla estable -> máscaras CLUT -> OCR -> letra.

    V0.3 añade compatibilidad con CDG que:
    - no usan Memory Preset entre páginas;
    - conservan fondos/gráficos de varios colores;
    - reescriben letras mediante Tile Block.

    En vez de enviar "todo lo que no es fondo" a Tesseract, genera máscaras
    por índice de color CDG y elige la variante OCR con mejor calidad.
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
    ) -> np.ndarray:
        scaled = cv2.resize(
            mask,
            None,
            fx=5,
            fy=5,
            interpolation=cv2.INTER_NEAREST,
        )
        scaled = cv2.morphologyEx(
            scaled,
            cv2.MORPH_CLOSE,
            np.ones((2, 2), np.uint8),
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
    ) -> list[tuple[str, float]]:
        prepared = self._prepare_for_ocr(mask)
        config = "--oem 3 --psm 6"

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

            lines.append(
                (
                    line,
                    round(confidence, 2),
                )
            )

        return lines

    def _best_ocr(
        self,
        indices: np.ndarray,
        background_index: int,
    ) -> tuple[
        str,
        list[tuple[str, float]],
        list[dict],
    ]:
        attempts: list[dict] = []
        best_name = ""
        best_lines: list[
            tuple[str, float]
        ] = []
        best_score = -1e9

        for name, mask in self._candidate_masks(
            indices,
            background_index,
        ):
            lines = self._ocr(mask)

            strong = [
                (text, confidence)
                for text, confidence in lines
                if confidence
                >= self.min_line_confidence
            ]

            avg = (
                sum(c for _, c in strong)
                / len(strong)
                if strong
                else 0.0
            )

            # Priorizamos confianza y luego cantidad razonable de líneas.
            score = (
                avg
                + min(len(strong), 6) * 2.5
                - max(0, len(strong) - 8) * 2
            )

            attempts.append(
                {
                    "mask": name,
                    "score": round(
                        score,
                        2,
                    ),
                    "average_confidence": round(
                        avg,
                        2,
                    ),
                    "lines": len(strong),
                }
            )

            if score > best_score:
                best_score = score
                best_name = name
                best_lines = strong

        return best_name, best_lines, attempts

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
            "engine_version": "0.3.0",
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
