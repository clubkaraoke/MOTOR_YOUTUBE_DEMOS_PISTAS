from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image
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
    """CDG -> páginas nativas -> pantallas estables -> OCR -> letra.

    V0.2:
    - Detecta Memory Preset como frontera nativa de página / Clear Screen.
    - Ignora los paquetes repetidos del mismo borrado.
    - Mantiene el filtro de pantalla estable para reducir OCR innecesario.
    - Deduplica dentro de la misma página, no entre páginas diferentes.
    - Entrega TXT/JSON y LRC provisional por línea.
    """

    def __init__(
        self,
        sample_interval: float = 0.50,
        min_motion: float = 0.006,
        stable_threshold: float = 0.006,
        min_added_pixels: float = 0.012,
        min_occupied: float = 0.012,
        ignore_first_seconds: float = 5.0,
        candidate_cooldown: float = 1.5,
        duplicate_window: float = 22.0,
    ) -> None:
        self.sample_interval = sample_interval
        self.min_motion = min_motion
        self.stable_threshold = stable_threshold
        self.min_added_pixels = min_added_pixels
        self.min_occupied = min_occupied
        self.ignore_first_seconds = ignore_first_seconds
        self.candidate_cooldown = candidate_cooldown
        self.duplicate_window = duplicate_window

    @staticmethod
    def _shape_mask(
        image: Image.Image,
        bg_rgb: tuple[int, int, int],
    ) -> np.ndarray:
        arr = np.asarray(image, dtype=np.int16)
        bg = np.asarray(bg_rgb, dtype=np.int16)

        # Separamos geometría del glifo del color de highlight.
        distance = np.max(np.abs(arr - bg), axis=2)
        mask = (distance > 28).astype(np.uint8) * 255

        return cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            np.ones((2, 2), np.uint8),
        )

    @staticmethod
    def _visual_change(
        a: np.ndarray | None,
        b: np.ndarray,
    ) -> float:
        if a is None:
            return 1.0
        return float(np.count_nonzero(a != b)) / float(b.size)

    @staticmethod
    def _prepare_for_ocr(mask: np.ndarray) -> np.ndarray:
        # CDG visible = 288x192. Nearest mantiene el dibujo pixel del CDG.
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

        # Tesseract suele rendir mejor con texto negro sobre blanco.
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
    def _valid_line(cls, value: str) -> str | None:
        line = cls._normalize_line(value)
        if len(line) < 2:
            return None
        if not re.search(
            r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]",
            line,
        ):
            return None
        return line

    @staticmethod
    def _looks_like_non_lyric(line: str) -> bool:
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
        )
        return any(token in low for token in blocked)

    def _ocr(self, mask: np.ndarray) -> list[tuple[str, float]]:
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

        groups: dict[tuple[int, int, int], list[str]] = {}
        confidences: dict[
            tuple[int, int, int],
            list[float],
        ] = {}

        for i, raw_word in enumerate(data.get("text", [])):
            word = (raw_word or "").strip()
            if not word:
                continue

            try:
                confidence = float(data["conf"][i])
            except (TypeError, ValueError):
                confidence = -1.0

            if confidence < 0:
                continue

            key = (
                int(data["block_num"][i]),
                int(data["par_num"][i]),
                int(data["line_num"][i]),
            )
            groups.setdefault(key, []).append(word)
            confidences.setdefault(key, []).append(confidence)

        lines: list[tuple[str, float]] = []

        for key in sorted(groups):
            raw_line = " ".join(groups[key])
            line = self._valid_line(raw_line)
            if not line:
                continue

            values = confidences.get(key, [])
            confidence = (
                sum(values) / len(values)
                if values
                else 0.0
            )
            lines.append((line, round(confidence, 2)))

        return lines

    @staticmethod
    def _comparison_key(value: str) -> str:
        # Solo se usa para comparar duplicados; nunca reescribe la letra.
        value = unicodedata.normalize("NFKD", value)
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

        # Colapsa artefactos OCR como CORAZOOOON solo para comparación.
        return re.sub(r"(.)\1{2,}", r"\1", value)

    @classmethod
    def _same_line(cls, a: str, b: str) -> bool:
        a2 = cls._comparison_key(a)
        b2 = cls._comparison_key(b)

        return bool(
            a2
            and b2
            and fuzz.ratio(a2, b2) >= 88
        )

    def extract(self, cdg_path: str | Path) -> dict:
        path = Path(cdg_path)
        data = path.read_bytes()

        total_packets = len(data) // 24
        duration = total_packets / PACKETS_PER_SECOND

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
        last_clear_time = -999.0

        page = 0
        clear_events: list[dict] = []

        candidates: list[OCRLineCandidate] = []
        raw_screens: list[dict] = []

        frames_sampled = 0
        frames_ocr = 0

        for i in range(total_packets):
            packet = data[
                i * 24 : (i + 1) * 24
            ]

            info = decoder.inspect_packet(packet)
            second_exact = (
                (i + 1)
                / PACKETS_PER_SECOND
            )

            # Memory Preset (instruction 1) borra toda la pantalla.
            # En una secuencia repetida, repeat==0 es el evento canónico.
            if info and info.is_memory_preset:
                repeat = info.memory_repeat or 0
                canonical = repeat == 0

                # Fallback para CDG no totalmente estándar:
                # un Memory Preset aislado también cuenta como Clear Screen.
                debounced_fallback = (
                    second_exact - last_clear_time
                    >= 0.75
                )

                if canonical or debounced_fallback:
                    if (
                        second_exact
                        >= self.ignore_first_seconds
                    ):
                        page += 1
                        clear_events.append(
                            {
                                "time": round(
                                    second_exact,
                                    3,
                                ),
                                "page": page,
                                "color": (
                                    info.memory_color
                                ),
                                "repeat": repeat,
                            }
                        )

                    # La nueva página debe poder volver a aceptar
                    # geometría que ya apareció en la página anterior.
                    accepted_mask = None
                    previous_mask = None
                    dirty = False
                    low_screen_samples = 0
                    last_clear_time = second_exact

            decoder.packet(packet)

            if (i + 1) % sample_packets:
                continue

            second = second_exact

            frame = decoder.image(
                crop=True,
                scale=1,
            )
            mask = self._shape_mask(
                frame,
                decoder.background_rgb(),
            )
            frames_sampled += 1

            change = self._visual_change(
                previous_mask,
                mask,
            )
            previous_mask = mask

            if second < self.ignore_first_seconds:
                continue

            occupied = (
                float(np.count_nonzero(mask))
                / float(mask.size)
            )

            # Fallback visual para archivos que limpian con tiles
            # en lugar de Memory Preset.
            if occupied < self.min_occupied:
                low_screen_samples += 1

                if low_screen_samples >= 2:
                    accepted_mask = np.zeros_like(
                        mask
                    )
                    dirty = False

                continue

            low_screen_samples = 0

            # Esperamos estabilidad después de actividad gráfica.
            if change >= self.min_motion:
                dirty = True
                continue

            if (
                change >= self.stable_threshold
                or not dirty
            ):
                continue

            if (
                second - last_candidate_time
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
                            & (accepted_mask == 0)
                        )
                    )
                    / float(mask.size)
                )

                removed_ratio = (
                    float(
                        np.count_nonzero(
                            (mask == 0)
                            & (accepted_mask > 0)
                        )
                    )
                    / float(mask.size)
                )

            # Si solo se está coloreando/borrando texto conocido,
            # no gastamos otra llamada OCR.
            if (
                accepted_mask is not None
                and added_ratio
                < self.min_added_pixels
            ):
                dirty = False
                continue

            ocr_lines = self._ocr(mask)
            frames_ocr += 1

            promo_hits = sum(
                1
                for text, _ in ocr_lines
                if self._looks_like_non_lyric(
                    text
                )
            )
            rejected_as_promo = promo_hits >= 2

            raw_screens.append(
                {
                    "time": round(second, 3),
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
                    "rejected_as_promo": (
                        rejected_as_promo
                    ),
                    "lines": [
                        {
                            "text": text,
                            "confidence": confidence,
                        }
                        for text, confidence
                        in ocr_lines
                    ],
                }
            )

            if not rejected_as_promo:
                for text, confidence in ocr_lines:
                    candidates.append(
                        OCRLineCandidate(
                            time=round(second, 3),
                            text=text,
                            confidence=confidence,
                            page=page,
                        )
                    )

            accepted_mask = mask.copy()
            last_candidate_time = second
            dirty = False

        # Deduplicación temporal, limitada a la misma página.
        recent: deque[
            tuple[float, int, int]
        ] = deque()
        final_lines: list[dict] = []

        for candidate in candidates:
            if self._looks_like_non_lyric(
                candidate.text
            ):
                continue

            while (
                recent
                and candidate.time
                - recent[0][0]
                > self.duplicate_window
            ):
                recent.popleft()

            duplicate_index: int | None = None

            for (
                _,
                index,
                candidate_page,
            ) in recent:
                if (
                    candidate_page
                    == candidate.page
                    and self._same_line(
                        candidate.text,
                        final_lines[index]["text"],
                    )
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
                    current["text"] = candidate.text
                    current["confidence"] = (
                        candidate.confidence
                    )
                    current["best_time"] = (
                        candidate.time
                    )

                continue

            final_lines.append(
                {
                    "time": candidate.time,
                    "best_time": candidate.time,
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
                    candidate.page,
                )
            )

        lyrics = "\n".join(
            item["text"]
            for item in final_lines
        )

        average_confidence = (
            round(
                sum(
                    float(item["confidence"])
                    for item in final_lines
                )
                / len(final_lines),
                2,
            )
            if final_lines
            else 0.0
        )

        return {
            "filename": path.name,
            "duration_seconds": round(
                duration,
                2,
            ),
            "frames_sampled": frames_sampled,
            "frames_ocr": frames_ocr,
            "clear_events": clear_events,
            "pages_detected": len(clear_events),
            "lines_detected": len(final_lines),
            "average_confidence": (
                average_confidence
            ),
            "lyrics": lyrics,
            "lrc": format_lrc(final_lines),
            "lines": final_lines,
            "raw_screens": raw_screens,
        }
