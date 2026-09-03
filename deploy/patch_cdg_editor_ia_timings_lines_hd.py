#!/usr/bin/env python3
from pathlib import Path

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
EDITOR=ROOT/'editor_v1'/'index.html'

s=SERVER.read_text(encoding='utf-8')
start=s.find('def _ai_project_from_words')
end=s.find("@app.post('/api/ai/create-job')",start)
if start<0 or end<0:
    raise SystemExit('No encontré bloque IA en server.py')

block=r'''def _ai_segment_scribe_words(items):
    """Agrupa palabras de Scribe en líneas legibles usando puntuación + pausas, sin tocar timings."""
    clean=[dict(w) for w in (items or []) if str(w.get('text') or '').strip()]
    groups=[]; line=[]
    def flush(paragraph_break=False):
        nonlocal line
        if line:
            groups.append(('line',line)); line=[]
        if paragraph_break and groups and groups[-1][0]!='break':
            groups.append(('break',[]))
    for idx,item in enumerate(clean):
        line.append(item)
        txt=str(item.get('text') or '').strip()
        next_item=clean[idx+1] if idx+1<len(clean) else None
        gap=0.0
        if next_item is not None:
            try: gap=max(0.0,float(next_item.get('start'))-float(item.get('end')))
            except Exception: gap=0.0
        chars=len(' '.join(str(x.get('text') or '').strip() for x in line))
        strong=bool(re.search(r'[.!?…]["”’\']?$',txt))
        comma=bool(re.search(r'[,;:]["”’\']?$',txt))
        final=next_item is None
        cut=final or (strong and len(line)>=2) or (gap>=0.78 and len(line)>=2)
        if not cut and chars>=34 and (comma or len(line)>=6): cut=True
        if not cut and len(line)>=8: cut=True
        if cut:
            flush(paragraph_break=(gap>=1.45 or (strong and gap>=0.85)))
    flush()
    while groups and groups[-1][0]=='break': groups.pop()
    return groups

def _ai_project_from_words(artist,title,voice_name,duration,lyrics,ai_words,source_mode,jid=''):
    """Convierte Scribe a proyecto nativo del editor conservando cada timing."""
    clean=[w for w in (ai_words or []) if str(w.get('text') or '').strip()]
    segments=[]; wi=0; si=0
    def make_segment(tokens):
        nonlocal wi,si
        words=[]
        for item in tokens:
            txt=str(item.get('master_text') or item.get('text') or '').strip()
            if not txt: continue
            a=item.get('start'); b=item.get('end')
            words.append({
                'id':f'w{wi:04d}','text':txt,
                'start_time':round(float(a),6) if a is not None else None,
                'end_time':round(float(b),6) if b is not None else None,
                'locked':False,'spoken':False,'vocal_role':None,
                'ai_confidence':float(item.get('confidence') or 0),
                'ai_status':str(item.get('qa_status') or ''),
                'scribe_text':item.get('scribe_text'),
                'ai_match_type':str(item.get('match_type') or ''),
            })
            wi+=1
        if words:
            segments.append({'id':f's{si:04d}','kind':'lyric','text':' '.join(w['text'] for w in words),'words':words}); si+=1
    def add_break():
        nonlocal si
        if segments and segments[-1].get('kind')!='break':
            segments.append({'id':f's{si:04d}','kind':'break','text':'','words':[]}); si+=1

    if source_mode=='compare_master' and lyrics.strip():
        pos=0
        for raw in lyrics.replace('\r','').split('\n'):
            line=raw.strip()
            if not line:
                add_break(); continue
            batch=[]
            for master_text in line.split():
                item=dict(clean[pos]) if pos<len(clean) else {
                    'text':master_text,'start':None,'end':None,'confidence':0,
                    'qa_status':'red','match_type':'missing'
                }
                item['master_text']=master_text; batch.append(item); pos+=1
            make_segment(batch)
    else:
        for kind,tokens in _ai_segment_scribe_words(clean):
            if kind=='break': add_break()
            else: make_segment(tokens)

    while segments and segments[-1].get('kind')=='break': segments.pop()
    audio_key=('online-'+str(jid)) if jid else ''
    return {
        'version':1,
        'song':{'artist':artist,'title':title,'audio_file':voice_name,'audio_sha1':audio_key,'duration':float(duration or 0)},
        'calibration_ms':0,'segments':segments,
        'ai':{'engine':'elevenlabs-scribe-v2','source_mode':source_mode,'format_version':2,
              'line_mode':'master' if source_mode=='compare_master' else 'scribe_punctuation_pauses',
              'generated_at':now()}
    }

def _project_lyrics(project):
    return '\n'.join('' if seg.get('kind')=='break' else str(seg.get('text') or '')
                     for seg in (project.get('segments') or [])).strip()

'''
s=s[:start]+block+s[end:]

