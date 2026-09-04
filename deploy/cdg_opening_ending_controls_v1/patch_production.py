#!/usr/bin/env python3
from pathlib import Path
import sys

EDITOR_MARKER="DJGABO_OPENING_ENDING_CONTROLS_V1"
NORMALIZE_MARKER="DJGABO_ENDING_ENABLE_NORMALIZER_V1"
CONFIG_MARKER="DJGABO_ENDING_ENABLE_CONFIG_V1"
COMPOSER_MARKER="DJGABO_ENDING_ENABLE_COMPOSER_V1"

def one(t,old,new,label):
    n=t.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1, encontre {n}")
    return t.replace(old,new,1)

def between(t,start,end,new,label):
    a=t.find(start)
    if a<0: raise RuntimeError(label+": falta inicio")
    b=t.find(end,a+len(start))
    if b<0: raise RuntimeError(label+": falta fin")
    return t[:a]+new+t[b:]

def patch_editor(p):
    t=p.read_text(encoding="utf-8")
    if EDITOR_MARKER in t:
        print("EDITOR_ALREADY=YES"); return

    css="""
/* DJGABO_OPENING_ENDING_CONTROLS_V1 */
.pvVisibilityBox{display:flex;flex-direction:column;gap:7px;margin:7px 0 11px;padding:9px;border:1px solid var(--line);border-radius:7px;background:rgba(255,255,255,.018)}
.pvVisibilityRow{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;font:600 10.5px var(--sans);color:var(--text)}
.pvVisibilityRow small{display:block;margin-top:2px;font:9px/1.35 var(--mono);color:var(--dimmer);font-weight:400}
.pvVisibilityRow input{width:17px;height:17px;accent-color:#8b5cf6;cursor:pointer}
"""
    t=one(t,"</style>",css+"\n</style>","css")

    t=one(t,
'''      <div class="pvCtlGroup" data-group="intro">
        <div class="pvCtlTitle">Opening beta · automático</div>''',
'''      <div class="pvCtlGroup" data-group="intro">
        <div class="pvCtlTitle">Opening beta · automático</div>
        <div class="pvVisibilityBox">
          <label class="pvVisibilityRow"><span>Mostrar en Preview<small>Solo controla la vista mientras editas.</small></span><input id="pvOpeningPreviewEnabled" type="checkbox" checked></label>
          <label class="pvVisibilityRow"><span>Incluir en CDG final<small>Apagado = no se renderiza Opening; no mueve timings.</small></span><input id="pvOpeningCdgEnabled" type="checkbox" checked></label>
        </div>''',"opening controls")

    t=one(t,
'''      <div class="pvCtlGroup" data-group="outro">
        <div class="pvCtlTitle">Ending / cierre</div>''',
'''      <div class="pvCtlGroup" data-group="outro">
        <div class="pvCtlTitle">Ending / cierre</div>
        <div class="pvVisibilityBox">
          <label class="pvVisibilityRow"><span>Mostrar en Preview<small>Solo controla la vista previa del Ending.</small></span><input id="pvEndingPreviewEnabled" type="checkbox" checked></label>
          <label class="pvVisibilityRow"><span>Incluir en CDG final<small>Apagado = el CDG termina sin pantalla de cierre.</small></span><input id="pvEndingCdgEnabled" type="checkbox" checked></label>
        </div>''',"ending controls")

    new_open=r'''function pvOpeningDecision(){
  const allTimed=(S.words||[]).filter(w=>w.start_time!==null).slice().sort((a,b)=>Number(a.start_time)-Number(b.start_time));
  const sung=(S.words||[]).filter(w=>!w.spoken&&w.start_time!==null).slice().sort((a,b)=>Number(a.start_time)-Number(b.start_time));
  const firstReal=allTimed.length?Number(allTimed[0].start_time):null;
  const firstSung=sung.length?Number(sung[0].start_time):null;
  const normal=Number(S.cfg.introDuration||6),short=Number(S.cfg.introShort||3),buffer=3;
  const shortNeeds=short+buffer,normalNeeds=normal+buffer;
  let autoDuration=normal,rule="AUTO_NORMAL_FITS";
  if(firstReal===null){autoDuration=normal;rule="SIN_VOZ_REAL";}
  else if(firstReal<shortNeeds){autoDuration=0;rule="AUTO_SKIP_NO_CABE";}
  else if(firstReal<normalNeeds){autoDuration=short;rule="AUTO_SHORT_FITS";}
  const v=cdgVisibilitySettings();
  const previewDuration=v.openingPreview?autoDuration:0;
  const cdgDuration=v.openingCdg?autoDuration:0;
  return {
    start:0,end:cdgDuration,duration:cdgDuration,enabled:v.openingCdg&&cdgDuration>0,
    preview_enabled:v.openingPreview,cdg_enabled:v.openingCdg,
    preview_start:0,preview_end:previewDuration,preview_duration:previewDuration,
    auto_duration_seconds:autoDuration,rule,
    first_real_voice_seconds:firstReal,first_timed_word_seconds:firstReal,first_sung_word_seconds:firstSung,
    first_syllable_buffer_seconds:buffer,short_requires_seconds:shortNeeds,normal_requires_seconds:normalNeeds,
    normal_seconds:normal,short_seconds:short
  };
}
function buildRenderTimelineDecision(){
  const o=pvOpeningDecision();
  return {
    version:"CDG_RENDER_TIMELINE_V1",clock_origin_seconds:0,
    first_real_voice_seconds:o.first_real_voice_seconds,first_sung_vocal_seconds:o.first_sung_word_seconds,
    opening:{
      enabled:o.enabled,render_screen:o.enabled,preview_enabled:o.preview_enabled,cdg_enabled:o.cdg_enabled,
      start_seconds:0,duration_seconds:o.duration,end_seconds:o.end,auto_duration_seconds:o.auto_duration_seconds,
      rule:o.rule,first_syllable_buffer_seconds:o.first_syllable_buffer_seconds
    },
    policy:{json_is_source_of_truth:true,synthetic_events_affect_opening:false,composer_intro_delay_seconds:0,preserve_original_audio_clock:true}
  };
}
'''
    t=between(t,"function pvOpeningDecision(){","function pvEndingDecision(){",new_open,"opening functions")

    new_end=r'''function pvEndingDecision(){
  const outroDuration=8,audioDuration=Math.max(0,Number(S.duration||S.audio?.duration||0));
  const v=cdgVisibilitySettings(),autoDuration=Math.min(outroDuration,audioDuration);
  const previewDuration=v.endingPreview?autoDuration:0;
  return {
    preview_enabled:v.endingPreview,cdg_enabled:v.endingCdg,
    preview_start:previewDuration>0?Math.max(0,audioDuration-previewDuration):audioDuration,
    preview_end:audioDuration,preview_duration:previewDuration,duration:previewDuration,
    renderer_outro_enabled:v.endingCdg,renderer_outro_duration_seconds:v.endingCdg?outroDuration:0,
    basis:"Preview y CDG obedecen controles independientes; timings musicales no cambian"
  };
}
'''
    t=between(t,"function pvEndingDecision(){","function pvTimelineDiagnostic(){",new_end,"ending function")

    t=one(t,'if(opening.duration>0&&now>=opening.start&&now<opening.end) phase="OPENING";',
          'if(opening.preview_duration>0&&now>=opening.preview_start&&now<opening.preview_end) phase="OPENING";',"diagnostic opening")
    t=one(t,'else if(ending.preview_end>0&&now>=ending.preview_start&&now<=ending.preview_end) phase="ENDING";',
          'else if(ending.preview_duration>0&&now>=ending.preview_start&&now<=ending.preview_end) phase="ENDING";',"diagnostic ending")

    t=one(t,
'''function pvTimelineState(t){
  const opening=pvOpeningDecision();
  const ending=pvEndingDecision();
  if(opening.duration>0&&t>=opening.start&&t<opening.end) return {phase:"OPENING",opening};
  if(ending.preview_end>0&&t>=ending.preview_start&&t<=ending.preview_end) return {phase:"ENDING",ending};
  const inst=pvInstrumentalState(t);
  if(inst) return {phase:"INSTRUMENTAL",instrumental:inst};
  return {phase:"KARAOKE"};
}''',
'''function pvTimelineState(t){
  const opening=pvOpeningDecision(),ending=pvEndingDecision();
  if(opening.preview_duration>0&&t>=opening.preview_start&&t<opening.preview_end) return {phase:"OPENING",opening};
  if(ending.preview_duration>0&&t>=ending.preview_start&&t<=ending.preview_end) return {phase:"ENDING",ending};
  const inst=pvInstrumentalState(t);
  if(inst) return {phase:"INSTRUMENTAL",instrumental:inst};
  return {phase:"KARAOKE"};
}''',"timeline state")

    t=one(t,'if(PV.mode==="intro"){ pvDrawIntro(); return; }',
'''if(PV.mode==="intro"){
    if(!cdgVisibilitySettings().openingPreview){pvx.fillStyle="#000";pvx.fillRect(0,0,PV.VW,PV.VH);$("#pvInfo").textContent="OPENING · Preview desactivado";return;}
    pvDrawIntro();return;
  }''',"direct opening")
    t=one(t,'if(PV.mode==="outro"){ pvDrawOutro(); return; }',
'''if(PV.mode==="outro"){
    if(!cdgVisibilitySettings().endingPreview){pvx.fillStyle="#000";pvx.fillRect(0,0,PV.VW,PV.VH);$("#pvInfo").textContent="ENDING · Preview desactivado";return;}
    pvDrawOutro();return;
  }''',"direct ending")

    helper=r'''function cdgVisibilitySettings(){
  const cs=S.doc?.cdg_settings||{};
  return {
    openingPreview:cs.opening_preview_enabled!==false,openingCdg:cs.opening_cdg_enabled!==false,
    endingPreview:cs.ending_preview_enabled!==false,endingCdg:cs.ending_cdg_enabled!==false
  };
}
function setCdgVisibilitySetting(key,value){
  if(!S.doc)return;
  S.doc.cdg_settings={...(S.doc.cdg_settings||{}),[key]:!!value};
  pvChanged();syncPvControls();
}
'''
    t=one(t,"function syncPvControls(){",helper+"\nfunction syncPvControls(){","visibility helper")

    t=one(t,
'''function syncPvControls(){
  const map={pvLines:S.cfg.linesPerPage,pvFont:S.cfg.fontFamily,pvFontSize:S.cfg.fontSize,pvStroke:S.cfg.strokeWidth,pvLyricY:S.cfg.lyricYOffset,pvOutro1:S.cfg.outroLine1,pvOutro2:S.cfg.outroLine2,pvOutroSize:S.cfg.outroSize,pvOutroTransition:S.cfg.outroTransition};
  Object.entries(map).forEach(([id,v])=>{const el=$("#"+id);if(el)el.value=v});
}''',
'''function syncPvControls(){
  const map={pvLines:S.cfg.linesPerPage,pvFont:S.cfg.fontFamily,pvFontSize:S.cfg.fontSize,pvStroke:S.cfg.strokeWidth,pvLyricY:S.cfg.lyricYOffset,pvOutro1:S.cfg.outroLine1,pvOutro2:S.cfg.outroLine2,pvOutroSize:S.cfg.outroSize,pvOutroTransition:S.cfg.outroTransition};
  Object.entries(map).forEach(([id,v])=>{const el=$("#"+id);if(el)el.value=v});
  const v=cdgVisibilitySettings();
  const checks={pvOpeningPreviewEnabled:v.openingPreview,pvOpeningCdgEnabled:v.openingCdg,pvEndingPreviewEnabled:v.endingPreview,pvEndingCdgEnabled:v.endingCdg};
  Object.entries(checks).forEach(([id,val])=>{const el=$("#"+id);if(el)el.checked=!!val;});
}''',"sync controls")

    t=one(t,
'''syncPvControls();

if($("#btnRoleNone"))''',
'''syncPvControls();
[
  ["#pvOpeningPreviewEnabled","opening_preview_enabled"],["#pvOpeningCdgEnabled","opening_cdg_enabled"],
  ["#pvEndingPreviewEnabled","ending_preview_enabled"],["#pvEndingCdgEnabled","ending_cdg_enabled"]
].forEach(([id,key])=>{const el=$(id);if(el)el.onchange=()=>setCdgVisibilitySetting(key,el.checked);});

if($("#btnRoleNone"))''',"visibility handlers")

    t=one(t,
'''  out.cdg_settings = {...(out.cdg_settings||{}), lines_per_page:S.cfg.linesPerPage, intro_mode:"auto", intro_duration_seconds:6, intro_short_duration_seconds:3,
    font_family:S.cfg.fontFamily,font_size:S.cfg.fontSize,stroke_width:S.cfg.strokeWidth,lyric_y_offset:S.cfg.lyricYOffset,
    outro_line1:S.cfg.outroLine1,outro_line2:S.cfg.outroLine2,outro_size:S.cfg.outroSize,outro_x:S.cfg.outroX,outro_y:S.cfg.outroY,outro_transition:S.cfg.outroTransition};''',
'''  const vis=cdgVisibilitySettings();
  out.cdg_settings = {...(out.cdg_settings||{}), lines_per_page:S.cfg.linesPerPage, intro_mode:"auto", intro_duration_seconds:6, intro_short_duration_seconds:3,
    font_family:S.cfg.fontFamily,font_size:S.cfg.fontSize,stroke_width:S.cfg.strokeWidth,lyric_y_offset:S.cfg.lyricYOffset,
    outro_line1:S.cfg.outroLine1,outro_line2:S.cfg.outroLine2,outro_size:S.cfg.outroSize,outro_x:S.cfg.outroX,outro_y:S.cfg.outroY,outro_transition:S.cfg.outroTransition,
    opening_preview_enabled:vis.openingPreview,opening_cdg_enabled:vis.openingCdg,ending_preview_enabled:vis.endingPreview,ending_cdg_enabled:vis.endingCdg};''',"buildExport flags")

    restore='''    if(window._restore.cdg_settings) S.doc.cdg_settings=window._restore.cdg_settings;'''
    t=one(t,restore,restore+"\n    syncPvControls();","restore flags")

    t=one(t,
'''    merged_spoken_intervals:pvMergedSpokenIntervals(),
    instrumental_config:PV.cfg.instrumental,''',
'''    merged_spoken_intervals:pvMergedSpokenIntervals(),
    screen_visibility:(()=>{const v=cdgVisibilitySettings();return {
      opening_preview_enabled:v.openingPreview,opening_cdg_enabled:v.openingCdg,
      ending_preview_enabled:v.endingPreview,ending_cdg_enabled:v.endingCdg
    };})(),
    instrumental_config:PV.cfg.instrumental,''',"diagnostic flags")

    p.write_text(t,encoding="utf-8")
    print("EDITOR_PATCH=OK")

