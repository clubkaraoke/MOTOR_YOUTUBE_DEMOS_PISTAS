from __future__ import annotations

import math
import re
import unicodedata
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
    "N": ("H", "M", "Ñ"),
    "Ñ": ("N",),
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
    "YOU", "YOUR", "YES",
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
                # V0.7 LAB: penaliza segmentaciones excesivas. Sin esto,
                # AHORASOLA podía ganar como "A HORAS O LA" solo porque las
                # palabras funcionales son extremadamente frecuentes.
                score -= (
                    len(parts) - 1
                ) * 0.65

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

    def _function_prefix_split(
        self,
        token: str,
    ) -> tuple[str, ...] | None:
        token = self._normalize_digits(
            token.upper()
        )

        # V0.6: una lectura OCR pegada puede existir accidentalmente en el
        # corpus (DELA, AMI, etc.). Solo bloqueamos la separación cuando la
        # palabra original es realmente frecuente. ALA, por ejemplo, se
        # conserva porque es una palabra española válida y supera el umbral.
        if (
            token in ENGLISH_WORDS
            or self.frequency(token) >= 5000
        ):
            return None

        prefixes = sorted(
            FUNCTION_WORDS,
            key=len,
            reverse=True,
        )

        for prefix in prefixes:
            if len(prefix) >= len(token) - 1:
                continue
            if not token.startswith(prefix):
                continue

            remainder = token[len(prefix):]

            (
                corrected_remainder,
                remainder_frequency,
                _,
            ) = self._best_dictionary_word(
                remainder,
                segment_mode=True,
            )

            if remainder_frequency < 250:
                continue

            return (
                prefix,
                corrected_remainder.upper(),
            )

        return None

    def correct_token(
        self,
        token: str,
    ) -> tuple[str, str | None]:
        if not token:
            return token, None

        normalized = self._normalize_digits(
            token
        )

        # 1) Corrección de la palabra completa.
        (
            corrected_word,
            _,
            _,
        ) = self._best_dictionary_word(
            normalized,
            segment_mode=False,
        )

        # Comparamos contra NORMALIZED. Si solo ocurrió 0->O,
        # todavía debemos intentar separar prefijos como Y/TE/ME.
        if (
            corrected_word.casefold()
            != normalized.casefold()
        ):
            corrected_word = (
                self._restore_case(
                    token,
                    corrected_word,
                )
            )
            return (
                corrected_word,
                "dictionary",
            )

        # 2) Prefijo funcional seguro:
        # TEVEO -> TE VEO, TESALUDO -> TE SALUDO,
        # YHO -> Y NO, YG0M0 -> Y COMO.
        function_split = (
            self._function_prefix_split(
                normalized
            )
        )

        if function_split is not None:
            corrected = " ".join(
                function_split
            )
            corrected = self._restore_case(
                token,
                corrected,
            )
            return (
                corrected,
                "split",
            )

        # 3) Segmentación general conservadora.
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

        # 4) Si lo único seguro fue un dígito confundido dentro de letras,
        # aplicamos esa normalización al final.
        if normalized != token:
            return (
                self._restore_case(
                    token,
                    normalized,
                ),
                "digit",
            )

        return token, None

    @staticmethod
    def _context_token_key(
        token: str,
    ) -> str:
        value = unicodedata.normalize(
            "NFKD",
            token.upper(),
        )
        return "".join(
            ch
            for ch in value
            if not unicodedata.combining(ch)
            and ch.isalnum()
        )

    @classmethod
    def _context_confusable(
        cls,
        left: str,
        right: str,
    ) -> bool:
        a = cls._context_token_key(left)
        b = cls._context_token_key(right)

        if (
            not a
            or len(a) != len(b)
            or a == b
        ):
            return False

        symmetric = {
            ("G", "C"),
            ("C", "G"),
            ("H", "N"),
            ("N", "H"),
            ("N", "M"),
            ("M", "N"),
            ("0", "O"),
            ("O", "0"),
            ("1", "I"),
            ("I", "1"),
        }

        differences = 0

        for x, y in zip(a, b):
            if x == y:
                continue

            if (x, y) not in symmetric:
                return False

            differences += 1
            if differences > 2:
                return False

        return 1 <= differences <= 2

    def _apply_song_context(
        self,
        lines: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Unifica variantes OCR repetidas dentro de la misma canción.

        No consulta letras externas. Si dos tokens solo difieren en glifos
        típicamente confundidos (G/C, H/N/M, 0/O, 1/I), gana la variante
        respaldada por frecuencia lingüística, repetición en la canción o
        una confianza OCR claramente mayor.
        """
        token_stats: dict[
            str,
            dict[str, float | int | str],
        ] = {}

        for item in lines:
            confidence = float(
                item.get("confidence", 0.0)
            )

            for match in TOKEN_RE.finditer(
                str(item.get("text", ""))
            ):
                token = match.group(0)
                key = self._context_token_key(
                    token
                )
                if len(key) < 3:
                    continue

                stats = token_stats.setdefault(
                    key,
                    {
                        "token": token,
                        "count": 0,
                        "confidence_sum": 0.0,
                        "frequency": self.frequency(
                            token
                        ),
                    },
                )
                stats["count"] = (
                    int(stats["count"]) + 1
                )
                stats["confidence_sum"] = (
                    float(stats["confidence_sum"])
                    + confidence
                )

        keys = list(token_stats)
        replacements: dict[str, str] = {}

        for source_key in keys:
            source = token_stats[source_key]
            source_count = int(
                source["count"]
            )
            source_conf = (
                float(source["confidence_sum"])
                / max(1, source_count)
            )
            source_freq = int(
                source["frequency"]
            )

            best_key: str | None = None
            best_strength = -1.0

            for candidate_key in keys:
                if candidate_key == source_key:
                    continue

                if not self._context_confusable(
                    source_key,
                    candidate_key,
                ):
                    continue

                candidate = token_stats[
                    candidate_key
                ]
                candidate_count = int(
                    candidate["count"]
                )
                candidate_conf = (
                    float(
                        candidate[
                            "confidence_sum"
                        ]
                    )
                    / max(
                        1,
                        candidate_count,
                    )
                )
                candidate_freq = int(
                    candidate["frequency"]
                )

                dictionary_win = (
                    candidate_freq >= 300
                    and candidate_freq
                    >= max(
                        source_freq * 8,
                        source_freq + 3000,
                    )
                )
                repetition_win = (
                    candidate_count
                    >= source_count + 1
                    and candidate_conf
                    >= source_conf - 3.0
                )
                confidence_win = (
                    source_freq == 0
                    and candidate_freq == 0
                    and candidate_conf
                    >= source_conf + 7.0
                )

                if not (
                    dictionary_win
                    or repetition_win
                    or confidence_win
                ):
                    continue

                strength = (
                    math.log10(
                        candidate_freq + 1
                    )
                    + candidate_count * 0.8
                    + candidate_conf / 25.0
                )

                if strength > best_strength:
                    best_strength = strength
                    best_key = candidate_key

            if best_key is not None:
                replacements[
                    source_key
                ] = str(
                    token_stats[
                        best_key
                    ]["token"]
                )

        if not replacements:
            return lines, []

        context_changes: list[dict] = []
        updated_lines: list[dict] = []

        for line_index, item in enumerate(
            lines
        ):
            changes_for_line: list[dict] = []

            def replace_context(
                match: re.Match[str],
            ) -> str:
                original = match.group(0)
                key = self._context_token_key(
                    original
                )
                replacement = replacements.get(
                    key
                )

                if (
                    not replacement
                    or self._context_token_key(
                        replacement
                    ) == key
                ):
                    return original

                corrected = self._restore_case(
                    original,
                    replacement,
                )
                changes_for_line.append(
                    {
                        "line_index": line_index,
                        "from": original,
                        "to": corrected,
                        "type": "song_context",
                    }
                )
                return corrected

            new_item = dict(item)
            new_item["text"] = TOKEN_RE.sub(
                replace_context,
                str(item.get("text", "")),
            )
            updated_lines.append(
                new_item
            )
            context_changes.extend(
                changes_for_line
            )

        return (
            updated_lines,
            context_changes,
        )

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

        # Frases pegadas muy características del OCR CDG. Son reglas de
        # separación, no sustituciones de letra externa.
        phrase_rules = (
            (r"\bYES\s+QUE\b", "Y ES QUE"),
            (r"\bOHNO\b", "OH NO"),
            (r"\bYEVERYBODY\b", "Y EVERYBODY"),
        )

        for pattern, replacement in phrase_rules:
            updated = re.sub(
                pattern,
                replacement,
                corrected_line,
                flags=re.IGNORECASE,
            )
            if updated != corrected_line:
                changes.append(
                    {
                        "from": corrected_line,
                        "to": updated,
                        "type": "phrase_split",
                    }
                )
                corrected_line = updated

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

        (
            corrected_lines,
            context_changes,
        ) = self._apply_song_context(
            corrected_lines
        )
        all_changes.extend(
            context_changes
        )

        return (
            corrected_lines,
            all_changes,
        )