old="""        if lyrics:
            final_lyrics=lyrics
        else:
            final_lyrics=str((payload.get('scribe') or {}).get('text') or '').strip()
            if not final_lyrics: final_lyrics=' '.join(str(w.get('text') or '').strip() for w in ai_words).strip()

        project=_ai_project_from_words(artist,title,voice_name,duration,final_lyrics,ai_words,source_mode)"""
new="""        seed_lyrics=lyrics if source_mode=='compare_master' else ''
        project=_ai_project_from_words(artist,title,voice_name,duration,seed_lyrics,ai_words,source_mode,jid=jid)
        final_lyrics=lyrics if source_mode=='compare_master' else _project_lyrics(project)
        if not final_lyrics:
            final_lyrics=str((payload.get('scribe') or {}).get('text') or '').strip()"""
if old not in s:
    raise SystemExit('No encontré bloque final_lyrics actual')
s=s.replace(old,new)
SERVER.write_text(s,encoding='utf-8')

h=EDITOR.read_text(encoding='utf-8')
old_restore='''  if(window._restore && window._restore.song && window._restore.song.audio_sha1 === S.sha1){
    S.doc.segments = window._restore.segments;
    reindex(); refreshEnds(0);
    toast("Marcas restauradas.");
  }
  window._restore = null;'''
new_restore='''  const restoreOnline=!!(pendingFile && pendingFile.online);
  const restoreOk=!!(window._restore && window._restore.song &&
    (restoreOnline || window._restore.song.audio_sha1 === S.sha1));
  if(restoreOk){
    S.doc.segments = window._restore.segments || [];
    if(window._restore.ai) S.doc.ai=window._restore.ai;
    if(window._restore.cdg_settings) S.doc.cdg_settings=window._restore.cdg_settings;
    if(restoreOnline){
      S.doc.song.audio_sha1=S.sha1;
      S.doc.song.audio_file=S.audioName;
      S.doc.song.duration=S.duration;
    }
    reindex(); refreshEnds(0);
    const n=timedCount();
    toast(n ? ("IA restaurada: "+n+"/"+S.words.length+" palabras sincronizadas.") : "Proyecto restaurado.");
  }
  window._restore = null;'''
if old_restore in h:
    h=h.replace(old_restore,new_restore)

old_resize='''function pvResize(){
  const box = $("#pvBox").getBoundingClientRect();
  const scale = Math.max(1, Math.min(3, Math.floor(Math.min(box.width / PV.VW, box.height / PV.VH) * 2) / 2));
  pvc.style.width = (PV.VW * scale) + "px";
  pvc.style.height = (PV.VH * scale) + "px";
  const dpr = window.devicePixelRatio || 1;
  pvc.width = PV.VW * dpr; pvc.height = PV.VH * dpr;
  pvx.setTransform(dpr, 0, 0, dpr, 0, 0);
}'''
new_resize='''function pvResize(){
  const box = $("#pvBox").getBoundingClientRect();
  const scale = Math.max(1, Math.min(3, Math.floor(Math.min(box.width / PV.VW, box.height / PV.VH) * 2) / 2));
  const cssW=PV.VW*scale, cssH=PV.VH*scale;
  pvc.style.width = cssW + "px";
  pvc.style.height = cssH + "px";
  const dpr = window.devicePixelRatio || 1;
  const ss = 2;
  pvc.width = Math.max(1,Math.round(cssW*dpr*ss));
  pvc.height = Math.max(1,Math.round(cssH*dpr*ss));
  pvx.setTransform(dpr*scale*ss, 0, 0, dpr*scale*ss, 0, 0);
  pvx.imageSmoothingEnabled=true;
  if("imageSmoothingQuality" in pvx) pvx.imageSmoothingQuality="high";
}'''
if old_resize in h:
    h=h.replace(old_resize,new_resize)

old_paint='''  resize(); pvResize(); paintNow(); paintCounter(); paintLyrics(); draw();'''
new_paint='''  resize(); pvResize(); paintNow(); paintCounter(); paintLyrics(); draw();
  try{ PV.plan=pvPlan(); }catch(_){ PV.plan=null; }
  pvDraw();'''
if old_paint in h and new_paint not in h:
    h=h.replace(old_paint,new_paint,1)

h=h.replace('Texto blanco · barrido por rol · borde negro. Los cambios se guardan con esta canción y pasan al render final.',
            'Preview HD nítido · texto blanco · barrido por rol · borde negro. El CD+G final conserva su geometría real 300×216.')
EDITOR.write_text(h,encoding='utf-8')
print('PATCH_TIMINGS_LINES_HD=OK')
