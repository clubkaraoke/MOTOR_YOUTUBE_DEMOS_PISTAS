from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path


TOKEN_RE = re.compile(
    r"[A-ZÁÉÍÓÚÜÑ0-9]+(?:'[A-ZÁÉÍÓÚÜÑ0-9]+)?",
    re.IGNORECASE,
)

FUNCTION_WORDS = {
    "A", "AL", "DE", "DEL", "EL", "EN", "ES", "LA", "LAS",
    "LE", "LO", "LOS", "ME", "MI", "NO", "O", "PA", "PARA",
    "POR", "QUE", "SE", "SI", "SIN", "SU", "TE", "TU", "UN",
    "UNA", "Y", "YO",
}

SINGLE_WORDS = {"A", "Y", "O", "E"}

# Confusiones observadas de forma repetitiva en fuentes CDG pixeladas.
# La sustitución solo se acepta si el resultado está respaldado por el
# diccionario de frecuencia y es claramente más probable.
CONFUSIONS = {
    "G": ("C",),
    "C": ("G",),
    "H": ("N",),
    "N": ("H", "M"),
    "M": ("N",),
}

DIGIT_TO_LETTER = {
    "0": "O",
    "1": "I",
    "3": "S",
    "5": "S",
}

ENGLISH_WORDS = {
    "A", "ALL", "AND", "BODY", "BRAND", "CAN", "DO", "ENDLESS",
    "EVERYBODY", "FEEL", "IS", "LINE", "MAKE", "NEW", "NO", "NOTHING",
    "OH", "SAY", "SO", "THE", "THERE", "THIS", "TIME", "WE", "WILL",
    "YOU", "YOUR",
}


