#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER_EDITOR="DJGABO_RENDER_TIMELINE_V1"
MARKER_NORMALIZE="DJGABO_RENDER_TIMELINE_NORMALIZER_V1"
MARKER_RENDER="DJGABO_RENDER_TIMELINE_RENDERER_V1"
MARKER_COMPOSER="DJGABO_NO_INTRO_DELAY_V1"

def replace_once(text, old, new, label):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontre {n}")
    return text.replace(old,new,1)

def patch_editor(path:Path):
    text=path.read_text(encoding="utf-8")
    if MARKER_EDITOR in text:
        return

    old='''function pvOpeningDecision(){
  const allTimed=(S.words||[]).filter(w=>w.start_time!==null).slice().sort((a,b)=>Number(a.start_time)-Number(b.start_time));
  const sung=(S.words||[]).filter(w=>!w.spoken&&w.start_time!==null).slice().sort((a,b)=>Number(a.start_time)-Number(b.start_time));
  const firstTimed=allTimed.length?Number(allTimed[0].start_time):null;
  const firstSung=sung.length?Number(sung[0].start_time):null;
  const normal=Number(S.cfg.introDuration||6);
  const short=Number(S.cfg.introShort||3);
  const skipBefore=3, shortBefore=6;
  let duration=normal, rule="AUTO_NORMAL";
  if(firstTimed===null){ duration=normal; rule="SIN_PRIMER_TIMING"; }
  else if(firstTimed<skipBefore){ duration=0; rule="AUTO_SKIP_<3S"; }
  else if(firstTimed<shortBefore){ duration=short; rule="AUTO_SHORT_3_6S"; }
  return {
    start:0,end:duration,duration,rule,
    first_timed_word_seconds:firstTimed,
    first_sung_word_seconds:firstSung,
    normal_seconds:normal,short_seconds:short,
    skip_before_seconds:skipBefore,short_before_seconds:shortBefore
  };
}'''
    new='''/* DJGABO_RENDER_TIMELINE_V1
   Una sola fuente de verdad: primero se detecta VOZ REAL, luego se decide
   cuánto opening cabe antes de que cdgmaker necesite dibujar la primera letra.
   Los eventos sintéticos (INSTRUMENTAL/círculos) jamás participan. */
function pvOpeningDecision(){
  const allTimed=(S.words||[]).filter(w=>w.start_time!==null).slice().sort((a,b)=>Number(a.start_time)-Number(b.start_time));
  const sung=(S.words||[]).filter(w=>!w.spoken&&w.start_time!==null).slice().sort((a,b)=>Number(a.start_time)-Number(b.start_time));
  const firstReal=allTimed.length?Number(allTimed[0].start_time):null;
  const firstSung=sung.length?Number(sung[0].start_time):null;
  const normal=Number(S.cfg.introDuration||6);
  const short=Number(S.cfg.introShort||3);
  const buffer=3;
  const shortNeeds=short+buffer;   // 3 s opening + 3 s para preparar letra = 6 s
  const normalNeeds=normal+buffer; // 6 s opening + 3 s para preparar letra = 9 s
  let duration=normal, rule="AUTO_NORMAL_FITS";
  if(firstReal===null){ duration=normal; rule="SIN_VOZ_REAL"; }
  else if(firstReal<shortNeeds){ duration=0; rule="AUTO_SKIP_NO_CABE"; }
  else if(firstReal<normalNeeds){ duration=short; rule="AUTO_SHORT_FITS"; }
  return {
    start:0,end:duration,duration,enabled:duration>0,rule,
    first_real_voice_seconds:firstReal,
    first_timed_word_seconds:firstReal,
    first_sung_word_seconds:firstSung,
    first_syllable_buffer_seconds:buffer,
    short_requires_seconds:shortNeeds,
    normal_requires_seconds:normalNeeds,
    normal_seconds:normal,short_seconds:short
  };
}
function buildRenderTimelineDecision(){
  const o=pvOpeningDecision();
  return {
    version:"CDG_RENDER_TIMELINE_V1",
    clock_origin_seconds:0,
    first_real_voice_seconds:o.first_real_voice_seconds,
    first_sung_vocal_seconds:o.first_sung_word_seconds,
    opening:{
      enabled:o.enabled,
      render_screen:o.enabled,
      start_seconds:0,
      duration_seconds:o.duration,
      end_seconds:o.end,
      rule:o.rule,
      first_syllable_buffer_seconds:o.first_syllable_buffer_seconds
    },
    policy:{
      json_is_source_of_truth:true,
      synthetic_events_affect_opening:false,
      composer_intro_delay_seconds:0,
      preserve_original_audio_clock:true
    }
  };
}'''
    text=replace_once(text,old,new,"editor opening policy")

    old='''function pvTimelineDiagnostic(){
  const opening=pvOpeningDecision();
  const instrumentals=_diagInstrumentalDecisions();
  const inserted=instrumentals.filter(x=>x.renderer_inserted&&x.renderer_first_synthetic_sync_seconds!=null);
  const allTimed=(S.words||[]).filter(w=>w.start_time!==null).map(w=>Number(w.start_time)).filter(Number.isFinite);
  const firstActual=allTimed.length?Math.min(...allTimed):null;
  const firstSynthetic=inserted.length?Math.min(...inserted.map(x=>Number(x.renderer_first_synthetic_sync_seconds))):null;
  const candidates=[firstActual,firstSynthetic].filter(v=>v!==null&&Number.isFinite(v));
  const firstComposerSync=candidates.length?Math.min(...candidates):null;
  const firstBuffer=3;
  const threshold=opening.duration+firstBuffer;
  const predictedIntroDelay=(firstComposerSync!==null&&firstComposerSync<threshold)?threshold:0;
  const ending=pvEndingDecision();
  const now=Number(S.audio?.currentTime||0);
  let phase="KARAOKE";
  if(opening.duration>0&&now>=opening.start&&now<opening.end) phase="OPENING";
  else if(ending.preview_end>0&&now>=ending.preview_start&&now<=ending.preview_end) phase="ENDING";
  else if(pvInstrumentalState(now)) phase="INSTRUMENTAL";
  return {
    current_time_seconds:+now.toFixed(3),
    current_phase:phase,
    opening,
    instrumental_events:instrumentals,
    ending,
    composer_probe:{
      first_syllable_buffer_seconds:firstBuffer,
      first_actual_timed_word_seconds:firstActual,
      first_synthetic_instrumental_sync_seconds:firstSynthetic,
      first_sync_seen_by_composer_seconds:firstComposerSync,
      intro_plus_buffer_threshold_seconds:+threshold.toFixed(3),
      predicted_intro_delay_seconds:+predictedIntroDelay.toFixed(3),
      predicted_external_audio_cdg_shift_seconds:+predictedIntroDelay.toFixed(3),
      warning:predictedIntroDelay>0
        ?"RIESGO: cdgmaker agregaría intro_delay porque el primer sync (posiblemente INSTRUMENTAL sintético) cae antes de OPENING+BUFFER. Con audio externo/original, el CDG puede quedar desplazado."
        :"Sin intro_delay predicho por esta regla."
    }
  };
}'''
    new='''function pvTimelineDiagnostic(){
  const opening=pvOpeningDecision();
  const instrumentals=_diagInstrumentalDecisions();
  const inserted=instrumentals.filter(x=>x.renderer_inserted&&x.renderer_first_synthetic_sync_seconds!=null);
  const allTimed=(S.words||[]).filter(w=>w.start_time!==null).map(w=>Number(w.start_time)).filter(Number.isFinite);
  const firstActual=allTimed.length?Math.min(...allTimed):null;
  const firstSynthetic=inserted.length?Math.min(...inserted.map(x=>Number(x.renderer_first_synthetic_sync_seconds))):null;
  const legacyCandidates=[firstActual,firstSynthetic].filter(v=>v!==null&&Number.isFinite(v));
  const legacyFirstSync=legacyCandidates.length?Math.min(...legacyCandidates):null;
  const firstBuffer=opening.first_syllable_buffer_seconds;
  const legacyThreshold=opening.duration+firstBuffer;
  const legacyDelay=(legacyFirstSync!==null&&legacyFirstSync<legacyThreshold)?legacyThreshold:0;
  const ending=pvEndingDecision();
  const now=Number(S.audio?.currentTime||0);
  let phase="KARAOKE";
  if(opening.duration>0&&now>=opening.start&&now<opening.end) phase="OPENING";
  else if(ending.preview_end>0&&now>=ending.preview_start&&now<=ending.preview_end) phase="ENDING";
  else if(pvInstrumentalState(now)) phase="INSTRUMENTAL";
  return {
    current_time_seconds:+now.toFixed(3),
    current_phase:phase,
    opening,
    instrumental_events:instrumentals,
    ending,
    render_timeline:buildRenderTimelineDecision(),
    composer_probe:{
      first_syllable_buffer_seconds:firstBuffer,
      first_actual_timed_word_seconds:firstActual,
      first_synthetic_instrumental_sync_seconds:firstSynthetic,
      legacy_first_sync_seen_by_composer_seconds:legacyFirstSync,
      legacy_intro_plus_buffer_threshold_seconds:+legacyThreshold.toFixed(3),
      legacy_predicted_intro_delay_seconds:+legacyDelay.toFixed(3),
      synthetic_events_affect_opening:false,
      renderer_intro_delay_seconds:0,
      predicted_intro_delay_seconds:0,
      predicted_external_audio_cdg_shift_seconds:0,
      warning:legacyDelay>0
        ?"CORREGIDO: el motor antiguo habría agregado "+legacyDelay.toFixed(2)+" s. El renderer actual conserva intro_delay=0 y obedece el JSON."
        :"OK: renderer actual conserva intro_delay=0 y obedece el JSON."
    }
  };
}'''
    text=replace_once(text,old,new,"editor diagnostic current renderer")

    text=replace_once(text,'diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V2",','diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V3",',"diagnostic version")
    text=replace_once(
        text,
        '    timeline:pvTimelineDiagnostic(),\n    ai_block_alignments:S.doc?.ai?.block_alignments||[],',
        '    timeline:pvTimelineDiagnostic(),\n    render_timeline:buildRenderTimelineDecision(),\n    ai_block_alignments:S.doc?.ai?.block_alignments||[],',
        "diagnostic render timeline",
    )

    text=replace_once(
        text,
        '  out.song.duration = S.duration;\n  for(const seg of out.segments){',
        '  out.song.duration = S.duration;\n  out.render_timeline = buildRenderTimelineDecision();\n  for(const seg of out.segments){',
        "export render timeline",
    )

    text=text.replace("</body>",f"<!-- {MARKER_EDITOR} -->\n</body>",1)
    path.write_text(text,encoding="utf-8")

