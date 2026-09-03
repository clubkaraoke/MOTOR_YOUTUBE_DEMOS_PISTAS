#!/usr/bin/env python3
import json, time, requests, sys
from pathlib import Path

ROOT=Path('/opt/djgabo-cdg-ia-test')
sys.path.insert(0,str(ROOT))
import server

with server.db() as c:
    r=c.execute("select * from jobs where origin='IA_TEST' order by updated desc limit 1").fetchone()
    if not r:
        raise SystemExit('NO_IA_TEST_JOB')
    row=dict(r)

jid=row['id']
jobdir=Path(server.JOBS)/jid
stamp=time.strftime('%Y%m%d-%H%M%S')
(jobdir/f'backup_pre_7_improvements_{stamp}.json').write_text(row.get('project_json') or '{}',encoding='utf-8')
(jobdir/f'backup_pre_7_improvements_{stamp}.txt').write_text(str(row.get('lyrics_corrected') or ''),encoding='utf-8')

voice=jobdir/row['voice_filename']
started=time.monotonic()
with voice.open('rb') as fh:
    rr=requests.post(
        'http://127.0.0.1:8097/api/elevenlabs/transcribe',
        files={'audio':(voice.name,fh,'audio/mpeg')},
        data={'lyrics':'','language_code':'spa'},
        timeout=(30,1200)
    )
rr.raise_for_status()
payload=rr.json()
words=payload.get('words') or []
if not words:
    raise SystemExit('SCRIBE_RETURNED_NO_WORDS')

project=server._ai_project_from_words(
    row['artist'],row['title'],row['voice_filename'],row.get('duration') or 0,
    '',words,'scribe_only',jid=jid
)
gaps=server._detect_untranscribed_voice(voice,words,row.get('duration') or 0)
project.setdefault('ai',{})['voice_gaps']=gaps
project['ai']['scribe_word_count']=len(words)
project['ai']['coverage_check']='audio_energy_vs_scribe'
lyrics=server._project_lyrics(project)
total=sum(len(seg.get('words',[])) for seg in project.get('segments',[]))
timed=sum(1 for seg in project.get('segments',[]) for w in seg.get('words',[]) if w.get('start_time') is not None)

with server.db() as c:
    c.execute(
        "update jobs set project_json=?,lyrics_corrected=?,lyrics_moises=?,updated=? where id=?",
        (json.dumps(project,ensure_ascii=False),lyrics,lyrics,server.now(),jid)
    )
    server.log(c,jid,'SCRIBE V2 · RECONSTRUIR COMPLETO 7 MEJORAS','scribe_only')
(jobdir/'letra_moises.txt').write_text(lyrics,encoding='utf-8')

print('REBUILT_JOB='+jid)
print('SCRIBE_WORDS='+str(len(words)))
print('PROJECT_WORDS='+str(total))
print('TIMED_WORDS='+str(timed))
print('VOICE_GAPS='+str(len(gaps)))
for g in gaps:
    print('VOICE_GAP='+json.dumps(g,ensure_ascii=False))
print('ELAPSED='+str(round(time.monotonic()-started,3)))

assert len(words)==total==timed, (len(words),total,timed)
assert project.get('ai',{}).get('source_mode')=='scribe_only'