def patch_normalize(p):
    t=p.read_text(encoding="utf-8")
    if NORMALIZE_MARKER in t:
        print("NORMALIZE_ALREADY=YES"); return
    t=one(t,"    duration: float\n    render_timeline: dict",
          "    duration: float\n    outro_enabled: bool  # DJGABO_ENDING_ENABLE_NORMALIZER_V1\n    render_timeline: dict","normalized field")
    t=one(t,'''        duration=doc["song"].get("duration", 0.0),
        render_timeline=render_timeline,''',
          '''        duration=doc["song"].get("duration", 0.0),
        outro_enabled=bool((doc.get("cdg_settings") or {}).get("ending_cdg_enabled", True)),
        render_timeline=render_timeline,''',"normalized ctor")
    t=one(t,'''        f"outro_background = {_q(assets / style['outro_background'])}",
        f"title_color = {_q(style['title_color'])}",''',
          '''        f"outro_background = {_q(assets / style['outro_background'])}",
        f"outro_enabled = {str(bool(n.outro_enabled)).lower()}",
        f"title_color = {_q(style['title_color'])}",''',"toml flag")
    p.write_text(t,encoding="utf-8")
    print("NORMALIZE_PATCH=OK")

def patch_config(p):
    t=p.read_text(encoding="utf-8")
    if CONFIG_MARKER in t:
        print("CONFIG_ALREADY=YES"); return
    t=one(t,'''    outro_transition: str = "centertexttoplogobottomtext"
    outro_text_line1: str = "THANK YOU FOR SINGING!"''',
          '''    # DJGABO_ENDING_ENABLE_CONFIG_V1
    outro_enabled: bool = True
    outro_transition: str = "centertexttoplogobottomtext"
    outro_text_line1: str = "THANK YOU FOR SINGING!"''',"config flag")
    p.write_text(t,encoding="utf-8")
    print("CONFIG_PATCH=OK")