def patch_normalize(path:Path):
    text=path.read_text(encoding="utf-8")
    if MARKER_NORMALIZE in text:
        return

    text=replace_once(
        text,
        '''    instrumentals: list[dict]
    duration: float
    warnings: list[Warning_] = field(default_factory=list)
''',
        '''    instrumentals: list[dict]
    duration: float
    render_timeline: dict
    warnings: list[Warning_] = field(default_factory=list)
''',
        "normalized render timeline field",
    )

    start=text.index("def decide_intro(doc: dict, style: dict) -> float:")
    end=text.index("\n\ndef normalize(doc: dict, style: dict) -> Normalized:",start)
    old=text[start:end]
    new='''# DJGABO_RENDER_TIMELINE_NORMALIZER_V1
def _first_real_voice(doc: dict) -> float | None:
    vals = [
        float(w["start_time"])
        for seg in doc.get("segments", [])
        for w in seg.get("words", [])
        if w.get("start_time") is not None
    ]
    return min(vals) if vals else None


def _first_sung_voice(doc: dict) -> float | None:
    vals = [
        float(w["start_time"])
        for seg in doc.get("segments", [])
        for w in seg.get("words", [])
        if not w.get("spoken") and w.get("start_time") is not None
    ]
    return min(vals) if vals else None


def _computed_render_timeline(doc: dict, style: dict) -> dict:
    first_real = _first_real_voice(doc)
    first_sung = _first_sung_voice(doc)
    normal = float(style.get("intro_duration_seconds", 6.0))
    short = float(style.get("intro_short_duration_seconds", 3.0))
    buffer_s = float(style.get("first_syllable_buffer_seconds", 3.0))
    short_needs = short + buffer_s
    normal_needs = normal + buffer_s

    if first_real is None:
        duration, rule = normal, "SIN_VOZ_REAL"
    elif first_real < short_needs:
        duration, rule = 0.0, "AUTO_SKIP_NO_CABE"
    elif first_real < normal_needs:
        duration, rule = short, "AUTO_SHORT_FITS"
    else:
        duration, rule = normal, "AUTO_NORMAL_FITS"

    return {
        "version": "CDG_RENDER_TIMELINE_V1",
        "clock_origin_seconds": 0.0,
        "first_real_voice_seconds": first_real,
        "first_sung_vocal_seconds": first_sung,
        "opening": {
            "enabled": duration > 0,
            "render_screen": duration > 0,
            "start_seconds": 0.0,
            "duration_seconds": duration,
            "end_seconds": duration,
            "rule": rule,
            "first_syllable_buffer_seconds": buffer_s,
        },
        "policy": {
            "json_is_source_of_truth": False,
            "synthetic_events_affect_opening": False,
            "composer_intro_delay_seconds": 0.0,
            "preserve_original_audio_clock": True,
        },
    }


def resolve_render_timeline(doc: dict, style: dict) -> dict:
    """El JSON del editor manda. Sólo calculamos fallback para proyectos antiguos."""
    raw = doc.get("render_timeline")
    if not isinstance(raw, dict) or not isinstance(raw.get("opening"), dict):
        return _computed_render_timeline(doc, style)

    opening = dict(raw["opening"])
    try:
        duration = max(0.0, float(opening.get("duration_seconds", 0.0)))
    except (TypeError, ValueError):
        raise NormalizeError("render_timeline.opening.duration_seconds no es numérico.")

    first_real = raw.get("first_real_voice_seconds")
    if first_real is None:
        first_real = _first_real_voice(doc)
    else:
        first_real = float(first_real)
    first_sung = raw.get("first_sung_vocal_seconds")
    if first_sung is None:
        first_sung = _first_sung_voice(doc)
    else:
        first_sung = float(first_sung)

    buffer_s = float(opening.get(
        "first_syllable_buffer_seconds",
        style.get("first_syllable_buffer_seconds", 3.0),
    ))
    # Si el editor dice que hay opening, verificamos que realmente quepa antes
    # de la zona de preparación de la primera voz. No inventamos retrasos.
    if first_real is not None and duration > 0 and duration + buffer_s > first_real + 1e-6:
        raise NormalizeError(
            "El JSON de render pide un Opening que invade la primera voz real: "
            f"opening={duration:.3f}s + buffer={buffer_s:.3f}s > voz={first_real:.3f}s."
        )

    timeline = {
        "version": str(raw.get("version") or "CDG_RENDER_TIMELINE_V1"),
        "clock_origin_seconds": 0.0,
        "first_real_voice_seconds": first_real,
        "first_sung_vocal_seconds": first_sung,
        "opening": {
            "enabled": bool(opening.get("enabled", duration > 0)) and duration > 0,
            "render_screen": bool(opening.get("render_screen", duration > 0)) and duration > 0,
            "start_seconds": 0.0,
            "duration_seconds": duration,
            "end_seconds": duration,
            "rule": str(opening.get("rule") or "JSON_EXPLICITO"),
            "first_syllable_buffer_seconds": buffer_s,
        },
        "policy": {
            "json_is_source_of_truth": True,
            "synthetic_events_affect_opening": False,
            "composer_intro_delay_seconds": 0.0,
            "preserve_original_audio_clock": True,
        },
    }
    return timeline


def decide_intro(doc: dict, style: dict) -> float:
    """Compatibilidad: devuelve la decisión final, ya resuelta por el JSON."""
    return float(resolve_render_timeline(doc, style)["opening"]["duration_seconds"])
'''
    text=text[:start]+new+text[end:]

    text=replace_once(
        text,
        '''    check_complete(doc)
    style = dict(style)
    style["intro_duration_seconds"] = decide_intro(doc, style)

    font_path = resolve_font_path(style)
''',
        '''    check_complete(doc)
    style = dict(style)
    render_timeline = resolve_render_timeline(doc, style)
    style["intro_duration_seconds"] = float(render_timeline["opening"]["duration_seconds"])

    font_path = resolve_font_path(style)
''',
        "normalize resolved timeline",
    )

    text=replace_once(
        text,
        '''        instrumentals=instrumentals,
        duration=doc["song"].get("duration", 0.0),
        warnings=warns,
''',
        '''        instrumentals=instrumentals,
        duration=doc["song"].get("duration", 0.0),
        render_timeline=render_timeline,
        warnings=warns,
''',
        "normalized return timeline",
    )

    path.write_text(text,encoding="utf-8")

