#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER="DJGABO_KARAOKE_TOTAL_TIMELINE_V1"

def replace_once(text, old, new, label):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontre {n}")
    return text.replace(old,new,1)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default="/opt/djgabo-cdg")
    args=ap.parse_args()
    p=Path(args.root)/"editor_v1"/"index.html"
    if not p.is_file():
        raise SystemExit(f"No existe {p}")
    text=p.read_text(encoding="utf-8")
    if MARKER in text:
        print("PATCH=ALREADY_PRESENT")
        return

    text=replace_once(
        text,
        'const base=prev?(prev.end_time??prev.start_time):Math.max(0,S.cfg.introDuration+.25);',
        'const base=prev?(prev.end_time??prev.start_time):Math.max(0,pvOpeningDecision().end+.25);',
        "pvInstrumentalState base opening",
    )
    text=replace_once(
        text,
        'const base=prev?(prev.end_time??prev.start_time):Math.max(0,S.cfg.introDuration+.25);',
        'const base=prev?(prev.end_time??prev.start_time):Math.max(0,pvOpeningDecision().end+.25);',
        "diag instrumental base opening",
    )

    old='''    out.push({
      prev:{line:_diagLineNoForWord(prev),text:prev?.text||null,start:prev?.start_time??null,end:prev?.end_time??null},
      next:{line:_diagLineNoForWord(next),text:next?.text||null,start:next?.start_time??null,end:next?.end_time??null},
      gap_seconds:+gap.toFixed(3),
      has_spoken:hasSpoken,
      spoken_overlap_seconds:+overlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      untranscribed_voice_overlap_seconds:+voiceOverlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      has_untranscribed_voice:hasUntranscribedVoice,
      rule:longSpoken?"HABLADO>=6s":regularGap?"PAUSA_REGULAR>=6s":hasUntranscribedVoice?"VOZ_SIN_TEXTO_SUPRIME_INSTRUMENTAL":"NO_INSTRUMENTAL",
      should_show_instrumental:!!(longSpoken||regularGap),
      lead_seconds:longSpoken?(c.spokenLead??4):c.lead
    });'''
    new='''    const shouldShow=!!(longSpoken||regularGap);
    const lead=longSpoken?(c.spokenLead??4):c.lead;
    const hideAt=Number(next.start_time)-Number(lead);
    const LABEL_SLOT=.55;
    const avail=hideAt-(Number(base)+.4);
    const useLabel=shouldShow && avail>=1.0+LABEL_SLOT;
    const useSpan=shouldShow ? Math.min(Number(c.span||6),avail-(useLabel?LABEL_SLOT:0)) : 0;
    const minSpan=longSpoken?.6:1.0;
    const rendererWillInsert=shouldShow && useSpan>=minSpan;
    const dotsEnd=rendererWillInsert?hideAt:null;
    const dotsStart=rendererWillInsert?dotsEnd-useSpan:null;
    const spacerAt=rendererWillInsert?Number(base)+.3:null;
    const labelAt=rendererWillInsert?dotsStart-LABEL_SLOT:null;
    const firstSynthetic=rendererWillInsert?spacerAt:null;
    out.push({
      prev:{line:_diagLineNoForWord(prev),text:prev?.text||null,start:prev?.start_time??null,end:prev?.end_time??null},
      next:{line:_diagLineNoForWord(next),text:next?.text||null,start:next?.start_time??null,end:next?.end_time??null},
      base_seconds:+Number(base).toFixed(3),
      gap_seconds:+gap.toFixed(3),
      has_spoken:hasSpoken,
      spoken_overlap_seconds:+overlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      untranscribed_voice_overlap_seconds:+voiceOverlaps.reduce((a,[x,y])=>a+(y-x),0).toFixed(3),
      has_untranscribed_voice:hasUntranscribedVoice,
      rule:longSpoken?"HABLADO>=6s":regularGap?"PAUSA_REGULAR>=6s":hasUntranscribedVoice?"VOZ_SIN_TEXTO_SUPRIME_INSTRUMENTAL":"NO_INSTRUMENTAL",
      should_show_instrumental:shouldShow,
      lead_seconds:+Number(lead).toFixed(3),
      preview_show_from_seconds:shouldShow?+Number(base+.2).toFixed(3):null,
      hide_at_seconds:shouldShow?+Number(hideAt).toFixed(3):null,
      renderer_inserted:rendererWillInsert,
      renderer_first_synthetic_sync_seconds:firstSynthetic==null?null:+Number(firstSynthetic).toFixed(3),
      renderer_label_at_seconds:labelAt==null?null:+Number(labelAt).toFixed(3),
      renderer_dots_start_seconds:dotsStart==null?null:+Number(dotsStart).toFixed(3),
      renderer_dots_end_seconds:dotsEnd==null?null:+Number(dotsEnd).toFixed(3),
      renderer_span_seconds:rendererWillInsert?+Number(useSpan).toFixed(3):0
    });'''
    text=replace_once(text,old,new,"instrumental diagnostic details")

    anchor='''function buildDiagnosticJsonPayload(){
'''
    helpers='''/* DJGABO_KARAOKE_TOTAL_TIMELINE_V1
   La pestaña Karaoke representa TODO el timeline que debería terminar en CDG:
   OPENING -> INSTRUMENTAL -> KARAOKE -> ENDING. El JSON expone además el
   posible intro_delay interno de cdgmaker para detectar desplazamientos. */
function pvOpeningDecision(){
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
}
function pvEndingDecision(){
  const outroDuration=8;
  const audioDuration=Math.max(0,Number(S.duration||S.audio?.duration||0));
  return {
    preview_start:Math.max(0,audioDuration-outroDuration),
    preview_end:audioDuration,
    duration:Math.min(outroDuration,audioDuration),
    renderer_outro_duration_seconds:outroDuration,
    basis:"preview basado en duración del audio; renderer garantiza 8 s al final"
  };
}
function pvTimelineDiagnostic(){
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
}
function pvTimelineState(t){
  const opening=pvOpeningDecision();
  const ending=pvEndingDecision();
  if(opening.duration>0&&t>=opening.start&&t<opening.end) return {phase:"OPENING",opening};
  if(ending.preview_end>0&&t>=ending.preview_start&&t<=ending.preview_end) return {phase:"ENDING",ending};
  const inst=pvInstrumentalState(t);
  if(inst) return {phase:"INSTRUMENTAL",instrumental:inst};
  return {phase:"KARAOKE"};
}

function buildDiagnosticJsonPayload(){
'''
    text=replace_once(text,anchor,helpers,"insert total timeline helpers")

    text=replace_once(text,'diagnostic_version:"CDG_IA_TEST_DIAG_V1",','diagnostic_version:"CDG_TOTAL_TIMELINE_DIAG_V2",',"diagnostic version")
    text=replace_once(
        text,
        '''    instrumental_config:PV.cfg.instrumental,
    instrumental_decisions:_diagInstrumentalDecisions(),
    ai_block_alignments:S.doc?.ai?.block_alignments||[],''',
        '''    instrumental_config:PV.cfg.instrumental,
    instrumental_decisions:_diagInstrumentalDecisions(),
    timeline:pvTimelineDiagnostic(),
    ai_block_alignments:S.doc?.ai?.block_alignments||[],''',
        "diagnostic timeline payload",
    )

    old='''function pvDraw(){
  if(!PV.on) return;
  if(PV.mode==="intro"){ pvDrawIntro(); return; }
  if(PV.mode==="outro"){ pvDrawOutro(); return; }
  if(!PV.plan){ try{ PV.plan = pvPlan(); }catch(e){ console.warn("Preview plan error",e); PV.plan=null; } }
  pvx.fillStyle = "#000"; pvx.fillRect(0, 0, PV.VW, PV.VH);
  const info = $("#pvInfo");
  const instState=pvInstrumentalState(S.audio.currentTime); if(instState){ pvDrawInstrumental(instState); info.textContent="INSTRUMENTAL · entrada de voz en "+instState.remain.toFixed(1)+" s"; return; }
'''
    new='''function pvDraw(){
  if(!PV.on) return;
  if(PV.mode==="intro"){ pvDrawIntro(); return; }
  if(PV.mode==="outro"){ pvDrawOutro(); return; }
  if(!PV.plan){ try{ PV.plan = pvPlan(); }catch(e){ console.warn("Preview plan error",e); PV.plan=null; } }
  pvx.fillStyle = "#000"; pvx.fillRect(0, 0, PV.VW, PV.VH);
  const info = $("#pvInfo");
  const now=Number(S.audio.currentTime||0);
  const timelineState=pvTimelineState(now);
  if(timelineState.phase==="OPENING"){
    pvDrawIntro();
    const o=timelineState.opening;
    info.textContent="KARAOKE · OPENING "+o.start.toFixed(2)+"–"+o.end.toFixed(2)+" s · dura "+o.duration.toFixed(2)+" s · "+o.rule;
    return;
  }
  if(timelineState.phase==="ENDING"){
    pvDrawOutro();
    const e=timelineState.ending;
    info.textContent="KARAOKE · ENDING "+e.preview_start.toFixed(2)+"–"+e.preview_end.toFixed(2)+" s · "+e.duration.toFixed(2)+" s";
    return;
  }
  const instState=timelineState.phase==="INSTRUMENTAL"?timelineState.instrumental:null;
  if(instState){ pvDrawInstrumental(instState); info.textContent="KARAOKE · INSTRUMENTAL · entrada de voz en "+instState.remain.toFixed(1)+" s"; return; }
'''
    text=replace_once(text,old,new,"pvDraw total timeline")

    text=text.replace("</body>","<!-- "+MARKER+" -->\n</body>",1)
    p.write_text(text,encoding="utf-8")
    print("PATCH=OK")
    print("MARKER="+MARKER)

if __name__=="__main__":
    main()