def patch_composer(p):
    t=p.read_text(encoding="utf-8")
    if COMPOSER_MARKER in t:
        print("COMPOSER_ALREADY=YES"); return
    start="            # NOTE If video padding is not added to the end of the song"
    end="            # Write CDG and MP3 data to ZIP file"
    new=r'''            # DJGABO_ENDING_ENABLE_COMPOSER_V1
            if self.config.outro_enabled:
                if self.config.clear_mode == LyricClearMode.PAGE:
                    self.writer.queue_packets([no_instruction()] * 3 * CDG_FPS)
                OUTRO_DURATION = 2400
                end=max(int(self.audio.duration_seconds * CDG_FPS),self.writer.packets_queued+OUTRO_DURATION)
                padding_before_outro=(end-OUTRO_DURATION)-self.writer.packets_queued
                self.writer.queue_packets([no_instruction()] * padding_before_outro)
                self._compose_outro(end)
                self.logger.info("karaoke file composed")
                outro_silence: AudioSegment=AudioSegment.silent(
                    ((self.writer.packets_queued*1000//CDG_FPS)-int(self.audio.duration_seconds*1000)),
                    frame_rate=song.frame_rate,
                )
                self.audio+=outro_silence
            else:
                end=max(int(self.audio.duration_seconds*CDG_FPS),self.writer.packets_queued)
                if self.writer.packets_queued<end:
                    self.writer.queue_packets([no_instruction()]*(end-self.writer.packets_queued))
                self.logger.info("Ending disabled; no outro screen composed. Audio/CDG clock preserved.")

            # Write CDG and MP3 data to ZIP file'''
    t=between(t,start,end,new,"composer ending")
    p.write_text(t,encoding="utf-8")
    print("COMPOSER_PATCH=OK")