def patch_render(path:Path):
    text=path.read_text(encoding="utf-8")
    if MARKER_RENDER in text:
        return
    text=replace_once(
        text,
        '''        style_run = dict(style)
        try:
''',
        '''        # DJGABO_RENDER_TIMELINE_RENDERER_V1
        # La decisión ya viene resuelta por el JSON/normalizador. No se vuelve
        # a interpretar la primera sílaba ni se deja que un sintético cambie el reloj.
        style_run = dict(style)
        style_run["intro_duration_seconds"] = float(norm.render_timeline["opening"]["duration_seconds"])
        style_run["intro_mode"] = "always" if style_run["intro_duration_seconds"] > 0 else "never"
        log.info(
            "render timeline: first_real_voice=%s · opening=%ss · rule=%s · intro_delay=0",
            norm.render_timeline.get("first_real_voice_seconds"),
            style_run["intro_duration_seconds"],
            norm.render_timeline["opening"].get("rule"),
        )
        try:
''',
        "renderer style timeline",
    )
    text=replace_once(
        text,
        '''        "composer_warnings": catcher.items,
    }
''',
        '''        "composer_warnings": catcher.items,
        "render_timeline": norm.render_timeline,
    }
''',
        "renderer report timeline",
    )
    path.write_text(text,encoding="utf-8")