class TextCorrector:
    """Corrección post-OCR conservadora para letras CDG.

    Reglas:
    - Nunca consulta una letra externa.
    - Conserva siempre el texto OCR crudo en el resultado del extractor.
    - Usa frecuencia de palabras solo para decidir entre confusiones gráficas
      plausibles y para separar palabras pegadas.
    - Si la mejora no es clara, deja el texto original.
    """

    def __init__(
        self,
        frequency_path: str | Path | None = None,
        frequencies: dict[str, int] | None = None,
    ) -> None:
        if frequencies is not None:
            self.freq = {
                str(word).casefold(): int(count)
                for word, count in frequencies.items()
            }
        else:
            if frequency_path is None:
                frequency_path = (
                    Path(__file__).resolve().parents[1]
                    / "data"
                    / "es_50k.txt"
                )
            self.freq = self._load_frequency_file(
                Path(frequency_path)
            )

    @staticmethod
    def _load_frequency_file(
        path: Path,
    ) -> dict[str, int]:
        result: dict[str, int] = {}

        if not path.exists():
            return result

        for raw in path.read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines():
            raw = raw.strip()
            if not raw:
                continue

            try:
                word, count = raw.rsplit(" ", 1)
                result[word.casefold()] = int(count)
            except (ValueError, TypeError):
                continue

        return result

    def frequency(
        self,
        word: str,
    ) -> int:
        return self.freq.get(
            word.casefold(),
            0,
        )

    @staticmethod
    def _restore_case(
        source: str,
        corrected: str,
    ) -> str:
        if source.isupper():
            return corrected.upper()
        if source.istitle():
            return corrected.title()
        return corrected

    @staticmethod
    def _normalize_digits(
        token: str,
    ) -> str:
        # Solo cambiamos dígitos si están mezclados con letras.
        # Un token puramente numérico se conserva.
        if not any(ch.isalpha() for ch in token):
            return token

        return "".join(
            DIGIT_TO_LETTER.get(ch, ch)
            for ch in token
        )

    @staticmethod
    def _variants(
        token: str,
        max_changes: int = 2,
    ) -> set[tuple[str, int]]:
        found: set[tuple[str, int]] = {
            (token, 0)
        }
        frontier = {(token, 0)}

        for _ in range(max_changes):
            new_frontier: set[
                tuple[str, int]
            ] = set()

            for current, changes in frontier:
                chars = list(current)

                for index, char in enumerate(chars):
                    for replacement in CONFUSIONS.get(
                        char.upper(),
                        (),
                    ):
                        replacement = (
                            replacement
                            if char.isupper()
                            else replacement.lower()
                        )
                        updated = (
                            current[:index]
                            + replacement
                            + current[index + 1 :]
                        )
                        item = (
                            updated,
                            changes + 1,
                        )

                        if item not in found:
                            found.add(item)
                            new_frontier.add(item)

            frontier = new_frontier

            if not frontier:
                break

        return found

    def _best_dictionary_word(
        self,
        token: str,
        *,
        segment_mode: bool = False,
    ) -> tuple[str, int, int]:
        normalized = self._normalize_digits(
            token
        )
        original_frequency = self.frequency(
            normalized
        )

        best_word = normalized
        best_frequency = original_frequency
        best_changes = (
            0
            if normalized == token
            else 1
        )
        best_score = (
            math.log10(
                original_frequency + 1
            )
            - best_changes * 0.35
        )

        for candidate, changes in self._variants(
            normalized,
            max_changes=2,
        ):
            frequency = self.frequency(
                candidate
            )
            if frequency <= 0:
                continue

            total_changes = (
                changes
                + (
                    0
                    if normalized == token
                    else 1
                )
            )
            score = (
                math.log10(frequency + 1)
                - total_changes * 0.35
            )

            if score > best_score:
                best_word = candidate
                best_frequency = frequency
                best_changes = total_changes
                best_score = score

        if best_word == normalized:
            return (
                normalized,
                original_frequency,
                best_changes,
            )

        # Para segmentos internos basta que sean palabras reales frecuentes;
        # el algoritmo de segmentación vuelve a evaluar el conjunto completo.
        if segment_mode:
            if best_frequency >= 250:
                return (
                    best_word,
                    best_frequency,
                    best_changes,
                )
            return (
                normalized,
                original_frequency,
                0,
            )

        # Palabra OCR inexistente -> aceptamos una alternativa razonablemente
        # frecuente. Esto cubre GON->CON, DIGE->DICE, MUGHAGHITA->MUCHACHITA.
        if original_frequency == 0:
            if best_frequency >= 300:
                return (
                    best_word,
                    best_frequency,
                    best_changes,
                )
            return (
                normalized,
                original_frequency,
                0,
            )

        # Si la lectura original ya es una palabra real, exigimos una mejora
        # muy grande para no convertir LOGO en LOCO, por ejemplo.
        if (
            best_frequency
            >= max(
                2000,
                original_frequency * 25,
            )
        ):
            return (
                best_word,
                best_frequency,
                best_changes,
            )

        return (
            normalized,
            original_frequency,
            0,
        )

    @lru_cache(maxsize=20000)
    def _segment(
        self,
        token: str,
    ) -> tuple[str, ...] | None:
        token = self._normalize_digits(
            token.upper()
        )
        n = len(token)

        if n < 4 or n > 28:
            return None

        original_frequency = self.frequency(
            token
        )

        # Una palabra española ya reconocida con frecuencia razonable no se
        # separa automáticamente. Evita ALA -> A LA cuando realmente es "ala".
        if original_frequency >= 300:
            return None

        candidates: list[
            tuple[float, tuple[str, ...], tuple[int, ...]]
        ] = []

        def walk(
            start: int,
            parts: list[str],
            freqs: list[int],
        ) -> None:
            if len(parts) > 4:
                return

            if start == n:
                if len(parts) < 2:
                    return

                has_function = any(
                    part in FUNCTION_WORDS
                    for part in parts
                )
                all_strong = all(
                    frequency >= 5000
                    for frequency in freqs
                )
                long_and_valid = (
                    n >= 9
                    and all(
                        frequency >= 500
                        for frequency in freqs
                    )
                )

                if not (
                    has_function
                    or all_strong
                    or long_and_valid
                ):
                    return

                # Frecuencia media: favorece palabras reales sin castigar
                # excesivamente una frase de 3-4 palabras pegadas.
                score = sum(
                    math.log10(
                        frequency + 1
                    )
                    for frequency in freqs
                ) / len(freqs)

                # Leve preferencia por menos fragmentos.
                score -= (
                    len(parts) - 1
                ) * 0.18

                candidates.append(
                    (
                        score,
                        tuple(parts),
                        tuple(freqs),
                    )
                )
                return

            remaining = n - start

            for end in range(
                start + 1,
                n + 1,
            ):
                raw_piece = token[start:end]

                if (
                    len(raw_piece) == 1
                    and raw_piece
                    not in SINGLE_WORDS
                ):
                    continue

                if (
                    len(raw_piece) > 1
                    and len(raw_piece) < 2
                ):
                    continue

                (
                    corrected_piece,
                    frequency,
                    _,
                ) = self._best_dictionary_word(
                    raw_piece,
                    segment_mode=True,
                )

                if frequency < 250:
                    continue

                # No dejamos una última pieza de una sola letra que no sea
                # palabra funcional.
                if (
                    end == n
                    and len(corrected_piece) == 1
                    and corrected_piece
                    not in SINGLE_WORDS
                ):
                    continue

                parts.append(
                    corrected_piece.upper()
                )
                freqs.append(
                    frequency
                )
                walk(
                    end,
                    parts,
                    freqs,
                )
                parts.pop()
                freqs.pop()

        walk(
            0,
            [],
            [],
        )

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best_parts, _ = (
            candidates[0]
        )

        # Un desconocido recibe score base 0. La segmentación debe tener
        # una calidad lingüística mínima para aceptarse.
        if best_score < 2.7:
            return None

        return best_parts

    def correct_token(
        self,
        token: str,
    ) -> tuple[str, str | None]:
        if not token:
            return token, None

        normalized = self._normalize_digits(
            token
        )

        # Primero intentamos corregir el token completo. Esto evita que una
        # palabra pixelada como MUGHAGHITA se fragmente si MUCHACHITA existe
        # claramente en el diccionario.
        (
            corrected_word,
            _,
            _,
        ) = self._best_dictionary_word(
            normalized,
            segment_mode=False,
        )

        corrected_word = (
            self._restore_case(
                token,
                corrected_word,
            )
        )

        if corrected_word != token:
            return (
                corrected_word,
                "dictionary",
            )

        # Si no existe una corrección completa clara, recién intentamos
        # separar palabras pegadas: TEVEO -> TE VEO, YSIN -> Y SIN, etc.
        segmentation = self._segment(
            normalized.upper()
        )

        if segmentation is not None:
            corrected = " ".join(
                segmentation
            )
            corrected = self._restore_case(
                token,
                corrected,
            )
            if corrected != token:
                return (
                    corrected,
                    "split",
                )

        return token, None

    def correct_line(
        self,
        line: str,
    ) -> tuple[str, list[dict]]:
        changes: list[dict] = []

        def replace(
            match: re.Match[str],
        ) -> str:
            original = match.group(0)
            corrected, kind = (
                self.correct_token(
                    original
                )
            )

            if kind and corrected != original:
                changes.append(
                    {
                        "from": original,
                        "to": corrected,
                        "type": kind,
                    }
                )

            return corrected

        corrected_line = TOKEN_RE.sub(
            replace,
            line,
        )

        corrected_line = re.sub(
            r"[ \t]+",
            " ",
            corrected_line,
        ).strip()

        return (
            corrected_line,
            changes,
        )

    def correct_lines(
        self,
        lines: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        corrected_lines: list[dict] = []
        all_changes: list[dict] = []

        for index, item in enumerate(
            lines
        ):
            raw_text = str(
                item.get(
                    "text",
                    "",
                )
            )
            corrected_text, changes = (
                self.correct_line(
                    raw_text
                )
            )

            updated = dict(item)
            updated["raw_text"] = (
                raw_text
            )
            updated["text"] = (
                corrected_text
            )
            corrected_lines.append(
                updated
            )

            for change in changes:
                all_changes.append(
                    {
                        "line_index": index,
                        **change,
                    }
                )

        return (
            corrected_lines,
            all_changes,
        )