def patch_yellow(editor,style):
    t=editor.read_text(encoding="utf-8")
    marker="DJGABO_POWER_YELLOW_V1"
    if marker not in t:
        old='b.role==="female"?"#FF4FA3":b.role==="male"?"#32B7FF":b.role==="duet"?"#7ED957":"#F2A900"'
        new='b.role==="female"?"#FF4FA3":b.role==="male"?"#32B7FF":b.role==="duet"?"#7ED957":"#FFFF00"'
        if t.count(old)!=1: raise RuntimeError("preview yellow anchor="+str(t.count(old)))
        t=t.replace(old,new,1).replace("</style>","/* "+marker+" */\n</style>",1)
        editor.write_text(t,encoding="utf-8")
    import json
    d=json.loads(style.read_text(encoding="utf-8"))
    d["highlight_default"]="#FFFF00"; d["highlight_color"]="#FFFF00"
    style.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PATCH_YELLOW=OK")

def main():
    if len(sys.argv)==3:
        patch_yellow(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
        return
    if len(sys.argv)!=5:
        raise SystemExit("uso: patch_production.py editor style | editor normalize composer config")
    e,n,c,g=[Path(x).resolve() for x in sys.argv[1:]]
    patch_editor(e);patch_normalize(n);patch_composer(c);patch_config(g)
    print("PATCH_CONTROLS=OK")

if __name__=="__main__":
    main()