def patch_composer(path:Path):
    text=path.read_text(encoding="utf-8")
    if MARKER_COMPOSER in text:
        return

    text=replace_once(
        text,
        '''            self.intro_delay = 0
            # Compose the intro
            # NOTE This also sets the intro delay for later.
            self._compose_intro()
''',
        '''            # DJGABO_NO_INTRO_DELAY_V1
            # El reloj del CDG es el reloj del audio original. El opening puede
            # existir o no, pero nunca desplaza audio/letra.
            self.intro_delay = 0
            self._compose_intro()
''',
        "composer intro call",
    )

    text=replace_once(
        text,
        '''            # Add audio padding to intro
            self.logger.debug("padding intro of audio file")
            intro_silence: AudioSegment = AudioSegment.silent(
                self.intro_delay * 1000 // CDG_FPS,
                frame_rate=song.frame_rate,
            )
            self.audio = intro_silence + song
''',
        '''            # El audio conserva 0:00 absoluto; jamás se inserta silencio
            # para acomodar la portada.
            self.logger.debug("preserving original audio clock; intro padding disabled")
            self.intro_delay = 0
            self.audio = song
''',
        "composer no audio padding",
    )

    text=replace_once(
        text,
        '''    def _compose_intro(self):
        # TODO Make it so the intro screen is not hardcoded
        self.logger.debug("composing intro")
        self.writer.queue_packets(
''',
        '''    def _compose_intro(self):
        # DJGABO_NO_INTRO_DELAY_V1: si el JSON decidió omitir Opening, no
        # generamos ni un solo paquete de portada.
        if float(self.config.intro_duration_seconds) <= 0:
            self.intro_delay = 0
            self.logger.info("Opening disabled by render timeline; no intro packets queued.")
            return

        # TODO Make it so the intro screen is not hardcoded
        self.logger.debug("composing intro")
        self.writer.queue_packets(
''',
        "composer skip disabled opening",
    )

    start=text.index("        # Replace hardcoded values with configured ones\n        INTRO_DURATION = int(self.config.intro_duration_seconds * CDG_FPS)")
    end=text.index("\n    def _compose_outro(self, end: int):",start)
    block=text[start:end]
    needle='''        # Replace hardcoded values with configured ones
        INTRO_DURATION = int(self.config.intro_duration_seconds * CDG_FPS)
        FIRST_SYLLABLE_BUFFER = int(self.config.first_syllable_buffer_seconds * CDG_FPS)

        # Queue the intro screen for 5 seconds
        end_time = INTRO_DURATION
        self.writer.queue_packets([no_instruction()] * (end_time - self.writer.packets_queued))

        first_syllable_start_offset = min(
            syllable.start_offset for lyric in self.lyrics for line in lyric.lines for syllable in line.syllables
        )
        self.logger.debug(f"first syllable starts at {first_syllable_start_offset}")

        MINIMUM_FIRST_SYLLABLE_TIME_FOR_NO_SILENCE = INTRO_DURATION + FIRST_SYLLABLE_BUFFER
        # If the first syllable is within buffer+intro time, add silence
        # Otherwise, don't add any silence
        if first_syllable_start_offset < MINIMUM_FIRST_SYLLABLE_TIME_FOR_NO_SILENCE:
            self.intro_delay = MINIMUM_FIRST_SYLLABLE_TIME_FOR_NO_SILENCE
            self.logger.info(
                f"First syllable within {self.config.intro_duration_seconds + self.config.first_syllable_buffer_seconds} seconds. Adding {self.intro_delay} frames of silence."
            )
        else:
            self.intro_delay = 0
            self.logger.info("First syllable after buffer period. No additional silence needed.")
'''
    repl='''        # El JSON ya decidió la duración. Sólo mantenemos la portada exactamente
        # ese tiempo; no inspeccionamos sílabas (reales ni sintéticas).
        INTRO_DURATION = int(self.config.intro_duration_seconds * CDG_FPS)
        end_time = INTRO_DURATION
        if self.writer.packets_queued < end_time:
            self.writer.queue_packets([no_instruction()] * (end_time - self.writer.packets_queued))
        self.intro_delay = 0
        self.logger.info(
            f"Opening composed for {self.config.intro_duration_seconds:.3f}s; intro_delay forced to 0."
        )
'''
    if needle not in block:
        raise RuntimeError("composer first syllable intro-delay block no coincide")
    block=block.replace(needle,repl,1)
    text=text[:start]+block+text[end:]

    path.write_text(text,encoding="utf-8")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    ap.add_argument("--composer",required=True)
    args=ap.parse_args()
    root=Path(args.root)
    editor=root/"editor_v1"/"index.html"
    normalize=root/"renderer"/"normalize.py"
    render=root/"renderer"/"render.py"
    composer=Path(args.composer)
    for p in (editor,normalize,render,composer):
        if not p.is_file():
            raise SystemExit(f"Falta {p}")
    patch_editor(editor)
    patch_normalize(normalize)
    patch_render(render)
    patch_composer(composer)
    print("PATCH=OK")
    print("EDITOR="+MARKER_EDITOR)
    print("NORMALIZE="+MARKER_NORMALIZE)
    print("RENDER="+MARKER_RENDER)
    print("COMPOSER="+MARKER_COMPOSER)

if __name__=="__main__":
    main()
