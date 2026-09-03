#!/usr/bin/env python3
import sqlite3, shutil, pathlib, json, datetime, os, sys

SRC_DB=pathlib.Path('/var/lib/djgabo-cdg-ia-test/local.db')
DST_DB=pathlib.Path('/var/lib/djgabo-cdg/local.db')
SRC_JOBS=pathlib.Path('/var/lib/djgabo-cdg-ia-test/jobs')
DST_JOBS=pathlib.Path('/var/lib/djgabo-cdg/jobs')
IDS=['LET-0078','LET-0079','LET-0080']

stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup_dir=pathlib.Path('/var/lib/djgabo-cdg/backups')/f'import_ia_test_{stamp}'
backup_dir.mkdir(parents=True,exist_ok=True)
shutil.copy2(DST_DB,backup_dir/'local.db.before')

src=sqlite3.connect(SRC_DB)
src.row_factory=sqlite3.Row
dst=sqlite3.connect(DST_DB)
dst.row_factory=sqlite3.Row

try:
    src_cols=[r[1] for r in src.execute('pragma table_info(jobs)')]
    dst_cols=[r[1] for r in dst.execute('pragma table_info(jobs)')]
    common=[c for c in src_cols if c in dst_cols]
    if 'id' not in common:
        raise RuntimeError('No existe columna id común.')

    rows={}
    for jid in IDS:
        row=src.execute('select * from jobs where id=?',(jid,)).fetchone()
        if not row:
            raise RuntimeError(f'No encuentro {jid} en IA TEST.')
        if dst.execute('select 1 from jobs where id=?',(jid,)).fetchone():
            raise RuntimeError(f'{jid} ya existe en producción; aborto sin sobrescribir.')
        folder=SRC_JOBS/jid
        if not folder.is_dir():
            raise RuntimeError(f'Falta carpeta fuente {folder}.')
        rows[jid]=dict(row)

    # Copy folders first into temporary names; no production job becomes visible yet.
    staged=[]
    for jid in IDS:
        src_dir=SRC_JOBS/jid
        tmp_dir=DST_JOBS/f'.{jid}.importing'
        final_dir=DST_JOBS/jid
        if tmp_dir.exists(): shutil.rmtree(tmp_dir)
        if final_dir.exists():
            raise RuntimeError(f'La carpeta {final_dir} ya existe; aborto.')
        shutil.copytree(src_dir,tmp_dir)
        staged.append((tmp_dir,final_dir))

    # Insert exact project data, but do not claim a CDG file is currently cached after
    # the test service restarts. The project remains fully timed and ready to re-export.
    qmarks=','.join('?' for _ in common)
    sql='insert into jobs ('+','.join('"'+c+'"' for c in common)+') values ('+qmarks+')'
    with dst:
        for jid in IDS:
            d=rows[jid]
            d['origin']='IA_TEST_IMPORTADO'
            if 'render_status' in d: d['render_status']=''
            if 'render_progress' in d: d['render_progress']=0
            if 'render_error' in d: d['render_error']=''
            if 'cdg_local_filename' in d: d['cdg_local_filename']=''
            if 'dropbox_status' in d: d['dropbox_status']='TEST_LOCAL_IMPORTADO'
            if 'voice_drive_status' in d: d['voice_drive_status']='TEST_LOCAL'
            if 'timings_drive_status' in d: d['timings_drive_status']='TEST_LOCAL'
            if 'sheet_master_status' in d: d['sheet_master_status']='TEST_LOCAL'
            vals=[d.get(c) for c in common]
            dst.execute(sql,vals)

    # Make folders visible only after DB insert succeeded.
    for tmp_dir,final_dir in staged:
        tmp_dir.rename(final_dir)

    # Ownership to production service account when run as root.
    try:
        import pwd, grp
        uid=pwd.getpwnam('djgabo-cdg').pw_uid
        gid=grp.getgrnam('djgabo-cdg').gr_gid
        for jid in IDS:
            root=DST_JOBS/jid
            for base,dirs,files in os.walk(root):
                os.chown(base,uid,gid)
                for name in dirs:
                    os.chown(os.path.join(base,name),uid,gid)
                for name in files:
                    os.chown(os.path.join(base,name),uid,gid)
    except Exception as e:
        print('WARN ownership:',e)

    print('BACKUP='+str(backup_dir/'local.db.before'))
    for jid in IDS:
        r=dst.execute('select id,artist,title,status,origin,render_status,dropbox_status from jobs where id=?',(jid,)).fetchone()
        folder=DST_JOBS/jid
        pj=folder/'proyecto.timings.json'
        words=timed=0
        if pj.is_file():
            doc=json.loads(pj.read_text(encoding='utf-8'))
            ws=[w for s in doc.get('segments',[]) for w in s.get('words',[])]
            words=len(ws); timed=sum(w.get('start_time') is not None for w in ws)
        print(json.dumps(dict(r),ensure_ascii=False),f'folder={folder.is_dir()} words={words} timed={timed}')
    print('IMPORT_THREE_IA_TEST_PROJECTS=OK')
finally:
    src.close(); dst.close()
