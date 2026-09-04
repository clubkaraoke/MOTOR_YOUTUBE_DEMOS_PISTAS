#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "DJGABO_CDG_ENGINE_V2_PATCH"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontre {count}")
    return text.replace(old, new, 1)


def patch_config(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    old = '''@define
class SettingsLyric:
    sync: list[int]
    text: str
    line_tile_height: int
    lines_per_page: int

    singer: int = 1
    row: int = 1
'''
    new = '''@define
class SettingsLyric:
    sync: list[int]
    text: str
    line_tile_height: int
    lines_per_page: int

    singer: int = 1
    row: int = 1
    # DJGABO_CDG_ENGINE_V2_PATCH
    # El JSON de ElevenLabs conserva START y END exactos. Nomad upstream
    # normalmente deduce el END usando la siguiente palabra o +0.45 s.
    end_sync: list[int] = field(factory=list)
    # La timeline visual se calcula una sola vez en engine_v2.py y tanto
    # preview como CDG obedecen exactamente esos draw/erase.
    explicit_timeline: bool = False
    line_draw: list[int] = field(factory=list)
    line_erase: list[int] = field(factory=list)
'''
    text = replace_once(text, old, new, "SettingsLyric")

    old_inst = '''@define
class SettingsInstrumental:
    sync: int
    line_tile_height: int

    wait: bool = True
    text: str = "INSTRUMENTAL"
    text_align: TextAlign = TextAlign.CENTER
    text_placement: TextPlacement = TextPlacement.MIDDLE
    fill: RGBColor = field(converter=to_rgbcolor, default="#bbb")
    stroke: RGBColor | None = field(
        converter=to_rgbcolor_or_none,
        default=None,
    )
    background: RGBColor | None = field(
        converter=to_rgbcolor_or_none,
        default=None,
    )
    image: Path | None = None
    transition: str | None = None
    x: int = 0
    y: int = 0
'''
    new_inst = '''@define
class SettingsInstrumental:
    sync: int
    line_tile_height: int

    wait: bool = True
    text: str = "INSTRUMENTAL"
    text_align: TextAlign = TextAlign.CENTER
    text_placement: TextPlacement = TextPlacement.MIDDLE
    fill: RGBColor = field(converter=to_rgbcolor, default="#bbb")
    stroke: RGBColor | None = field(
        converter=to_rgbcolor_or_none,
        default=None,
    )
    background: RGBColor | None = field(
        converter=to_rgbcolor_or_none,
        default=None,
    )
    image: Path | None = None
    transition: str | None = None
    x: int = 0
    y: int = 0
    # DJGABO_CDG_ENGINE_V2_PATCH
    # Fin musical real de la pausa. _compose_instrumental conserva la
    # preparación nativa de Nomad 3 s antes, sin +2 s ocultos.
    end_sync: int | None = None
'''
    text = replace_once(text, old_inst, new_inst, "SettingsInstrumental end_sync")

    old_outro_cfg = '''    intro_duration_seconds: float = 5.0
    first_syllable_buffer_seconds: float = 3.0

    outro_transition: str = "centertexttoplogobottomtext"
'''
    new_outro_cfg = '''    intro_duration_seconds: float = 5.0
    first_syllable_buffer_seconds: float = 3.0
    # DJGABO_CDG_ENGINE_V2_PATCH
    # Si se define, el ending empieza dentro del reloj del audio original.
    # No añade 8 s artificiales al archivo.
    outro_start_sync: int | None = None

    outro_transition: str = "centertexttoplogobottomtext"
'''
    text = replace_once(text, old_outro_cfg, new_outro_cfg, "Settings explicit outro start")
    path.write_text(text, encoding="utf-8")


def patch_composer(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return

    old_sync = '''                line_sync = lyric.sync[sync_i : sync_i + len(line)]
                sync_i += len(line)
                if line_sync:
                    # The last syllable ends 0.45 seconds after it
                    # starts...
                    next_sync_point = line_sync[-1] + 45
                    if sync_i < len(lyric.sync):
                        # ...or when the first syllable of the next line
                        # starts, whichever comes first
                        next_sync_point = min(
                            next_sync_point,
                            lyric.sync[sync_i],
                        )
                    line_sync.append(next_sync_point)

                # Collect this line's syllables
                syllables: list[SyllableInfo] = []
                for si, (mask, syllable, (start, end)) in enumerate(
                    zip(
                        line_mask,
                        line,
                        it.pairwise(line_sync),
                    )
                ):
'''
    new_sync = '''                # DJGABO_CDG_ENGINE_V2_PATCH
                # START y END son autoridad del JSON. Sólo conservamos el
                # comportamiento upstream como fallback de compatibilidad.
                line_sync = lyric.sync[sync_i : sync_i + len(line)]
                line_end_sync = lyric.end_sync[sync_i : sync_i + len(line)] if lyric.end_sync else []
                sync_i += len(line)
                if line_end_sync and len(line_end_sync) == len(line_sync):
                    line_pairs = list(zip(line_sync, line_end_sync))
                else:
                    work_sync = list(line_sync)
                    if work_sync:
                        next_sync_point = work_sync[-1] + 45
                        if sync_i < len(lyric.sync):
                            next_sync_point = min(next_sync_point, lyric.sync[sync_i])
                        work_sync.append(next_sync_point)
                    line_pairs = list(it.pairwise(work_sync))

                # Collect this line's syllables
                syllables: list[SyllableInfo] = []
                for si, (mask, syllable, (start, end)) in enumerate(
                    zip(
                        line_mask,
                        line,
                        line_pairs,
                    )
                ):
'''
    text = replace_once(text, old_sync, new_sync, "exact word END")

    old_times = '''            line_count = len(lyric.lines)
            line_draw: list[int] = [0] * line_count
            line_erase: list[int] = [0] * line_count

            # The first page is drawn 3 seconds before the first
'''
    new_times = '''            line_count = len(lyric.lines)
            line_draw: list[int] = [0] * line_count
            line_erase: list[int] = [0] * line_count

            # DJGABO_CDG_ENGINE_V2_PATCH
            cfg_lyric = self.config.lyrics[lyric.lyric_index]
            if getattr(cfg_lyric, "explicit_timeline", False):
                if len(cfg_lyric.line_draw) != line_count or len(cfg_lyric.line_erase) != line_count:
                    raise RuntimeError(
                        f"V2 explicit timeline: {len(cfg_lyric.line_draw)}/{len(cfg_lyric.line_erase)} "
                        f"tiempos para {line_count} lineas"
                    )
                line_draw = [sync_to_cdg(int(x)) for x in cfg_lyric.line_draw]
                line_erase = [sync_to_cdg(int(x)) for x in cfg_lyric.line_erase]
                self.logger.info("CDG V2: authoritative line timeline (%d lines)", line_count)
                self.lyric_times.append(LyricTimes(line_draw=line_draw, line_erase=line_erase))
                continue

            # The first page is drawn 3 seconds before the first
'''
    text = replace_once(text, old_times, new_times, "explicit timeline")

    old_inst_end = '''            # The instrumental should end when the next line is drawn by
            # default
            if line_draw_time is not None:
                instrumental_end = line_draw_time
            else:
                # NOTE A value of None here means this instrumental will
                # never end (and once the screen is drawn, it will not
                # pause), unless there is another instrumental after
                # this.
                instrumental_end = None
'''
    new_inst_end = '''            # DJGABO_CDG_ENGINE_V2_PATCH
            # Si el V2 conoce el START siguiente, ese es el fin musical real
            # del instrumental. Nomad mantiene su preparación nativa 3 s antes.
            if getattr(instrumental, "end_sync", None) is not None:
                instrumental_end = sync_to_cdg(int(instrumental.end_sync))
            # Fallback upstream: terminar cuando se dibuja la siguiente línea.
            elif line_draw_time is not None:
                instrumental_end = line_draw_time
            else:
                instrumental_end = None
'''
    text = replace_once(text, old_inst_end, new_inst_end, "instrumental explicit end")

    old_outro_sched = '''            # Calculate video padding before outro
            OUTRO_DURATION = 2400
            # This karaoke file ends at the later of:
            # - The end of the audio (with the padded intro)
            # - 8 seconds after the current video time
            end = max(
                int(self.audio.duration_seconds * CDG_FPS),
                self.writer.packets_queued + OUTRO_DURATION,
            )
            self.logger.debug(f"song should be {end} frame(s) long")
            padding_before_outro = (end - OUTRO_DURATION) - self.writer.packets_queued
            self.logger.debug(f"queueing {padding_before_outro} packets before outro")
            self.writer.queue_packets([no_instruction()] * padding_before_outro)

            # Compose the outro (and thus, finish the video)
            self._compose_outro(end)
'''
    new_outro_sched = '''            # DJGABO_CDG_ENGINE_V2_PATCH
            # El panel DJGABO ya decide dónde empieza el Ending dentro del
            # audio. Si se provee outro_start_sync, respetamos ese reloj y no
            # añadimos una cola artificial de 8 segundos.
            explicit_outro = getattr(self.config, "outro_start_sync", None)
            if explicit_outro is not None:
                end = max(int(self.audio.duration_seconds * CDG_FPS), self.writer.packets_queued)
                outro_start = sync_to_cdg(int(explicit_outro))
                if self.writer.packets_queued < outro_start:
                    self.writer.queue_packets([no_instruction()] * (outro_start - self.writer.packets_queued))
                self.logger.info(
                    "CDG V2: explicit outro at %d, audio end %d",
                    outro_start,
                    end,
                )
                self._compose_outro(end)
            else:
                # Upstream behavior.
                OUTRO_DURATION = 2400
                end = max(
                    int(self.audio.duration_seconds * CDG_FPS),
                    self.writer.packets_queued + OUTRO_DURATION,
                )
                self.logger.debug(f"song should be {end} frame(s) long")
                padding_before_outro = (end - OUTRO_DURATION) - self.writer.packets_queued
                self.logger.debug(f"queueing {padding_before_outro} packets before outro")
                self.writer.queue_packets([no_instruction()] * padding_before_outro)
                self._compose_outro(end)
'''
    text = replace_once(text, old_outro_sched, new_outro_sched, "explicit outro schedule")

    old_intro = '''    def _compose_intro(self):
        # TODO Make it so the intro screen is not hardcoded
        self.logger.debug("composing intro")
'''
    new_intro = '''    def _compose_intro(self):
        # DJGABO_CDG_ENGINE_V2_PATCH
        # La timeline musical nunca se desplaza. Si el V2 desactiva opening,
        # no se agregan ni pantalla ni silencio. Si luego se habilita opening,
        # intro_delay sigue forzado a cero por el bloque de abajo.
        if float(self.config.intro_duration_seconds or 0) <= 0:
            self.intro_delay = 0
            self.logger.info("CDG V2: opening disabled; intro_delay=0")
            return
        # TODO Make it so the intro screen is not hardcoded
        self.logger.debug("composing intro")
'''
    text = replace_once(text, old_intro, new_intro, "intro disable")

    old_delay = '''        if first_syllable_start_offset < MINIMUM_FIRST_SYLLABLE_TIME_FOR_NO_SILENCE:
            self.intro_delay = MINIMUM_FIRST_SYLLABLE_TIME_FOR_NO_SILENCE
            self.logger.info(
                f"First syllable within {self.config.intro_duration_seconds + self.config.first_syllable_buffer_seconds} seconds. Adding {self.intro_delay} frames of silence."
            )
        else:
            self.intro_delay = 0
            self.logger.info("First syllable after buffer period. No additional silence needed.")
'''
    new_delay = '''        # DJGABO_CDG_ENGINE_V2_PATCH
        # Prohibido desplazar el JSON para acomodar el opening.
        self.intro_delay = 0
        self.logger.info("CDG V2: intro_delay forced to 0; word timestamps are immutable")
'''
    text = replace_once(text, old_delay, new_delay, "intro delay")

    old_outro = '''    def _compose_outro(self, end: int):
        # TODO Make it so the outro screen is not hardcoded
        self.logger.debug("composing outro")
'''
    new_outro = '''    def _compose_outro(self, end: int):
        # DJGABO_CDG_ENGINE_V2_PATCH
        # En la primera fase del motor V2 el ending visual queda desactivado.
        # Sólo rellenamos hasta el final del reloj sin introducir otra lógica.
        if not str(self.config.outro_text_line1 or "").strip() and not str(self.config.outro_text_line2 or "").strip():
            remaining = max(0, end - self.writer.packets_queued)
            self.writer.queue_packets([no_instruction()] * remaining)
            self.logger.info("CDG V2: ending disabled; padded %d packets", remaining)
            return
        # TODO Make it so the outro screen is not hardcoded
        self.logger.debug("composing outro")
'''
    text = replace_once(text, old_outro, new_outro, "outro disable")

    path.write_text(text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("uso: patch_nomad.py /ruta/a/cdgmaker")
    root = Path(sys.argv[1]).resolve()
    patch_config(root / "config.py")
    patch_composer(root / "composer.py")
    print("NOMAD_PATCH=OK")
    print("MARKER=" + MARKER)


if __name__ == "__main__":
    main()
