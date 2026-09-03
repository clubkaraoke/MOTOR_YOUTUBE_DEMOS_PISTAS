#!/usr/bin/env python3
import sqlite3, json, os, pathlib

DBS={'TEST':'/var/lib/djgabo-cdg-ia-test/local.db','PROD':'/var/lib/djgabo-cdg/local.db'}
terms=['armonia','armonía','laura','mujeres enga','amor rebelde']

for label,path in DBS.items():
    print(f'=== {label} {path} ===')
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    cols=[r[1] for r in con.execute('pragma table_info(jobs)')]
    rows=con.execute("select * from jobs order by id desc limit 180").fetchall()
    found=0
    for r in rows:
        d=dict(r)
        hay=' '.join(str(d.get(k) or '') for k in ('artist','title','voice_filename','instrumental_filename')).lower()
        if any(t in hay for t in terms):
            found+=1
            keys=['id','artist','title','status','origin','created','updated','voice_filename','voice_original_filename',
                  'instrumental_filename','duration','size_bytes','voice_drive_status','voice_drive_id',
                  'dropbox_status','dropbox_path','dropbox_folder_id','dropbox_display_path',
                  'instrumental_dropbox_path','instrumental_dropbox_id','timings_drive_status','timings_drive_id',
                  'sheet_master_status','cdg_local_filename','canonical_name','render_status','render_progress']
            out={k:d.get(k) for k in keys if k in d}
            pj=d.get('project_json')
            if pj:
                try:
                    p=json.loads(pj)
                    words=[w for s in p.get('segments',[]) for w in s.get('words',[])]
                    out['project_words']=len(words)
                    out['timed_words']=sum(w.get('start_time') is not None for w in words)
                    out['voice_gaps']=len((p.get('ai') or {}).get('voice_gaps') or [])
                    out['ai_engine']=(p.get('ai') or {}).get('engine')
                except Exception as e:
                    out['project_error']=str(e)
            print(json.dumps(out,ensure_ascii=False))
    print('FOUND',found)
    con.close()
    print()

for root_label,root in [('TEST','/var/lib/djgabo-cdg-ia-test/jobs'),('PROD','/var/lib/djgabo-cdg/jobs')]:
    print(f'=== {root_label} JOB FOLDERS 0075-0085 ===')
    for n in range(75,86):
        jid=f'LET-{n:04d}'; p=pathlib.Path(root)/jid
        if not p.is_dir(): continue
        files=[]
        for x in sorted(p.iterdir()):
            if x.is_file():
                files.append((x.name,x.stat().st_size))
        print(jid,json.dumps(files,ensure_ascii=False))
