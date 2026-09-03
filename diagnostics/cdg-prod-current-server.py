from __future__ import annotations
import base64, json, os, re, secrets, shutil, sqlite3, subprocess, sys, tempfile, time, zipfile, unicodedata, difflib, threading, math
from contextlib import contextmanager
from array import array
from datetime import datetime
from io import BytesIO
from pathlib import Path
from flask import Flask, request, send_file, jsonify, abort, redirect, Response
import requests
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT=Path(__file__).resolve().parent

def env_bool(name, default=False):
    value=os.getenv(name)
    if value is None: return bool(default)
    return str(value).strip().lower() in ('1','true','yes','si','on')

ENVIRONMENT=str(os.getenv('DJGABO_ENV') or 'local').strip().lower()
IS_PRODUCTION=ENVIRONMENT in ('production','prod','server','ovh')
DATA=Path(os.getenv('DJGABO_DATA_DIR') or (ROOT/'data')).expanduser().resolve()
JOBS=Path(os.getenv('DJGABO_JOBS_DIR') or (DATA/'jobs')).expanduser().resolve()
OUTPUT=Path(os.getenv('DJGABO_OUTPUT_DIR') or (ROOT/'output' if not IS_PRODUCTION else DATA/'output')).expanduser().resolve()
DB=Path(os.getenv('DJGABO_DB_PATH') or (DATA/'local.db')).expanduser().resolve()
RENDER=ROOT/'renderer'/'render.py'
DROPBOX_CFG=DATA/'dropbox_oauth.json'
# LOCAL 16.4: la configuración del puente Drive ya no depende de la carpeta/version
# extraída. Se conserva también en AppData del usuario para que ADMIN la configure
# una sola vez y Augusto/Valeria reutilicen siempre la misma conexión.
GLOBAL_CFG_ROOT=Path(os.getenv('DJGABO_CONFIG_DIR') or os.getenv('LOCALAPPDATA') or (Path.home()/'.djgabo_cdg')).expanduser()/'DJGABO_CONTROL_CDG'
DRIVE_BRIDGE_CFG=DATA/'drive_bridge.json'                 # espejo portable/local
DRIVE_BRIDGE_CFG_GLOBAL=GLOBAL_CFG_ROOT/'drive_bridge.json'  # fuente persistente
DRIVE_BRIDGE_KEY_DEFAULT=str(os.getenv('DJGABO_DRIVE_BRIDGE_KEY') or '')
DRIVE_AUDIO_TOKEN_SECRET=str(os.getenv('DJGABO_DRIVE_AUDIO_TOKEN_SECRET') or '')
DRIVE_ACAPELLAS_FOLDER_ID=str(os.getenv('DJGABO_DRIVE_ACAPELLAS_FOLDER_ID') or '112a-UKRFBUHylAN1NXu19eOELxiAWT6y').strip()
DRIVE_TIMINGS_FOLDER_ID=str(os.getenv('DJGABO_DRIVE_TIMINGS_FOLDER_ID') or '1Pj10IsZ_tUh8OLpIhHnhJvCcp4CiXWlp').strip()
# Las URL /exec identifican despliegues públicos, no autentican. Se conservan
# como respaldo para autorreparar un endpoint guardado que Google deje en 404.
DRIVE_BRIDGE_URL_DEFAULT=str(os.getenv('DJGABO_DRIVE_BRIDGE_URL') or 'https://script.google.com/macros/s/AKfycbwu5f2voTdx8V4K2gbZDOqAWKyBh2ouv92xH79UzjyytWM-HBBB8DV5WLwTZz8w0ang/exec')
DRIVE_BRIDGE_URL_LEGACY=str(os.getenv('DJGABO_DRIVE_BRIDGE_URL_LEGACY') or 'https://script.google.com/macros/s/AKfycbxHIS59PUw-U4bnxKcAnOVi7I90XkO4CPwbeKMkNEfGwU5D70Q5USwyttaXwiJDASZ-/exec')
PUBLIC_BASE_URL=str(os.getenv('DJGABO_PUBLIC_BASE_URL') or '').strip().rstrip('/')
DROPBOX_REDIRECT_URI=str(os.getenv('DROPBOX_REDIRECT_URI') or ((PUBLIC_BASE_URL+'/dropbox/callback') if PUBLIC_BASE_URL else 'http://localhost:8765/dropbox/callback'))
DROPBOX_APP_KEY_DEFAULT=str(os.getenv('DROPBOX_APP_KEY') or '2ld58g05ug5g6i1')
DROPBOX_OAUTH_STATES={}
LEGACY_SEED=ROOT/'legacy_seed'
HISTORICAL_ID_FLOOR=75  # el Sheet antiguo ya usa LET-0003..LET-0075; los nuevos empiezan en 0076
PENDING_CDG_DIR=DATA/'pending'/'cdg'
PENDING_WAV_DIR=DATA/'pending'/'wav'
VOICE_CACHE_DIR=Path(os.getenv('DJGABO_VOICE_CACHE_DIR') or (DATA/'voice_cache')).expanduser().resolve()
for p in (DATA,DB.parent,JOBS,OUTPUT,PENDING_CDG_DIR,PENDING_WAV_DIR,VOICE_CACHE_DIR): p.mkdir(parents=True,exist_ok=True)

def migrate_previous_install_data():
    '''LOCAL 16 ONLINE adopta primero LOCAL 15 (si existe) y, como respaldo, LOCAL 14.
    Conserva base, trabajos locales, salidas y sesión Dropbox. No pisa una LOCAL 16 ya iniciada.
    '''
    if DB.exists() or os.getenv('DJGABO_SKIP_PREV_MIGRATION')=='1': return
    bases=[]
    for b in (ROOT.parent, ROOT.parent.parent, Path.home()/'Downloads'):
        try:
            b=Path(b)
            if b.exists() and b not in bases: bases.append(b)
        except Exception: pass
    candidates=[]; source_version=''
    for version in ('16','15','14'):
        found=[]
        for base in bases:
            try: found.extend(base.glob(f'CONTROL_CDG_DJGABO_LOCAL_INTEGRADO_{version}*/**/data/local.db'))
            except Exception: pass
        found=[x for x in found if x.is_file() and ROOT not in x.parents]
        if found:
            candidates=found; source_version=version; break
    if not candidates: return
    src_db=max(candidates,key=lambda x:x.stat().st_mtime); src_data=src_db.parent; src_root=src_data.parent
    try:
        shutil.copy2(src_db,DB)
        if (src_data/'jobs').is_dir(): shutil.copytree(src_data/'jobs',JOBS,dirs_exist_ok=True)
        if (src_data/'peaks_cache').is_dir(): shutil.copytree(src_data/'peaks_cache',DATA/'peaks_cache',dirs_exist_ok=True)
        if (src_root/'output').is_dir(): shutil.copytree(src_root/'output',OUTPUT,dirs_exist_ok=True)
        if (src_data/'dropbox_oauth.json').is_file(): shutil.copy2(src_data/'dropbox_oauth.json',DROPBOX_CFG)
        if (src_data/'drive_bridge.json').is_file():
            shutil.copy2(src_data/'drive_bridge.json',DRIVE_BRIDGE_CFG)
            try:
                if not DRIVE_BRIDGE_CFG_GLOBAL.exists():
                    DRIVE_BRIDGE_CFG_GLOBAL.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(src_data/'drive_bridge.json',DRIVE_BRIDGE_CFG_GLOBAL)
            except Exception: pass
        print(f'[LOCAL 16.14 ONLINE CONTROLADO] Datos de LOCAL {source_version} migrados desde:',src_root)
    except Exception as e:
        print(f'[LOCAL 16.2 ONLINE] No se pudo migrar LOCAL {source_version} automáticamente:',e)

migrate_previous_install_data()
app=Flask(__name__)
if env_bool('DJGABO_TRUST_PROXY',IS_PRODUCTION):
    # Debe usarse solamente detrás del Nginx incluido en deploy/.
    app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1,x_port=1)
app.config['MAX_CONTENT_LENGTH']=750*1024*1024  # WAVs grandes: subida multipart, sin Base64
SESSIONS={}
SESSION_ACTIVITY={}
SESSION_TTL_SECONDS=max(900,int(os.getenv('DJGABO_SESSION_TTL_SECONDS') or (43200 if IS_PRODUCTION else 604800)))
ADMIN_PASSWORD=str(os.getenv('DJGABO_ADMIN_PASSWORD') or ('' if IS_PRODUCTION else 'matata22'))
CORRECTORA_PASSWORD=str(os.getenv('DJGABO_CORRECTORA_PASSWORD') or '')
PORTAL_COOKIE_NAME='djgabo_portal_session'
LOGIN_FAILURES={}
LOGIN_RATE_WINDOW=15*60
LOGIN_RATE_MAX=max(3,int(os.getenv('DJGABO_LOGIN_RATE_MAX') or 10))
if IS_PRODUCTION and (len(ADMIN_PASSWORD)<16 or len(CORRECTORA_PASSWORD)<16 or ADMIN_PASSWORD==CORRECTORA_PASSWORD):
    raise RuntimeError('Producción exige claves ADMIN/CORRECTORA distintas y de al menos 16 caracteres.')
if IS_PRODUCTION and (not PUBLIC_BASE_URL.startswith('https://') or not DROPBOX_REDIRECT_URI.startswith('https://')):
    raise RuntimeError('Producción exige DJGABO_PUBLIC_BASE_URL y DROPBOX_REDIRECT_URI con HTTPS.')
EST_P='PENDIENTE DE CORRECCIÓN'; EST_C='EN CORRECCIÓN'; EST_OK='LETRA CORREGIDA'; EST_TERM='KARAOKE TERMINADO'; EST_DEL='ELIMINADO'

# LOCAL 16.14: el navegador no recibe el MP3 completo antes de reproducirlo.
# El backend recupera UNA sola vez el audio mediante la acción antigua `audio`
# del puente ya publicado, lo comparte en memoria entre waveform y reproductor,
# y sirve desde esa copia todos los HTTP Range que solicite el navegador.
_DRIVE_AUDIO_META={}
_DRIVE_CACHE_LOCK=threading.RLock()
_DRIVE_AUDIO_MEMORY={}
_DRIVE_AUDIO_LOADING=set()
_DRIVE_AUDIO_ERRORS={}
_DRIVE_AUDIO_MEMORY_COND=threading.Condition(threading.RLock())
_DRIVE_TOKEN_CACHE={'token':'','expires':0.0}
_DRIVE_TOKEN_LOCK=threading.RLock()
_DRIVE_BACKUP_LOCK=threading.RLock()
_DRIVE_TIMINGS_PENDING={}
_DRIVE_TIMINGS_RUNNING=set()
DRIVE_AUDIO_MEMORY_MAX=3
DRIVE_AUDIO_YIELD_SIZE=256*1024
VOICE_CACHE_MAX_BYTES=max(0,int(float(os.getenv('DJGABO_VOICE_CACHE_MAX_GB') or 8)*1024*1024*1024))
VOICE_CACHE_METADATA_TTL=max(300,int(os.getenv('DJGABO_VOICE_CACHE_METADATA_TTL_SECONDS') or 86400))

_PEAKS_CACHE={}
_PEAK_TASKS={}
_PEAKS_LOCK=threading.RLock()
PEAKS_DIR=DATA/'peaks_cache'; PEAKS_DIR.mkdir(parents=True,exist_ok=True)

_DROPBOX_RECONCILE_CACHE={}
_DROPBOX_RECONCILE_LOCK=threading.RLock()
_DROPBOX_RECONCILE_INFLIGHT=set()
_DROPBOX_RECONCILE_TTL=90            # segundos: evita golpear la API de Dropbox en cada refresco del panel
_DROPBOX_RECONCILE_MAX_PER_PASS=8    # trabajos verificados por tanda en segundo plano

_RENDER_TASKS={}

_AI_TASKS={}
_AI_TASK_LOCK=threading.RLock()
_RENDER_LOCK=threading.RLock()
RENDER_TIMEOUT_SECONDS=max(300,int(os.getenv('DJGABO_RENDER_TIMEOUT_SECONDS') or 1800))
RENDER_CONCURRENCY=max(1,int(os.getenv('DJGABO_RENDER_CONCURRENCY') or 1))
_RENDER_SLOTS=threading.BoundedSemaphore(RENDER_CONCURRENCY)
_CDG_ONLINE_CACHE={}   # Cache caliente; la copia recuperable vive en DATA/pending.
_CDG_CACHE_LOCK=threading.RLock()
_WAV_ONLINE_CACHE={}   # Cache caliente; la copia recuperable vive en DATA/pending.
_WAV_CACHE_LOCK=threading.RLock()

def _pending_paths(folder,jid):
    key=re.sub(r'[^A-Za-z0-9_.-]+','_',str(jid))
    return folder/(key+'.bin'),folder/(key+'.json')

def _pending_disk_put(folder,jid,data,name):
    payload,meta=_pending_paths(folder,jid)
    tmp=payload.with_suffix('.tmp'); tmp.write_bytes(bytes(data)); tmp.replace(payload)
    _atomic_write_json(meta,{'name':str(name),'size':len(data),'updated':time.time()})

def _pending_disk_get(folder,jid):
    payload,meta=_pending_paths(folder,jid)
    try:
        cfg=_read_json_cfg(meta)
        if not payload.is_file() or payload.stat().st_size!=int(cfg.get('size') or -1): return None
        return {'data':payload.read_bytes(),'name':str(cfg.get('name') or payload.name),'used':time.time()}
    except Exception:
        return None

def _pending_disk_pop(folder,jid):
    for path in _pending_paths(folder,jid):
        try: path.unlink(missing_ok=True)
        except Exception: pass

def _cdg_cache_get(jid):
    with _CDG_CACHE_LOCK:
        x=_CDG_ONLINE_CACHE.get(str(jid))
        if not x:
            x=_pending_disk_get(PENDING_CDG_DIR,jid)
            if x: _CDG_ONLINE_CACHE[str(jid)]=x
        return (x.get('data'),x.get('name')) if x else (None,'')

def _cdg_cache_put(jid,data,name):
    with _CDG_CACHE_LOCK:
        _pending_disk_put(PENDING_CDG_DIR,jid,data,name)
        _CDG_ONLINE_CACHE[str(jid)]={'data':bytes(data),'name':str(name),'used':time.time()}
        while len(_CDG_ONLINE_CACHE)>8:
            k=min(_CDG_ONLINE_CACHE,key=lambda x:_CDG_ONLINE_CACHE[x].get('used',0)); _CDG_ONLINE_CACHE.pop(k,None)

def _cdg_cache_pop(jid):
    with _CDG_CACHE_LOCK:
        value=_CDG_ONLINE_CACHE.pop(str(jid),None)
        _pending_disk_pop(PENDING_CDG_DIR,jid)
        return value

def _wav_cache_get(jid):
    with _WAV_CACHE_LOCK:
        x=_WAV_ONLINE_CACHE.get(str(jid))
        if not x:
            x=_pending_disk_get(PENDING_WAV_DIR,jid)
            if x: _WAV_ONLINE_CACHE[str(jid)]=x
        return x

def _wav_cache_put(jid,data,name):
    with _WAV_CACHE_LOCK:
        _pending_disk_put(PENDING_WAV_DIR,jid,data,name)
        _WAV_ONLINE_CACHE[str(jid)]={'data':bytes(data),'name':str(name),'used':time.time()}
        while len(_WAV_ONLINE_CACHE)>2:
            k=min(_WAV_ONLINE_CACHE,key=lambda x:_WAV_ONLINE_CACHE[x].get('used',0)); _WAV_ONLINE_CACHE.pop(k,None)

def _wav_cache_pop(jid):
    with _WAV_CACHE_LOCK:
        value=_WAV_ONLINE_CACHE.pop(str(jid),None)
        _pending_disk_pop(PENDING_WAV_DIR,jid)
        return value

def now(): return datetime.now().strftime('%d/%m/%Y %H:%M')
@contextmanager
def db():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def _read_json_cfg(path):
    try:
        if path.exists():
            data=json.loads(path.read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else {}
    except Exception:
        pass
    return {}

def _atomic_write_json(path,cfg):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

def load_drive_bridge_cfg():
    # Prioridad: configuración global persistente -> espejo de esta versión ->
    # URL estable autorizada. Así cambiar de LOCAL 16.x no obliga a pegar el script.
    cfg=_read_json_cfg(DRIVE_BRIDGE_CFG_GLOBAL)
    if not str(cfg.get('webapp_url') or '').strip():
        local_cfg=_read_json_cfg(DRIVE_BRIDGE_CFG)
        if str(local_cfg.get('webapp_url') or '').strip():
            cfg=local_cfg
            try: _atomic_write_json(DRIVE_BRIDGE_CFG_GLOBAL,cfg)
            except Exception: pass
    cfg.setdefault('api_key',DRIVE_BRIDGE_KEY_DEFAULT)
    if not str(cfg.get('webapp_url') or '').strip():
        cfg['webapp_url']=DRIVE_BRIDGE_URL_DEFAULT
    return cfg

def save_drive_bridge_cfg(cfg):
    # Guardamos en AppData y también un espejo en la carpeta actual. La caída de
    # uno de los dos no debe impedir que ADMIN pueda seguir trabajando.
    cfg=dict(cfg or {})
    cfg['api_key']=str(cfg.get('api_key') or DRIVE_BRIDGE_KEY_DEFAULT)
    cfg['webapp_url']=str(cfg.get('webapp_url') or DRIVE_BRIDGE_URL_DEFAULT).strip()
    disk_cfg=dict(cfg)
    if os.getenv('DJGABO_DRIVE_BRIDGE_KEY'): disk_cfg.pop('api_key',None)
    global_error=None
    try: _atomic_write_json(DRIVE_BRIDGE_CFG_GLOBAL,disk_cfg)
    except Exception as e: global_error=e
    try: _atomic_write_json(DRIVE_BRIDGE_CFG,disk_cfg)
    except Exception:
        if global_error: raise global_error

def drive_bridge_configured():
    cfg=load_drive_bridge_cfg()
    return bool(str(cfg.get('webapp_url') or '').strip())

class DriveBridgeEndpointError(ValueError):
    """Fallo de URL/red/protocolo del Web App; permite probar el endpoint estable."""


def _drive_bridge_request(url, key, action, payload=None, timeout=90):
    try:
        if payload is None:
            r=requests.get(url,params={'api':action,'key':key},timeout=timeout,allow_redirects=True)
        else:
            body=dict(payload); body.update({'api':action,'key':key})
            r=requests.post(url,json=body,timeout=timeout,allow_redirects=True)
    except requests.RequestException as e:
        raise DriveBridgeEndpointError('No pude conectar con Google Drive histórico: '+str(e)) from e

    text=r.text or ''
    if r.status_code>=400:
        raise DriveBridgeEndpointError('Drive histórico HTTP '+str(r.status_code)+': '+text[:500])

    try:
        data=r.json()
    except Exception:
        if '<html' in text.lower() or 'accounts.google.com' in str(r.url).lower():
            raise DriveBridgeEndpointError('El Web App de Drive devolvió una página de Google en vez del puente JSON.')
        raise DriveBridgeEndpointError('Respuesta inválida del puente Drive: '+text[:500])

    # Un error JSON válido ya viene del Web App correcto: no debe cambiar de URL.
    if not data.get('ok',False):
        raise ValueError(str(data.get('error') or 'Error del puente Drive.'))
    return data


def drive_bridge_call(action, payload=None, timeout=90):
    """Puente Drive con autorreparación contundente.

    Prueba la URL persistente y las implementaciones autorizadas conocidas. Un
    404/HTML/red se reintenta y cambia de endpoint automáticamente. Un error JSON
    válido NO cambia de endpoint porque ya proviene del Apps Script correcto.
    """
    cfg=load_drive_bridge_cfg()
    # En producción, la clave inyectada por systemd es autoritativa. El JSON
    # persistente sólo sirve como respaldo para instalaciones locales antiguas.
    key=str(DRIVE_BRIDGE_KEY_DEFAULT or cfg.get('api_key') or '')
    saved=str(cfg.get('webapp_url') or '').strip()
    candidates=[]
    # La URL del entorno de producción es autoritativa. Un espejo persistente
    # puede corresponder a una implementación antigua que responda JSON válido
    # pero aún no conozca las acciones nuevas del Sheet Maestro.
    for u in (DRIVE_BRIDGE_URL_DEFAULT,saved,DRIVE_BRIDGE_URL_LEGACY):
        u=str(u or '').strip()
        if u and u not in candidates: candidates.append(u)
    first_endpoint_error=None
    for url in candidates:
        for attempt in range(2):
            try:
                data=_drive_bridge_request(url,key,action,payload=payload,timeout=timeout)
                if url!=saved:
                    cfg['webapp_url']=url; cfg['api_key']=key
                    try: save_drive_bridge_cfg(cfg)
                    except Exception: pass
                return data
            except DriveBridgeEndpointError as e:
                if first_endpoint_error is None: first_endpoint_error=e
                if attempt==0: time.sleep(.65)
            except ValueError:
                raise
    raise first_endpoint_error or DriveBridgeEndpointError('No hay un Web App Drive disponible.')

def drive_bridge_get_job(jid, open_job=False, actor='Valeria'):
    action='open_job' if open_job else 'job'
    return drive_bridge_call(action,{'id':jid,'actor':actor})

def _sheet_managed(job):
    """Un trabajo gestionado por el Sheet usa SQLite sólo como espejo rápido."""
    job=dict(job)
    return ((job.get('origin') or '')=='HISTORICO_DRIVE' or
            str(job.get('sheet_master_status') or '').upper() in ('RESERVADO','OK','PENDIENTE','ERROR'))

def master_reserve(jid, artist, title, voice_name, lyrics, size_bytes=0, duration=0):
    """Reserva el LET-XXXX en el Sheet antes de confirmar el alta en OVH.

    La LETRA MAESTRA es opcional en el panel. El Web App histórico todavía
    valida el campo lyrics como dato no vacío al reservar una fila; por eso,
    cuando aún no hay letra, enviamos un marcador temporal SOLO al puente.
    El trabajo local conserva lyrics_moises="" y master_sync/IA reemplaza este
    marcador con el valor real cuando corresponda.
    """
    # DJGABO_OPTIONAL_MASTER_LYRICS_V1
    artist=str(artist or '').strip()
    title=str(title or '').strip()
    voice_name=Path(str(voice_name or '')).name.strip()
    if not artist or not title or not voice_name:
        raise ValueError('Faltan Artista, Título o archivo de Voz para registrar el trabajo maestro.')
    reserve_lyrics=str(lyrics or '').strip() or '[PENDIENTE IA]'
    return drive_bridge_call('master_reserve',{
        'id':str(jid),'artist':artist,'title':title,
        'voiceName':voice_name,'lyrics':reserve_lyrics,
        'sizeBytes':int(size_bytes or 0),'duration':float(duration or 0)
    },timeout=120)

def master_sync(jid, observation=''):
    """Actualiza las mismas 26 columnas sin cambiar la estructura del Sheet."""
    with db() as c: job=dict(jobrow(c,str(jid)))
    payload={
        'id':job['id'],'artist':job['artist'],'title':job['title'],
        'voiceName':job.get('voice_original_filename') or job.get('voice_filename') or '',
        'audioDriveId':job.get('voice_drive_id') or job.get('legacy_audio_drive_id') or '',
        'sizeBytes':int(job.get('size_bytes') or 0),'duration':float(job.get('duration') or 0),
        'lyricsMoises':job.get('lyrics_moises') or '',
        'lyricsCorrected':job.get('lyrics_corrected') or '',
        'status':job.get('status') or EST_P,'copied':job.get('copied') or 'NO',
        'observation':str(observation or '')[:500]
    }
    try:
        result=drive_bridge_call('master_sync',payload,timeout=120)
        with db() as c: c.execute("UPDATE jobs SET sheet_master_status='OK',sheet_master_error='',updated=? WHERE id=?",(now(),str(jid)))
        return result
    except Exception as e:
        with db() as c: c.execute("UPDATE jobs SET sheet_master_status='PENDIENTE',sheet_master_error=?,updated=? WHERE id=?",(str(e)[:1000],now(),str(jid)))
        raise

def master_file(jid, file_id, file_type, version=1):
    if not str(file_id or '').strip(): return None
    return drive_bridge_call('master_file',{
        'id':str(jid),'fileId':str(file_id),'fileType':str(file_type),
        'version':int(version or 1)
    },timeout=120)

def master_state(jid, status, detail=''):
    try:
        result=drive_bridge_call('master_state',{
            'id':str(jid),'status':str(status),'detail':str(detail or '')[:500]
        },timeout=90)
        with db() as c: c.execute("UPDATE jobs SET sheet_master_status='OK',sheet_master_error='' WHERE id=?",(str(jid),))
        return result
    except Exception as e:
        with db() as c: c.execute("UPDATE jobs SET sheet_master_status='PENDIENTE',sheet_master_error=? WHERE id=?",(str(e)[:1000],str(jid)))
        raise

def drive_oauth_token(force=False):
    """Token OAuth corto, entregado sólo al backend por el Apps Script privado."""
    if not DRIVE_AUDIO_TOKEN_SECRET:
        raise ValueError('Falta DJGABO_DRIVE_AUDIO_TOKEN_SECRET en el servidor.')
    with _DRIVE_TOKEN_LOCK:
        if not force and _DRIVE_TOKEN_CACHE['token'] and time.time()<_DRIVE_TOKEN_CACHE['expires']-90:
            return _DRIVE_TOKEN_CACHE['token']
        data=drive_bridge_call('audio_token',{'audioSecret':DRIVE_AUDIO_TOKEN_SECRET},timeout=45)
        token=str(data.get('accessToken') or '').strip()
        if not token: raise ValueError('Google Drive no entregó un token OAuth al backend.')
        ttl=max(120,int(data.get('expiresInSec') or 1200))
        _DRIVE_TOKEN_CACHE.update(token=token,expires=time.time()+ttl)
        return token

def _drive_api(method,url,force_token=False,**kwargs):
    """Drive API con renovación única; nunca expone el token al navegador/log."""
    headers=dict(kwargs.pop('headers',{}) or {})
    headers['Authorization']='Bearer '+drive_oauth_token(force=force_token)
    try: response=requests.request(method,url,headers=headers,timeout=kwargs.pop('timeout',120),**kwargs)
    except requests.RequestException as e: raise ValueError('No se pudo conectar con Google Drive: '+str(e)) from e
    if response.status_code==401 and not force_token:
        return _drive_api(method,url,force_token=True,headers={k:v for k,v in headers.items() if k.lower()!='authorization'},**kwargs)
    if response.status_code>=400:
        raise ValueError('Google Drive HTTP '+str(response.status_code)+': '+(response.text or '')[:700])
    return response

def _drive_find_job_file(parent_id,jid,kind):
    esc=lambda value:str(value).replace('\\','\\\\').replace("'","\\'")
    q=("'"+esc(parent_id)+"' in parents and trashed=false and "
       "appProperties has { key='djgabo_job_id' and value='"+esc(jid)+"' } and "
       "appProperties has { key='djgabo_kind' and value='"+esc(kind)+"' }")
    response=_drive_api('GET','https://www.googleapis.com/drive/v3/files',params={
        'q':q,'fields':'files(id,name,size,modifiedTime)','pageSize':10,'spaces':'drive','supportsAllDrives':'true','includeItemsFromAllDrives':'true'
    },timeout=60)
    files=response.json().get('files') or []
    return files[0] if files else None

def _drive_resumable_upsert(parent_id,jid,kind,name,mime,size,source,file_id=''):
    """Crea/actualiza un único archivo por trabajo usando appProperties idempotentes."""
    if not parent_id: raise ValueError('Falta la carpeta destino de Google Drive.')
    existing=None
    if not file_id: existing=_drive_find_job_file(parent_id,jid,kind)
    file_id=str(file_id or ((existing or {}).get('id') or '')).strip()
    metadata={'name':Path(str(name)).name,'appProperties':{'djgabo_job_id':str(jid),'djgabo_kind':str(kind)}}
    if not file_id: metadata['parents']=[parent_id]
    if file_id:
        url='https://www.googleapis.com/upload/drive/v3/files/'+file_id; method='PATCH'
    else:
        url='https://www.googleapis.com/upload/drive/v3/files'; method='POST'
    init=_drive_api(method,url,params={'uploadType':'resumable','supportsAllDrives':'true'},json=metadata,headers={
        'X-Upload-Content-Type':mime,'X-Upload-Content-Length':str(int(size))
    },timeout=90)
    location=str(init.headers.get('Location') or '').strip()
    if not location: raise ValueError('Google Drive no abrió una sesión de subida recuperable.')
    headers={'Authorization':'Bearer '+drive_oauth_token(),'Content-Type':mime,'Content-Length':str(int(size))}
    def upload_once(payload):
        try: return requests.put(location,data=payload,headers=headers,timeout=300)
        except requests.RequestException as e: raise ValueError('Se cortó la subida a Google Drive: '+str(e)) from e
    response=upload_once(source)
    if response.status_code==401:
        headers['Authorization']='Bearer '+drive_oauth_token(force=True)
        if hasattr(source,'seek'): source.seek(0)
        response=upload_once(source)
    if response.status_code>=400:
        raise ValueError('Google Drive no confirmó la subida (HTTP '+str(response.status_code)+'): '+(response.text or '')[:700])
    result=response.json() if response.content else {}
    result_id=str(result.get('id') or file_id).strip()
    if not result_id: raise ValueError('Google Drive confirmó datos, pero no devolvió el ID del archivo.')
    verify=_drive_api('GET','https://www.googleapis.com/drive/v3/files/'+result_id,params={'fields':'id,name,size,parents,trashed'},timeout=60).json()
    if verify.get('trashed') or int(verify.get('size') or -1)!=int(size) or parent_id not in (verify.get('parents') or []):
        raise ValueError('La verificación final de Google Drive no coincide con el archivo enviado.')
    return verify

def drive_upsert_path(parent_id,jid,kind,name,mime,path,file_id=''):
    path=Path(path); size=path.stat().st_size
    with path.open('rb') as source:
        return _drive_resumable_upsert(parent_id,jid,kind,name,mime,size,source,file_id=file_id)

def drive_upsert_bytes(parent_id,jid,kind,name,mime,data,file_id=''):
    raw=bytes(data)
    return _drive_resumable_upsert(parent_id,jid,kind,name,mime,len(raw),BytesIO(raw),file_id=file_id)

def _timings_name(job):
    stem=Path(str(job.get('instrumental_filename') or '')).stem or _provisional_master_stem(job) or str(job.get('id') or 'proyecto')
    return safe_name(stem)+'.timings.json'

def _timings_local_path(jid): return JOBS/str(jid)/'proyecto.timings.json'

def backup_voice_to_drive(jid):
    with db() as c:
        job=dict(jobrow(c,jid)); c.execute("UPDATE jobs SET voice_drive_status='SUBIENDO',voice_drive_error='',updated=? WHERE id=?",(now(),jid))
    if (job.get('origin') or '')=='HISTORICO_DRIVE':
        with db() as c: c.execute("UPDATE jobs SET voice_drive_status='OK',voice_drive_id=CASE WHEN voice_drive_id='' THEN legacy_audio_drive_id ELSE voice_drive_id END,updated=? WHERE id=?",(now(),jid))
        return {'id':job.get('legacy_audio_drive_id') or 'HISTORICO'}
    path=JOBS/str(jid)/str(job.get('voice_filename') or '')
    name=str(job.get('voice_original_filename') or job.get('voice_filename') or path.name)
    try:
        result=drive_upsert_path(DRIVE_ACAPELLAS_FOLDER_ID,jid,'acapella',name,'audio/mpeg',path,file_id=job.get('voice_drive_id') or '')
        with db() as c:
            c.execute("UPDATE jobs SET voice_drive_id=?,voice_drive_status='OK',voice_drive_error='',updated=? WHERE id=?",(result['id'],now(),jid)); log(c,jid,'RESPALDAR ACAPELLA DRIVE','OK')
        master_file(jid,result['id'],'ACAPELLA')
        master_sync(jid,'Acapella respaldado en 01_ACAPELLAS desde OVH')
        return result
    except Exception as e:
        with db() as c: c.execute("UPDATE jobs SET voice_drive_status='PENDIENTE',voice_drive_error=?,updated=? WHERE id=?",(str(e)[:1000],now(),jid))
        raise

def backup_timings_to_drive(jid,data):
    raw=bytes(data)
    with db() as c:
        job=dict(jobrow(c,jid)); c.execute("UPDATE jobs SET timings_drive_status='SUBIENDO',timings_drive_error='',updated=? WHERE id=?",(now(),jid))
    name=_timings_name(job)
    try:
        result=drive_upsert_bytes(DRIVE_TIMINGS_FOLDER_ID,jid,'timings',name,'application/json',raw,file_id=job.get('timings_drive_id') or '')
        with db() as c:
            c.execute("UPDATE jobs SET timings_drive_id=?,timings_drive_name=?,timings_drive_status='OK',timings_drive_error='',updated=? WHERE id=?",(result['id'],name,now(),jid)); log(c,jid,'RESPALDAR TIMINGS DRIVE','OK')
        with db() as c: version=int(jobrow(c,jid)['version'] or 1)
        master_file(jid,result['id'],'TIMINGS_JSON',version)
        master_sync(jid,'JSON de timings respaldado en 06_PROYECTOS_TIMINGS')
        return result
    except Exception as e:
        with db() as c: c.execute("UPDATE jobs SET timings_drive_status='PENDIENTE',timings_drive_error=?,updated=? WHERE id=?",(str(e)[:1000],now(),jid))
        raise

def schedule_timings_backup(jid,data):
    jid=str(jid)
    with _DRIVE_BACKUP_LOCK:
        _DRIVE_TIMINGS_PENDING[jid]=bytes(data)
        if jid in _DRIVE_TIMINGS_RUNNING: return
        _DRIVE_TIMINGS_RUNNING.add(jid)
    def worker():
        try:
            while True:
                with _DRIVE_BACKUP_LOCK: payload=_DRIVE_TIMINGS_PENDING.pop(jid,None)
                if payload is None: break
                try: backup_timings_to_drive(jid,payload)
                except Exception as e: app.logger.warning('timings Drive %s: %s',jid,e)
        finally:
            with _DRIVE_BACKUP_LOCK: _DRIVE_TIMINGS_RUNNING.discard(jid)
    threading.Thread(target=worker,daemon=True,name='drive-timings-'+jid).start()

def schedule_timings_rename(jid):
    with db() as c: job=dict(jobrow(c,jid))
    file_id=str(job.get('timings_drive_id') or '')
    if not file_id: return
    name=_timings_name(job)
    def worker():
        try:
            _drive_api('PATCH','https://www.googleapis.com/drive/v3/files/'+file_id,params={'supportsAllDrives':'true'},json={'name':name},timeout=60)
            with db() as c: c.execute("UPDATE jobs SET timings_drive_name=?,timings_drive_status='OK',timings_drive_error='',updated=? WHERE id=?",(name,now(),jid))
            master_file(jid,file_id,'TIMINGS_JSON',int(job.get('version') or 1))
            master_sync(jid,'Timings renombrados con la identidad maestra del WAV')
        except Exception as e:
            with db() as c: c.execute("UPDATE jobs SET timings_drive_status='PENDIENTE',timings_drive_error=?,updated=? WHERE id=?",(str(e)[:1000],now(),jid))
    threading.Thread(target=worker,daemon=True,name='drive-rename-'+str(jid)).start()

def _voice_cache_paths(jid):
    key=re.sub(r'[^A-Za-z0-9_.-]+','_',str(jid))
    return VOICE_CACHE_DIR/(key+'.audio'),VOICE_CACHE_DIR/(key+'.json')

def _voice_disk_info(jid,allow_stale=False):
    audio,meta=_voice_cache_paths(jid)
    cfg=_read_json_cfg(meta)
    try:
        size=int(cfg.get('size') or 0); cached_at=float(cfg.get('cached_at') or 0)
        if not audio.is_file() or size<=0 or audio.stat().st_size!=size: return None
        if not allow_stale and (time.time()-cached_at)>VOICE_CACHE_METADATA_TTL: return None
        return {'size':size,'mime':str(cfg.get('mime') or 'audio/mpeg'),'name':str(cfg.get('name') or (str(jid)+'.mp3')),'duration':float(cfg.get('duration') or 0),'used':time.time()}
    except Exception:
        return None

def _voice_disk_info_save(jid,info):
    _,meta=_voice_cache_paths(jid)
    _atomic_write_json(meta,{'size':int(info.get('size') or 0),'mime':str(info.get('mime') or 'audio/mpeg'),'name':str(info.get('name') or (str(jid)+'.mp3')),'duration':float(info.get('duration') or 0),'cached_at':time.time()})

def _voice_disk_info_from_db(jid):
    """Migra caches creados por 16.14 antes de que existiera el .json lateral."""
    audio,_=_voice_cache_paths(jid)
    if not audio.is_file() or audio.stat().st_size<=0: return None
    try:
        with db() as c:
            row=c.execute('SELECT voice_filename,duration FROM jobs WHERE id=?',(str(jid),)).fetchone()
        if not row: return None
        name=str(row['voice_filename'] or (str(jid)+'.mp3'))
        info={'size':audio.stat().st_size,'mime':'audio/wav' if Path(name).suffix.lower()=='.wav' else 'audio/mpeg','name':name,'duration':float(row['duration'] or 0),'used':time.time()}
        _voice_disk_info_save(jid,info)
        return info
    except Exception as e:
        app.logger.warning('No se pudo migrar metadata del cache %s: %s',jid,e)
        return None

def drive_audio_info(jid, force=False):
    jid=str(jid)
    with _DRIVE_CACHE_LOCK:
        if not force and jid in _DRIVE_AUDIO_META:
            x=dict(_DRIVE_AUDIO_META[jid]); x['used']=time.time(); _DRIVE_AUDIO_META[jid]=x; return x
    disk_info=_voice_disk_info(jid) or (_voice_disk_info_from_db(jid) if not force else None)
    if not force and disk_info:
        with _DRIVE_CACHE_LOCK: _DRIVE_AUDIO_META[jid]=disk_info
        return dict(disk_info)
    try:
        info=drive_bridge_call('audio_info',{'id':jid},timeout=60)
    except Exception:
        stale=_voice_disk_info(jid,allow_stale=True)
        if stale:
            app.logger.warning('Drive metadata no respondió para %s; se usará la voz cacheada.',jid)
            with _DRIVE_CACHE_LOCK: _DRIVE_AUDIO_META[jid]=stale
            return dict(stale)
        raise
    out={'size':int(info.get('sizeBytes') or 0),'mime':str(info.get('mime') or 'audio/mpeg'),'name':str(info.get('name') or (jid+'.mp3')),'duration':float(info.get('duration') or 0),'used':time.time()}
    if out['size']<=0: raise ValueError('Drive informó un audio vacío para '+jid)
    with _DRIVE_CACHE_LOCK: _DRIVE_AUDIO_META[jid]=out
    return dict(out)

def _voice_disk_path(jid):
    return _voice_cache_paths(jid)[0]

def _voice_disk_load(jid,expected):
    if VOICE_CACHE_MAX_BYTES<=0: return None
    path=_voice_disk_path(jid)
    try:
        if path.is_file() and path.stat().st_size==int(expected):
            os.utime(path,None)
            return path.read_bytes()
    except Exception as e:
        app.logger.warning('No se pudo leer cache de voz %s: %s',jid,e)
    return None

def _voice_disk_prune(protected=None):
    if VOICE_CACHE_MAX_BYTES<=0: return
    try:
        files=[p for p in VOICE_CACHE_DIR.glob('*.audio') if p.is_file()]
        total=sum(p.stat().st_size for p in files)
        for path in sorted(files,key=lambda p:p.stat().st_mtime):
            if total<=VOICE_CACHE_MAX_BYTES: break
            if protected and path==protected: continue
            size=path.stat().st_size
            path.unlink(missing_ok=True)
            try: path.with_suffix('.json').unlink(missing_ok=True)
            except Exception: pass
            total-=size
    except Exception as e:
        app.logger.warning('No se pudo depurar cache de voz: %s',e)

def _voice_disk_save(jid,raw,info):
    if VOICE_CACHE_MAX_BYTES<=0 or len(raw)>VOICE_CACHE_MAX_BYTES: return
    path=_voice_disk_path(jid)
    try:
        tmp=path.with_suffix('.tmp'); tmp.write_bytes(raw); tmp.replace(path); _voice_disk_info_save(jid,info)
        _voice_disk_prune(protected=path)
    except Exception as e:
        app.logger.warning('No se pudo guardar cache de voz %s: %s',jid,e)

def drive_audio_bytes(jid, progress=None):
    """Obtiene el audio una sola vez y lo comparte en RAM.

    La acción `audio` es exactamente el mecanismo que usaba el panel antiguo.
    La diferencia es que ahora el Base64 termina en el backend, no en la laptop
    de Valeria. Waveform, PLAY y seek reutilizan la misma copia temporal.
    """
    jid=str(jid); info=drive_audio_info(jid); expected=int(info.get('size') or 0)
    with _DRIVE_AUDIO_MEMORY_COND:
        cached=_DRIVE_AUDIO_MEMORY.get(jid)
        if cached and int(cached.get('size') or 0)==expected:
            cached['used']=time.time()
            if progress: progress(expected,expected)
            return cached['data'],dict(cached['info'])
        if jid in _DRIVE_AUDIO_LOADING:
            deadline=time.time()+240
            while jid in _DRIVE_AUDIO_LOADING and time.time()<deadline:
                _DRIVE_AUDIO_MEMORY_COND.wait(timeout=min(2,max(.1,deadline-time.time())))
            cached=_DRIVE_AUDIO_MEMORY.get(jid)
            if cached and int(cached.get('size') or 0)==expected:
                cached['used']=time.time()
                if progress: progress(expected,expected)
                return cached['data'],dict(cached['info'])
            err=_DRIVE_AUDIO_ERRORS.get(jid)
            if err: raise ValueError(err)
            raise ValueError('La carga única del audio agotó el tiempo de espera para '+jid+'.')
        _DRIVE_AUDIO_LOADING.add(jid); _DRIVE_AUDIO_ERRORS.pop(jid,None)
    try:
        if progress: progress(0,expected)
        final_info=dict(info)
        raw=_voice_disk_load(jid,expected)
        if raw is None:
            data=drive_bridge_call('audio',{'id':jid},timeout=180)
            encoded=str(data.get('audioBase64') or '')
            try: raw=base64.b64decode(encoded,validate=True) if encoded else b''
            except Exception as e: raise ValueError('Drive devolvió un audio Base64 inválido para '+jid+'.') from e
            if not raw: raise ValueError('Drive devolvió el audio vacío para '+jid+'.')
            if expected and len(raw)!=expected:
                raise ValueError('Drive informó '+str(expected)+' bytes pero entregó '+str(len(raw))+' para '+jid+'.')
            final_info['mime']=str(data.get('mime') or info.get('mime') or 'audio/mpeg')
            final_info['name']=str(data.get('name') or info.get('name') or (jid+'.mp3'))
            final_info['duration']=float(data.get('duration') or info.get('duration') or 0)
            final_info['size']=len(raw)
            _voice_disk_save(jid,raw,final_info)
        final_info['size']=len(raw)
        if raw is not None and not _voice_disk_info(jid,allow_stale=True):
            try: _voice_disk_info_save(jid,final_info)
            except Exception: pass
        with _DRIVE_AUDIO_MEMORY_COND:
            _DRIVE_AUDIO_MEMORY[jid]={'data':raw,'info':final_info,'size':len(raw),'used':time.time()}
            while len(_DRIVE_AUDIO_MEMORY)>DRIVE_AUDIO_MEMORY_MAX:
                old=min((k for k in _DRIVE_AUDIO_MEMORY if k!=jid),key=lambda k:_DRIVE_AUDIO_MEMORY[k].get('used',0),default=None)
                if old is None: break
                _DRIVE_AUDIO_MEMORY.pop(old,None)
        if progress: progress(len(raw),len(raw))
        return raw,final_info
    except Exception as e:
        with _DRIVE_AUDIO_MEMORY_COND: _DRIVE_AUDIO_ERRORS[jid]=str(e)
        raise
    finally:
        with _DRIVE_AUDIO_MEMORY_COND:
            _DRIVE_AUDIO_LOADING.discard(jid); _DRIVE_AUDIO_MEMORY_COND.notify_all()

def drive_audio_iter(jid, start=0, end=None, chunk_size=DRIVE_AUDIO_YIELD_SIZE):
    raw,info=drive_audio_bytes(jid)
    total=len(raw); start=max(0,int(start)); end=total-1 if end is None else min(int(end),total-1)
    piece=max(1,int(chunk_size)); offset=start
    while offset<=end:
        nxt=min(end+1,offset+piece)
        yield raw[offset:nxt]
        offset=nxt

def parse_http_byte_range(value, size):
    """Convierte un Range HTTP simple en límites inclusivos.

    Los navegadores de audio usan un solo intervalo. Los rangos múltiples o
    inválidos se rechazan con 416 para no anunciar bytes que no se entregarán.
    """
    size=int(size or 0)
    if size<=0: return None
    value=str(value or '').strip()
    if not value: return 0,size-1,False
    m=re.fullmatch(r'bytes=(\d*)-(\d*)',value,re.IGNORECASE)
    if not m or (not m.group(1) and not m.group(2)): return None
    first,last=m.groups()
    if not first:
        suffix=int(last)
        if suffix<=0: return None
        return max(0,size-suffix),size-1,True
    start=int(first)
    if start>=size: return None
    end=size-1 if not last else min(int(last),size-1)
    if end<start: return None
    return start,end,True

def drive_audio_to_bytes(jid, progress=None):
    return drive_audio_bytes(jid,progress=progress)

def drive_bridge_get_audio(jid):
    """Compatibilidad: reúne la voz sólo para funciones antiguas.
    El editor V1 nuevo usa streaming /voice + /peaks y no llama esta función.
    """
    raw,info=drive_audio_to_bytes(jid)
    return raw,info['mime'],info['name'],info['duration']

def refresh_historical_from_drive(c, r, open_job=False, actor='Valeria'):
    if (r['origin'] or '')!='HISTORICO_DRIVE' or not drive_bridge_configured(): return r
    try:
        d=drive_bridge_get_job(r['id'],open_job=open_job,actor=actor)
        c.execute('''UPDATE jobs SET artist=?,title=?,status=?,copied=?,updated=?,voice_filename=?,lyrics_moises=?,lyrics_corrected=?,duration=?,size_bytes=?,version=?,legacy_audio_drive_id=? WHERE id=?''',(
          str(d.get('artist') or r['artist']),str(d.get('title') or r['title']),str(d.get('status') or r['status']),str(d.get('copied') or r['copied']),
          str(d.get('updated') or r['updated']),str(d.get('voiceName') or r['voice_filename']),str(d.get('lyricsMoises') or ''),str(d.get('lyricsCorrected') or ''),
          float(d.get('duration') or r['duration'] or 0),int(d.get('sizeBytes') or r['size_bytes'] or 0),int(d.get('version') or r['version'] or 1),str(d.get('audioDriveId') or r['legacy_audio_drive_id']),r['id']))
        return jobrow(c,r['id'])
    except Exception as e:
        print('[Drive histórico] No se pudo refrescar',r['id'],':',e)
        return r

def init_db():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA synchronous=NORMAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, artist TEXT, title TEXT, status TEXT, copied TEXT DEFAULT 'NO',
          created TEXT, updated TEXT, voice_filename TEXT, instrumental_filename TEXT,
          lyrics_moises TEXT, lyrics_corrected TEXT DEFAULT '', dropbox_path TEXT DEFAULT '/KARAOKES_TERMINADOS',
          duration REAL DEFAULT 0, size_bytes INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
          version INTEGER DEFAULT 1, project_json TEXT DEFAULT '',
          instrumental_dropbox_path TEXT DEFAULT '', instrumental_dropbox_id TEXT DEFAULT '',
          cdg_dropbox_path TEXT DEFAULT '', cdg_dropbox_id TEXT DEFAULT '', dropbox_status TEXT DEFAULT '',
          dropbox_folder_id TEXT DEFAULT '', dropbox_display_path TEXT DEFAULT '',
          origin TEXT DEFAULT 'LOCAL', legacy_audio_drive_id TEXT DEFAULT '',
          cdg_local_filename TEXT DEFAULT '', canonical_name TEXT DEFAULT '',
          voice_original_filename TEXT DEFAULT '', voice_drive_id TEXT DEFAULT '',
          voice_drive_status TEXT DEFAULT '', voice_drive_error TEXT DEFAULT '',
          timings_drive_id TEXT DEFAULT '', timings_drive_name TEXT DEFAULT '',
          timings_drive_status TEXT DEFAULT '', timings_drive_error TEXT DEFAULT '',
          sheet_master_status TEXT DEFAULT '', sheet_master_error TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, action TEXT, state TEXT, at TEXT);
        ''')
        cols={r['name'] for r in c.execute('PRAGMA table_info(jobs)').fetchall()}
        extra={
          'instrumental_dropbox_path':"TEXT DEFAULT ''",
          'instrumental_dropbox_id':"TEXT DEFAULT ''",
          'cdg_dropbox_path':"TEXT DEFAULT ''",
          'cdg_dropbox_id':"TEXT DEFAULT ''",
          'dropbox_status':"TEXT DEFAULT ''",
          'dropbox_folder_id':"TEXT DEFAULT ''",
          'dropbox_display_path':"TEXT DEFAULT ''",
          'origin':"TEXT DEFAULT 'LOCAL'",
          'legacy_audio_drive_id':"TEXT DEFAULT ''",
          'cdg_local_filename':"TEXT DEFAULT ''",
          'canonical_name':"TEXT DEFAULT ''",
          'render_status':"TEXT DEFAULT ''",
          'render_progress':"INTEGER DEFAULT 0",
          'render_error':"TEXT DEFAULT ''",
          'voice_original_filename':"TEXT DEFAULT ''",
          'voice_drive_id':"TEXT DEFAULT ''",
          'voice_drive_status':"TEXT DEFAULT ''",
          'voice_drive_error':"TEXT DEFAULT ''",
          'timings_drive_id':"TEXT DEFAULT ''",
          'timings_drive_name':"TEXT DEFAULT ''",
          'timings_drive_status':"TEXT DEFAULT ''",
          'timings_drive_error':"TEXT DEFAULT ''",
          'sheet_master_status':"TEXT DEFAULT ''",
          'sheet_master_error':"TEXT DEFAULT ''",
        }
        for name,decl in extra.items():
            if name not in cols: c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {decl}')
init_db()

def recover_interrupted_renders():
    """Un reinicio no debe dejar un trabajo fingiendo que todavía renderiza."""
    with db() as c:
        c.execute("""UPDATE jobs SET render_status='INTERRUMPIDO',render_progress=0,
                     render_error='El servidor se reinició durante el render. El proyecto está guardado; vuelve a pulsar Exportar.',updated=?
                     WHERE render_status='RENDERIZANDO'""",(now(),))

recover_interrupted_renders()

COMPLETED_HISTORICAL_IDS=set(['LET-0007', 'LET-0008', 'LET-0009', 'LET-0011', 'LET-0012', 'LET-0013', 'LET-0014', 'LET-0016', 'LET-0019', 'LET-0021', 'LET-0022', 'LET-0023', 'LET-0024', 'LET-0040', 'LET-0041', 'LET-0042', 'LET-0043', 'LET-0045', 'LET-0046', 'LET-0047', 'LET-0048', 'LET-0066', 'LET-0067', 'LET-0072', 'LET-0073'])

def clean_completed_historical_jobs():
    '''Quita del panel sólo históricos ya confirmados con CDG en Dropbox.
    Nunca toca trabajos nuevos/LOCAL aunque compartan un título parecido.
    '''
    removed=[]
    with db() as c:
        for jid in sorted(COMPLETED_HISTORICAL_IDS):
            row=c.execute('SELECT id,origin FROM jobs WHERE id=?',(jid,)).fetchone()
            if not row or (row['origin'] or '')!='HISTORICO_DRIVE': continue
            c.execute('DELETE FROM jobs WHERE id=?',(jid,))
            c.execute('DELETE FROM logs WHERE job_id=?',(jid,))
            removed.append(jid)
    for jid in removed:
        try: shutil.rmtree(JOBS/jid,ignore_errors=True)
        except Exception: pass
        try: shutil.rmtree(OUTPUT/jid,ignore_errors=True)
        except Exception: pass
    if removed: print('[LOCAL 16] Históricos ya producidos retirados del panel:',len(removed))


def seed_legacy_pending_jobs():
    """Importa sólo el ÍNDICE de históricos pendientes. No descarga audios ni crea copias de Drive.
    La voz y la letra se leen bajo demanda mediante el Web App del panel antiguo.
    """
    manifest=LEGACY_SEED/'legacy_jobs.json'
    if not manifest.is_file(): return
    try: items=json.loads(manifest.read_text(encoding='utf-8'))
    except Exception as e:
        print('[LOCAL 16.2 ONLINE] Índice histórico inválido:',e); return
    added=0
    with db() as c:
        for it in items:
            jid=str(it.get('id') or '').strip()
            if not jid or jid in COMPLETED_HISTORICAL_IDS or c.execute('SELECT 1 FROM jobs WHERE id=?',(jid,)).fetchone(): continue
            voice_name=Path(str(it.get('legacy_original_filename') or it.get('voice_filename') or (jid+'.mp3'))).name
            lm=str(it.get('lyrics_moises') or ''); lc=str(it.get('lyrics_corrected') or '')
            dst=JOBS/jid; dst.mkdir(parents=True,exist_ok=True)
            meta={'idTrabajo':jid,'artista':it.get('artist',''),'titulo':it.get('title',''),'origen':'HISTORICO_DRIVE','audioDriveId':it.get('legacy_audio_drive_id',''),'archivoOriginalDrive':voice_name,'modo':'ONLINE'}
            (dst/'trabajo.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
            sql=('INSERT INTO jobs(id,artist,title,status,copied,created,updated,voice_filename,instrumental_filename,lyrics_moises,lyrics_corrected,dropbox_path,duration,size_bytes,version,origin,legacy_audio_drive_id,dropbox_status) '
                 'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
            c.execute(sql,(
                jid,str(it.get('artist') or ''),str(it.get('title') or ''),str(it.get('status') or EST_OK),str(it.get('copied') or 'NO'),
                str(it.get('created') or now()),str(it.get('updated') or now()),voice_name,'',lm,lc,'',float(it.get('duration') or 0),
                int(it.get('size_bytes') or 0),int(it.get('version') or 1),'HISTORICO_DRIVE',str(it.get('legacy_audio_drive_id') or ''),'SIN_DESTINO'))
            log(c,jid,'INDEXAR HISTÓRICO ONLINE',str(it.get('status') or EST_OK)); added+=1
    print('[LOCAL 16.2 ONLINE] Históricos pendientes indexados:',added,'· audios permanecen en Google Drive')


def safe_name(s): return re.sub(r'[\\/:*?"<>|]+','_',str(s)).strip().rstrip('.') or 'archivo'
def clean_title(t):
    t=re.sub(r'\s+KARAOKE\b.*$','',t,flags=re.I)
    t=re.sub(r'\s+INSTRUMENTAL\b.*$','',t,flags=re.I)
    t=re.sub(r'\s+PISTA\b.*$','',t,flags=re.I)
    t=re.sub(r'\s*\((?:CORO|COROS|SIN\s+CORO|SIN\s+COROS)\)\s*$','',t,flags=re.I)
    return t.strip()
def master_identity(filename):
    stem=Path(filename or '').stem.strip()
    m=re.match(r'^(.+?)\s+[-–—]\s+(.+)$',stem)
    if not m: raise ValueError('El instrumental debe llamarse: ARTISTA - TÍTULO ...')
    artist=m.group(1).strip(); title=clean_title(m.group(2))
    if not artist or not title: raise ValueError('No pude obtener Artista y Título del instrumental.')
    return artist,title

def dataurl_save(dataurl,path):
    try: payload=dataurl.split(',',1)[1] if ',' in dataurl else dataurl; raw=base64.b64decode(payload)
    except Exception as e: raise ValueError('Audio inválido') from e
    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw); return len(raw)
def next_id(c):
    r=c.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    n=max(HISTORICAL_ID_FLOOR+1, int(r['id'].split('-')[-1])+1 if r else HISTORICAL_ID_FLOOR+1)
    return f'LET-{n:04d}'
def log(c,jid,action,state=''):
    c.execute('INSERT INTO logs(job_id,action,state,at) VALUES(?,?,?,?)',(jid,action,state,now()))
clean_completed_historical_jobs()
seed_legacy_pending_jobs()

def _client_ip():
    return str(request.remote_addr or 'unknown')

def _login_rate_check(ip):
    cutoff=time.time()-LOGIN_RATE_WINDOW
    attempts=[t for t in LOGIN_FAILURES.get(ip,[]) if t>=cutoff]
    LOGIN_FAILURES[ip]=attempts
    if len(attempts)>=LOGIN_RATE_MAX:
        raise PermissionError('Demasiados intentos. Espera 15 minutos antes de volver a ingresar.')

def _login_failed(ip):
    LOGIN_FAILURES.setdefault(ip,[]).append(time.time())

def session(token, role=None):
    token=str(token or '')
    r=SESSIONS.get(token)
    last=float(SESSION_ACTIVITY.get(token) or 0)
    if not r or not last or (time.time()-last)>SESSION_TTL_SECONDS:
        SESSIONS.pop(token,None); SESSION_ACTIVITY.pop(token,None)
        raise PermissionError('Sesión vencida. Vuelve a ingresar.')
    if role and r!=role: raise PermissionError('Acceso no permitido.')
    SESSION_ACTIVITY[token]=time.time()
    return r

def migrate_legacy_dropbox_cfg():
    """Conserva autorización OAuth y carpeta actual al pasar de V10/V11 a V12."""
    if DROPBOX_CFG.exists(): return
    bases=[]
    try: bases.extend([ROOT.parent,ROOT.parent.parent,Path.home()/'Downloads'])
    except Exception: pass
    seen=set(); candidates=[]
    for base in bases:
        try:
            base=Path(base)
            if not base.exists() or str(base) in seen: continue
            seen.add(str(base))
            candidates.extend(base.glob('CONTROL_CDG_DJGABO_LOCAL_INTEGRADO_1[0-4]*/**/data/dropbox_oauth.json'))
        except Exception: pass
    candidates=[x for x in candidates if x.is_file() and x.resolve()!=DROPBOX_CFG.resolve()]
    if candidates:
        src=max(candidates,key=lambda x:x.stat().st_mtime)
        try:
            DROPBOX_CFG.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,DROPBOX_CFG)
            print('[Dropbox] Autorización V10 migrada automáticamente desde:',src)
        except Exception: pass

def load_dropbox_cfg():
    migrate_legacy_dropbox_cfg()
    cfg={}
    if DROPBOX_CFG.exists():
        try: cfg=json.loads(DROPBOX_CFG.read_text(encoding='utf-8'))
        except Exception: cfg={}
    if os.getenv('DROPBOX_APP_KEY'): cfg['app_key']=os.getenv('DROPBOX_APP_KEY')
    if os.getenv('DROPBOX_APP_SECRET'): cfg['app_secret']=os.getenv('DROPBOX_APP_SECRET')
    if os.getenv('DROPBOX_REFRESH_TOKEN'): cfg['refresh_token']=os.getenv('DROPBOX_REFRESH_TOKEN')
    cfg.setdefault('app_key',DROPBOX_APP_KEY_DEFAULT)
    return cfg

def save_dropbox_cfg(cfg):
    disk_cfg=dict(cfg or {})
    for env_name,key in (('DROPBOX_APP_KEY','app_key'),('DROPBOX_APP_SECRET','app_secret'),('DROPBOX_REFRESH_TOKEN','refresh_token')):
        if os.getenv(env_name): disk_cfg.pop(key,None)
    _atomic_write_json(DROPBOX_CFG,disk_cfg)
    if os.name!='nt':
        try: os.chmod(DROPBOX_CFG,0o600)
        except Exception: pass

def dropbox_connected(cfg=None):
    cfg=cfg or load_dropbox_cfg()
    return bool(cfg.get('app_key') and cfg.get('app_secret') and cfg.get('refresh_token'))

def dropbox_ensure_namespace_context(force=False):
    """Guarda el namespace HOME real de la cuenta. Es imprescindible para carpetas compartidas/montadas."""
    cfg=load_dropbox_cfg()
    if not dropbox_connected(cfg): return cfg
    if (not force) and cfg.get('home_namespace_id'):
        return cfg
    tok=dropbox_access_token()
    last=None
    for attempt in range(3):
        try:
            r=requests.post('https://api.dropboxapi.com/2/users/get_current_account',
              headers={'Authorization':'Bearer '+tok,'Content-Type':'application/json'},data=b'null',timeout=30)
            if r.ok:
                acc=r.json(); ri=acc.get('root_info') or {}
                cfg['account_name']=(acc.get('name') or {}).get('display_name','')
                cfg['account_email']=acc.get('email',''); cfg['account_id']=acc.get('account_id','')
                cfg['root_namespace_id']=str(ri.get('root_namespace_id') or '')
                cfg['home_namespace_id']=str(ri.get('home_namespace_id') or ri.get('root_namespace_id') or '')
                cfg['home_path']=str(ri.get('home_path') or '')
                save_dropbox_cfg(cfg); return cfg
            last=ValueError('Dropbox no devolvió el contexto de carpetas: '+r.text[:500])
            if r.status_code not in (408,429,500,502,503,504): break
        except requests.RequestException as e:
            last=e
        if attempt<2: _retry_sleep(attempt)
    raise ValueError('No se pudo obtener el namespace de Dropbox: '+str(last))

def dropbox_home_namespace_id():
    cfg=load_dropbox_cfg()
    ns=str(cfg.get('home_namespace_id') or '').strip()
    if not ns and dropbox_connected(cfg):
        cfg=dropbox_ensure_namespace_context(); ns=str(cfg.get('home_namespace_id') or '').strip()
    return ns

def dropbox_path_root_value(namespace_id=None):
    # Dropbox-API-Path-Root es un HEADER: JSON ASCII evita corromper rutas con PERÚ/acentos.
    ns=str(namespace_id or dropbox_home_namespace_id() or '').strip()
    return json.dumps({'.tag':'namespace_id','namespace_id':ns},separators=(',',':'),ensure_ascii=True) if ns else ''

def dropbox_headers(tok, content_type='application/json; charset=utf-8', namespace_id=None):
    h={'Authorization':'Bearer '+tok}
    if content_type: h['Content-Type']=content_type
    pr=dropbox_path_root_value(namespace_id)
    if pr: h['Dropbox-API-Path-Root']=pr
    return h

def _retry_sleep(attempt):
    # 1.25 s, 3 s. Tres intentos totales sin hacer esperar demasiado a Valeria.
    time.sleep((1.25,3.0)[min(attempt,1)])

def dropbox_access_token(force=False):
    cfg=load_dropbox_cfg()
    if not dropbox_connected(cfg): raise ValueError('Dropbox no está conectado. En ADMIN pulsa Configurar Dropbox.')
    if not force and cfg.get('access_token') and float(cfg.get('expires_at') or 0)>time.time()+60:
        return cfg['access_token']
    last=None
    for attempt in range(3):
        try:
            r=requests.post('https://api.dropboxapi.com/oauth2/token',
              data={'grant_type':'refresh_token','refresh_token':cfg['refresh_token']},
              auth=(cfg['app_key'],cfg['app_secret']),timeout=30)
            if r.ok:
                j=r.json(); cfg['access_token']=j['access_token']; cfg['expires_at']=time.time()+float(j.get('expires_in') or 14400)
                save_dropbox_cfg(cfg); return cfg['access_token']
            last=ValueError('Dropbox no pudo renovar la sesión: '+r.text[:500])
            if r.status_code not in (408,429,500,502,503,504): break
        except requests.RequestException as e:
            last=e
        if attempt<2: _retry_sleep(attempt)
    raise ValueError('Dropbox no pudo renovar la sesión tras 3 intentos: '+str(last))

def dropbox_rpc(endpoint,payload=None,token=None,path_root=True,namespace_id=None):
    body=json.dumps(payload or {},ensure_ascii=False).encode('utf-8')
    last=None; forced=False
    for attempt in range(3):
        try:
            tok=token or dropbox_access_token(force=forced)
            headers={'Authorization':'Bearer '+tok,'Content-Type':'application/json; charset=utf-8'}
            if path_root and endpoint.startswith('files/'):
                pr=dropbox_path_root_value(namespace_id)
                if pr: headers['Dropbox-API-Path-Root']=pr
            r=requests.post('https://api.dropboxapi.com/2/'+endpoint,headers=headers,data=body,timeout=45)
            if r.status_code==401 and token is None and not forced:
                forced=True
                continue
            if r.ok: return r.json() if r.content else {}
            last=ValueError('Dropbox API: '+r.text[:900])
            if r.status_code not in (408,429,500,502,503,504): break
        except requests.RequestException as e:
            last=e
        if attempt<2: _retry_sleep(attempt)
    raise ValueError(str(last or 'Dropbox API no respondió.'))

def dropbox_join(folder,name):
    folder=str(folder or '').strip().replace('\\','/')
    if folder in ('','/'): return '/'+name
    if not folder.startswith('/'): folder='/'+folder
    return folder.rstrip('/')+'/'+name

def _norm_dropbox_path(v):
    import unicodedata
    v=unicodedata.normalize('NFC',str(v or '').replace('\\','/')).rstrip('/')
    return v.casefold() or '/'

def dropbox_folder_meta(folder_ref):
    """Resuelve una carpeta por ID estable y conserva DOS contextos:
    - path_lower/path_display: ruta montada visible desde HOME (UI / auditoría)
    - api_path_lower + namespace_id: ruta REAL dentro del namespace compartido para escribir

    Esto evita que una carpeta dentro de un shared folder pierda el último nivel
    (ej. 07_Julio 2026) o termine en una rama duplicada/corrupta al subir.
    """
    dropbox_ensure_namespace_context()
    ref=str(folder_ref or '').strip()
    if not ref or ref=='/':
        home=dropbox_home_namespace_id()
        return {'id':'','path_lower':'','path_display':'/','api_path_lower':'','namespace_id':home,'name':'Dropbox'}

    # 1) Resolver SIEMPRE desde HOME para obtener la ruta visible/montada correcta.
    meta_home=dropbox_rpc('files/get_metadata',{'path':ref,'include_media_info':False,'include_deleted':False},namespace_id=dropbox_home_namespace_id())
    if meta_home.get('.tag')!='folder': raise ValueError('El destino Dropbox seleccionado ya no es una carpeta.')
    folder_id=str(meta_home.get('id') or '').strip()
    home_lower=str(meta_home.get('path_lower') or '').strip()
    home_display=str(meta_home.get('path_display') or home_lower or '').strip()
    if not folder_id: raise ValueError('Dropbox no devolvió el ID estable de la carpeta seleccionada.')
    if not home_lower:
        raise ValueError('La carpeta seleccionada no está montada en esta cuenta de Dropbox. Vuelve a elegirla desde el panel.')
    if '\ufffd' in home_display or '\ufffd' in home_lower:
        raise ValueError('Esa carpeta contiene el carácter inválido � y corresponde a una copia dañada. Elige la carpeta original que dice PERÚ.')

    # 2) Si está dentro de una carpeta compartida, escribir dentro de ESE namespace.
    #    Dropbox expone el namespace como parent_shared_folder_id/shared_folder_id.
    sharing=meta_home.get('sharing_info') or {}
    shared_ns=str(sharing.get('parent_shared_folder_id') or sharing.get('shared_folder_id') or '').strip()
    namespace_id=shared_ns if shared_ns.isdigit() else dropbox_home_namespace_id()
    api_lower=home_lower
    if namespace_id and namespace_id!=dropbox_home_namespace_id():
        try:
            meta_ns=dropbox_rpc('files/get_metadata',{'path':folder_id,'include_media_info':False,'include_deleted':False},namespace_id=namespace_id)
            if meta_ns.get('.tag')=='folder' and meta_ns.get('path_lower'):
                api_lower=str(meta_ns.get('path_lower') or '').strip()
        except Exception:
            # Nunca inventamos rutas. Si Dropbox no deja re-resolver por namespace,
            # conservamos la ruta HOME y la verificación post-upload impedirá un destino silencioso incorrecto.
            namespace_id=dropbox_home_namespace_id()
            api_lower=home_lower

    return {
        'id':folder_id,
        'path_lower':home_lower,
        'path_display':home_display or home_lower,
        'api_path_lower':api_lower,
        'namespace_id':namespace_id,
        'name':meta_home.get('name','')
    }

def validate_dropbox_folder(folder_ref):
    return dropbox_folder_meta(folder_ref)

def dropbox_get_metadata_optional(path, namespace_id=None):
    try:
        return dropbox_rpc('files/get_metadata',{'path':path,'include_media_info':False,'include_deleted':False},namespace_id=namespace_id)
    except ValueError as e:
        if 'not_found' in str(e).lower(): return None
        raise

def dropbox_temporary_upload_link(folder_ref, filename, expected_display_path='', duration=3600):
    """Crea un enlace de un solo destino para subir PC -> Dropbox.

    El navegador nunca recibe el token OAuth. Dropbox fija ruta y modo al crear
    el enlace; el archivo no atraviesa OVH y el enlace caduca rápidamente.
    """
    filename=Path(str(filename or '')).name
    if not filename or Path(filename).suffix.lower()!='.wav':
        raise ValueError('El instrumental directo debe ser un archivo WAV.')
    folder=dropbox_folder_meta(folder_ref)
    if expected_display_path and _norm_dropbox_path(expected_display_path)!=_norm_dropbox_path(folder['path_display']):
        raise ValueError('El destino Dropbox cambió antes de preparar la subida. Vuelve a elegir la carpeta exacta.')
    ns=folder.get('namespace_id') or dropbox_home_namespace_id()
    remote_api_path=dropbox_join(folder.get('api_path_lower') or folder['path_lower'],filename)
    payload={
        'commit_info':{'path':remote_api_path,'mode':'overwrite','autorename':False,'mute':False,'strict_conflict':True},
        'duration':max(60,min(14400,int(duration or 3600)))
    }
    result=dropbox_rpc('files/get_temporary_upload_link',payload,namespace_id=ns)
    link=str(result.get('link') or '').strip()
    if not link.startswith('https://content.dropboxapi.com/'):
        raise ValueError('Dropbox no devolvió un enlace temporal de subida válido.')
    return {
        'upload_url':link,
        'filename':filename,
        'folder_id':folder['id'],
        'folder_display':folder['path_display'],
        'folder_path_lower':folder['path_lower'],
        'namespace_id':ns,
        'remote_api_path':remote_api_path,
        'expires_in':payload['duration']
    }

def dropbox_confirm_uploaded_file(folder_ref,filename,expected_size=0,expected_display_path=''):
    """Confirma por ID, tamaño y ruta visible un archivo subido directamente."""
    filename=Path(str(filename or '')).name
    folder=dropbox_folder_meta(folder_ref)
    if expected_display_path and _norm_dropbox_path(expected_display_path)!=_norm_dropbox_path(folder['path_display']):
        raise ValueError('El destino Dropbox cambió durante la subida; no se vinculó otra carpeta.')
    ns=folder.get('namespace_id') or dropbox_home_namespace_id()
    remote=dropbox_join(folder.get('api_path_lower') or folder['path_lower'],filename)
    meta=None
    for attempt in range(6):
        meta=dropbox_get_metadata_optional(remote,namespace_id=ns)
        if meta: break
        time.sleep(.5+attempt*.25)
    if not meta or meta.get('.tag')!='file': raise ValueError('Dropbox todavía no confirma el archivo en la carpeta destino.')
    if expected_size and int(meta.get('size') or -1)!=int(expected_size):
        raise ValueError('Dropbox recibió un tamaño distinto al archivo seleccionado.')
    file_id=str(meta.get('id') or '').strip()
    if not file_id: raise ValueError('Dropbox no devolvió el ID del archivo.')
    home=dropbox_rpc('files/get_metadata',{'path':file_id,'include_media_info':False,'include_deleted':False},namespace_id=dropbox_home_namespace_id())
    actual=str(home.get('path_display') or '').strip(); expected=dropbox_join(folder['path_display'],filename)
    if _norm_dropbox_path(actual)!=_norm_dropbox_path(expected):
        raise ValueError('Dropbox guardó el archivo en una ruta diferente. Esperado: '+expected+' · Real: '+actual)
    home['_folder_id']=folder['id']; home['_folder_display']=folder['path_display']; home['_folder_path_lower']=folder['path_lower']; home['_folder_namespace_id']=ns
    return home

def _dropbox_upload_once(local_path,remote_path,mode='add',namespace_id=None):
    local_path=Path(local_path); tok=dropbox_access_token(); size=local_path.stat().st_size
    commit={'path':remote_path,'mode':mode,'autorename':False,'mute':False,'strict_conflict':True}
    headers=dropbox_headers(tok,'application/octet-stream',namespace_id=namespace_id)
    if size <= 140*1024*1024:
        h=dict(headers); h['Dropbox-API-Arg']=json.dumps(commit,ensure_ascii=True,separators=(',',':'))
        with local_path.open('rb') as f:
            r=requests.post('https://content.dropboxapi.com/2/files/upload',headers=h,data=f,timeout=300)
        if not r.ok: raise ValueError('HTTP '+str(r.status_code)+' '+r.text[:900])
        return r.json()
    chunk=8*1024*1024
    with local_path.open('rb') as f:
        first=f.read(chunk)
        h=dict(headers); h['Dropbox-API-Arg']=json.dumps({'close':False})
        r=requests.post('https://content.dropboxapi.com/2/files/upload_session/start',headers=h,data=first,timeout=120)
        if not r.ok: raise ValueError('HTTP '+str(r.status_code)+' '+r.text[:900])
        sid=r.json()['session_id']; offset=len(first)
        while offset < size:
            data=f.read(chunk); final=(offset+len(data))>=size
            if final:
                arg={'cursor':{'session_id':sid,'offset':offset},'commit':commit}
                h=dict(headers); h['Dropbox-API-Arg']=json.dumps(arg,ensure_ascii=True,separators=(',',':'))
                r=requests.post('https://content.dropboxapi.com/2/files/upload_session/finish',headers=h,data=data,timeout=180)
                if not r.ok: raise ValueError('HTTP '+str(r.status_code)+' '+r.text[:900])
                return r.json()
            arg={'cursor':{'session_id':sid,'offset':offset},'close':False}
            h=dict(headers); h['Dropbox-API-Arg']=json.dumps(arg)
            r=requests.post('https://content.dropboxapi.com/2/files/upload_session/append_v2',headers=h,data=data,timeout=180)
            if not r.ok: raise ValueError('HTTP '+str(r.status_code)+' '+r.text[:900])
            offset+=len(data)
    raise ValueError('Dropbox no confirmó el final de la subida.')

def _dropbox_upload_bytes(data,remote_path,mode='overwrite',namespace_id=None):
    data=bytes(data); tok=dropbox_access_token(); commit={'path':remote_path,'mode':mode,'autorename':False,'mute':False,'strict_conflict':True}
    headers=dropbox_headers(tok,'application/octet-stream',namespace_id=namespace_id)
    if len(data)<=140*1024*1024:
        h=dict(headers); h['Dropbox-API-Arg']=json.dumps(commit,ensure_ascii=True,separators=(',',':'))
        r=requests.post('https://content.dropboxapi.com/2/files/upload',headers=h,data=data,timeout=300)
        if not r.ok: raise ValueError('HTTP '+str(r.status_code)+' '+r.text[:900])
        return r.json()
    raise ValueError('Archivo online demasiado grande para subida directa en memoria.')

def dropbox_upload_bytes(data,folder_ref,filename,mode='overwrite',expected_display_path=''):
    folder=dropbox_folder_meta(folder_ref)
    if expected_display_path and _norm_dropbox_path(expected_display_path)!=_norm_dropbox_path(folder['path_display']):
        raise ValueError('Destino Dropbox cambió antes de subir. Vuelve a elegir la carpeta exacta.')
    ns=folder.get('namespace_id') or dropbox_home_namespace_id(); remote=dropbox_join(folder.get('api_path_lower') or folder['path_lower'],Path(filename).name)
    up=_dropbox_upload_bytes(data,remote,mode=mode,namespace_id=ns)
    uploaded_id=str(up.get('id') or '').strip()
    if not uploaded_id: raise ValueError('Dropbox no devolvió ID para verificar la subida.')
    check=dropbox_rpc('files/get_metadata',{'path':uploaded_id,'include_media_info':False,'include_deleted':False},namespace_id=dropbox_home_namespace_id())
    actual=str(check.get('path_display') or '').strip(); expected=dropbox_join(folder['path_display'],Path(filename).name)
    if _norm_dropbox_path(actual)!=_norm_dropbox_path(expected):
        raise ValueError('Dropbox guardó el archivo en una ruta distinta. Esperado: '+expected+' · Real: '+actual)
    return check

def dropbox_upload_filestorage_online(fs,folder_ref,filename,expected_display_path=''):
    """Sube el WAV directamente del request a Dropbox por sesiones de 8 MiB.
    No crea JOBS/<id>/<wav> ni una copia permanente local.
    """
    folder=dropbox_folder_meta(folder_ref)
    if expected_display_path and _norm_dropbox_path(expected_display_path)!=_norm_dropbox_path(folder['path_display']):
        raise ValueError('Destino Dropbox cambió antes de subir.')
    ns=folder.get('namespace_id') or dropbox_home_namespace_id(); remote=dropbox_join(folder.get('api_path_lower') or folder['path_lower'],Path(filename).name)
    tok=dropbox_access_token(); headers=dropbox_headers(tok,'application/octet-stream',namespace_id=ns); chunk=8*1024*1024
    stream=fs.stream; stream.seek(0); first=stream.read(chunk)
    if not first: raise ValueError('El WAV llegó vacío.')
    h=dict(headers); h['Dropbox-API-Arg']=json.dumps({'close':False})
    r=requests.post('https://content.dropboxapi.com/2/files/upload_session/start',headers=h,data=first,timeout=180)
    if not r.ok: raise ValueError('Dropbox start: '+r.text[:700])
    sid=r.json()['session_id']; offset=len(first)
    while True:
        data=stream.read(chunk)
        if not data:
            arg={'cursor':{'session_id':sid,'offset':offset},'commit':{'path':remote,'mode':'overwrite','autorename':False,'mute':False,'strict_conflict':True}}
            h=dict(headers); h['Dropbox-API-Arg']=json.dumps(arg,ensure_ascii=True,separators=(',',':'))
            r=requests.post('https://content.dropboxapi.com/2/files/upload_session/finish',headers=h,data=b'',timeout=180)
            if not r.ok: raise ValueError('Dropbox finish: '+r.text[:700])
            up=r.json(); break
        arg={'cursor':{'session_id':sid,'offset':offset},'close':False}
        h=dict(headers); h['Dropbox-API-Arg']=json.dumps(arg)
        r=requests.post('https://content.dropboxapi.com/2/files/upload_session/append_v2',headers=h,data=data,timeout=180)
        if not r.ok: raise ValueError('Dropbox append: '+r.text[:700])
        offset+=len(data)
    uploaded_id=str(up.get('id') or '').strip(); check=dropbox_rpc('files/get_metadata',{'path':uploaded_id,'include_media_info':False,'include_deleted':False},namespace_id=dropbox_home_namespace_id())
    return check

def _retryable_dropbox_error(e):
    if isinstance(e,(requests.exceptions.SSLError,requests.exceptions.ConnectionError,requests.exceptions.Timeout,requests.exceptions.ChunkedEncodingError)):
        return True
    txt=str(e).lower()
    return any(x in txt for x in ('internal_error','too_many_write_operations','rate_limit','http 429','http 500','http 502','http 503','http 504','ssl','connection aborted','connection reset','remotedisconnected'))

def dropbox_upload_file(local_path,folder_ref,filename,mode='add',max_attempts=3,expected_display_path=''):
    """Sube al folder ID exacto. Para shared folders usa su namespace real.

    expected_display_path es una foto de la carpeta que el usuario vio al pulsar
    "Usar esta carpeta". Si el ID y la ruta ya no coinciden, se aborta ANTES de subir.
    """
    local_path=Path(local_path)
    if not local_path.is_file(): raise ValueError('Archivo local no encontrado para Dropbox: '+str(local_path))
    filename=Path(str(filename or local_path.name)).name
    folder=dropbox_folder_meta(folder_ref)
    if expected_display_path and _norm_dropbox_path(expected_display_path)!=_norm_dropbox_path(folder['path_display']):
        raise ValueError('Destino Dropbox cambió antes de subir. Esperado: '+str(expected_display_path)+' · Dropbox resolvió: '+folder['path_display']+'. Vuelve a elegir la carpeta exacta.')

    remote_api_path=dropbox_join(folder.get('api_path_lower') or folder['path_lower'],filename)
    ns=folder.get('namespace_id') or dropbox_home_namespace_id()
    if mode=='add':
        existing=dropbox_get_metadata_optional(remote_api_path,namespace_id=ns)
        if existing:
            raise ValueError('Ya existe un archivo con ese nombre en la carpeta Dropbox exacta seleccionada. No se sobrescribió ni se creó una copia.')
    last=None
    for attempt in range(max_attempts):
        try:
            folder=dropbox_folder_meta(folder_ref)
            if expected_display_path and _norm_dropbox_path(expected_display_path)!=_norm_dropbox_path(folder['path_display']):
                raise ValueError('Destino Dropbox cambió durante la subida. No se continuará en otra carpeta.')
            ns=folder.get('namespace_id') or dropbox_home_namespace_id()
            remote_api_path=dropbox_join(folder.get('api_path_lower') or folder['path_lower'],filename)
            replaced_existing=False
            if mode=='overwrite':
                replaced_existing=bool(dropbox_get_metadata_optional(remote_api_path,namespace_id=ns))
            up=_dropbox_upload_once(local_path,remote_api_path,mode=mode,namespace_id=ns)

            # Verificación FINAL por file ID desde HOME: el padre real debe ser exactamente
            # la carpeta seleccionada. Si no, jamás mostramos "subido correctamente".
            uploaded_id=str(up.get('id') or '').strip()
            if not uploaded_id:
                raise ValueError('Dropbox subió el archivo pero no devolvió su ID para verificar el destino.')
            check=dropbox_rpc('files/get_metadata',{'path':uploaded_id,'include_media_info':False,'include_deleted':False},namespace_id=dropbox_home_namespace_id())
            actual_display=str(check.get('path_display') or '').strip()
            expected_file_display=dropbox_join(folder['path_display'],filename)
            if _norm_dropbox_path(actual_display)!=_norm_dropbox_path(expected_file_display):
                raise ValueError('Dropbox guardó el archivo en una ruta distinta. Esperado: '+expected_file_display+' · Real: '+actual_display+'. Se bloqueó el flujo para no perder el destino mensual.')

            # Usamos la metadata HOME verificada como identidad pública del archivo.
            check['_folder_id']=folder['id']
            check['_folder_display']=folder['path_display']
            check['_folder_path_lower']=folder['path_lower']
            check['_folder_namespace_id']=ns
            check['_remote_api_path']=remote_api_path
            check['_attempts']=attempt+1
            check['_replaced_existing']=replaced_existing
            return check
        except Exception as e:
            last=e; txt=str(e).lower()
            if mode=='add' and 'conflict' in txt and attempt>0:
                try:
                    existing=dropbox_get_metadata_optional(remote_api_path,namespace_id=ns)
                    if existing and int(existing.get('size') or -1)==local_path.stat().st_size:
                        existing['_folder_id']=folder['id']; existing['_folder_display']=folder['path_display']; existing['_folder_path_lower']=folder['path_lower']; existing['_folder_namespace_id']=ns; existing['_remote_api_path']=remote_api_path; existing['_attempts']=attempt+1; existing['_replaced_existing']=False
                        return existing
                except Exception:
                    pass
            if 'http 409' in txt or 'path/conflict' in txt: break
            if not _retryable_dropbox_error(e) or attempt>=max_attempts-1: break
            _retry_sleep(attempt)
    raise ValueError('No se pudo subir a Dropbox tras '+str(max_attempts)+' intentos: '+str(last))

def dropbox_delete_best_effort(remote_path,namespace_id=None):
    try: dropbox_rpc('files/delete_v2',{'path':remote_path},namespace_id=namespace_id)
    except Exception: pass

def dropbox_default_folder(validate=False):
    cfg=load_dropbox_cfg(); fid=str(cfg.get('default_folder_id') or '').strip()
    if not fid: return None
    if validate and dropbox_connected(cfg):
        try:
            meta=dropbox_folder_meta(fid)
            cfg['default_folder_id']=meta['id']; cfg['default_folder_display']=meta['path_display']; cfg['default_folder_path_lower']=meta['path_lower']; save_dropbox_cfg(cfg)
            return meta
        except Exception:
            for k in ('default_folder_id','default_folder_display','default_folder_path_lower'): cfg.pop(k,None)
            save_dropbox_cfg(cfg); return None
    return {'id':fid,'path_display':cfg.get('default_folder_display',''),'path_lower':cfg.get('default_folder_path_lower',''),'name':Path(cfg.get('default_folder_display') or '').name}

def jobrow(c,jid):
    r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not r: raise ValueError('Trabajo no encontrado.')
    return r

def _job_local_cdg(r):
    # Compatibilidad de lectura con trabajos viejos. Nuevos CDG no se persisten localmente.
    name=str(r['cdg_local_filename'] or '') if 'cdg_local_filename' in r.keys() else ''
    p=OUTPUT/r['id']/name if name else None
    return bool((p and p.is_file()) or r['cdg_dropbox_id'] or r['cdg_dropbox_path'] or _cdg_cache_get(r['id'])[0])

def _job_voice_ready(r):
    return bool(r['legacy_audio_drive_id']) if (r['origin'] or '')=='HISTORICO_DRIVE' else bool(r['voice_filename'] and (JOBS/r['id']/r['voice_filename']).is_file())

def _job_lyrics_ready(r):
    return bool((r['lyrics_corrected'] or r['lyrics_moises'] or '').strip())

def _job_remote_complete(r):
    """Terminado significa PUBLICADO de verdad: destino + WAV/CDG confirmados en Dropbox."""
    return bool(r['dropbox_folder_id'] and r['instrumental_dropbox_id'] and r['cdg_dropbox_id'] and _job_voice_ready(r) and _job_lyrics_ready(r))

def _sync_terminal_status(c,jid):
    """Alinea ESTADO con la realidad publicada sin borrar letra/proyecto."""
    r=jobrow(c,jid)
    should_term=_job_remote_complete(r)
    if should_term and r['status']!=EST_TERM:
        c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(EST_TERM,now(),jid)); log(c,jid,'AUTO TERMINADO DROPBOX',EST_TERM)
        r=jobrow(c,jid)
    elif (not should_term) and r['status']==EST_TERM:
        fallback=EST_OK if _job_lyrics_ready(r) else EST_C
        c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(fallback,now(),jid)); log(c,jid,'REABRIR POR DROPBOX INCOMPLETO',fallback)
        r=jobrow(c,jid)
    return r

def pubjob(r):
    voice_ready=_job_voice_ready(r)
    dest_ready=bool(r['dropbox_folder_id'])
    # Con Destino, WAV/CDG publicados se leen exclusivamente de Dropbox.
    # Sin Destino, se permite mostrar artefactos preparados en backend/cache.
    if dest_ready:
        inst_ready=bool(r['instrumental_dropbox_id'])
        cdg_ready=bool(r['cdg_dropbox_id'])
    else:
        inst_ready=bool(r['instrumental_filename'] and (_wav_cache_get(r['id']) or (JOBS/r['id']/r['instrumental_filename']).is_file()))
        cdg_ready=_job_local_cdg(r)
    lyrics_ready=_job_lyrics_ready(r)
    return dict(idTrabajo=r['id'],artista=r['artist'],titulo=r['title'],estado=r['status'],copiada=r['copied'],
      actualizado=r['updated'],fecha=r['created'],tamanoBytes=r['size_bytes'],duracionGuardada=r['duration'],version=r['version'],
      origen=r['origin'] or 'LOCAL',vozLista=voice_ready,letraLista=lyrics_ready,instrumentalLista=inst_ready,cdgLista=cdg_ready,destinoLista=dest_ready,
      instrumentalNombre=r['instrumental_filename'] or '',cdgNombre=r['cdg_local_filename'] or '',destinoDropbox=r['dropbox_display_path'] or '',dropboxEstado=r['dropbox_status'] or '',
      legacyAudioDriveId=r['legacy_audio_drive_id'] or '',renderEstado=r['render_status'] or '',renderProgreso=int(r['render_progress'] or 0),renderError=r['render_error'] or '',exportado=bool(cdg_ready),
      vozDriveEstado=('OK' if (r['origin'] or '')=='HISTORICO_DRIVE' else (r['voice_drive_status'] or 'PENDIENTE')),
      vozDriveError=r['voice_drive_error'] or '',timingsDriveEstado=r['timings_drive_status'] or '',timingsDriveNombre=r['timings_drive_name'] or '',timingsDriveError=r['timings_drive_error'] or '',
      sheetMaestroEstado=('OK' if (r['origin'] or '')=='HISTORICO_DRIVE' else (r['sheet_master_status'] or 'PENDIENTE')),
      sheetMaestroError=r['sheet_master_error'] or '')

@app.after_request
def security_headers(response):
    response.headers.setdefault('X-Content-Type-Options','nosniff')
    # El editor V1 se integra en un iframe del propio panel. SAMEORIGIN impide
    # que otros sitios lo incrusten, pero permite esa navegación interna.
    response.headers.setdefault('X-Frame-Options','SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy','same-origin')
    response.headers.setdefault('Permissions-Policy','camera=(), microphone=(), geolocation=()')
    # El WAV viaja directamente del navegador al enlace temporal y de ruta fija
    # que entrega Dropbox. Sin este host en connect-src, el navegador bloquea la
    # petición antes de que salga de la PC y XMLHttpRequest sólo informa un corte.
    response.headers.setdefault('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self' https://content.dropboxapi.com https://uvronline.app https://*.uvronline.app; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; base-uri 'self'; frame-ancestors 'self'")
    if request.path.startswith('/api/') or request.path.startswith('/dropbox/'):
        response.headers.setdefault('Cache-Control','private, no-store')
    return response

@app.errorhandler(PermissionError)
def expired_session_error(error):
    """Las rutas directas también deben responder 401, no registrar un 500."""
    message=str(error) or 'Sesión vencida. Vuelve a ingresar.'
    if request.path.startswith('/api/'):
        return jsonify(ok=False,error=message),401
    return '<h3>Sesión vencida</h3><p>Vuelve al panel e ingresa nuevamente.</p>',401

@app.get('/healthz')
def healthz():
    checks={'database':False,'ffmpeg':bool(shutil.which('ffmpeg')),'data_writable':os.access(DATA,os.W_OK)}
    try:
        with db() as c: checks['database']=c.execute('SELECT 1').fetchone()[0]==1
    except Exception as e:
        checks['database_error']=type(e).__name__
    try:
        usage=shutil.disk_usage(DATA); checks['disk_free_mb']=round(usage.free/(1024*1024))
        checks['disk_ok']=usage.free>=int(os.getenv('DJGABO_MIN_FREE_BYTES') or 1073741824)
    except Exception:
        checks['disk_ok']=False
    ok=all(bool(checks.get(k)) for k in ('database','ffmpeg','data_writable','disk_ok'))
    return jsonify(ok=ok,service='djgabo-cdg',version='16.14-server',environment=ENVIRONMENT,checks=checks),200 if ok else 503

@app.get('/api/public-config')
def public_config():
    return jsonify(ok=True,environment=ENVIRONMENT,correctora_password_required=bool(CORRECTORA_PASSWORD))

def _portal_auth(required_role=None):
    try:
        role=session(request.cookies.get(PORTAL_COOKIE_NAME,''),required_role)
        return jsonify(ok=True,rol=role),200
    except PermissionError:
        return jsonify(ok=False,error='Acceso no autorizado.'),401

@app.get('/api/portal/session')
def portal_session_api():
    return _portal_auth()

@app.get('/api/portal/auth/any')
def portal_auth_any_api():
    return _portal_auth()

@app.get('/api/portal/auth/admin')
def portal_auth_admin_api():
    return _portal_auth('ADMIN')

@app.get('/')
def panel(): return send_file(ROOT/'panel.html')
@app.get('/editor-v1')
def editor_v1(): return send_file(ROOT/'editor_v1'/'index.html')
@app.get('/api/style')
def style(): return jsonify(json.loads((ROOT/'renderer'/'style.json').read_text(encoding='utf-8')))

@app.get('/api/drive/status')
def drive_status_api():
    token=request.args.get('token',''); session(token,'ADMIN')
    cfg=load_drive_bridge_cfg(); configured=bool(str(cfg.get('webapp_url') or '').strip()); online=False; error=''
    if configured:
        try: online=bool(drive_bridge_call('ping').get('ok'))
        except Exception as e: error=str(e)
    return jsonify(ok=True,configured=configured,online=online,webapp_url=cfg.get('webapp_url',''),error=error)

@app.post('/api/drive/config')
def drive_config_api():
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    url=str(d.get('webapp_url') or '').strip()
    if url and not url.startswith('https://script.google.com/macros/s/'): return jsonify(ok=False,error='Pega la URL /exec del Web App de EDITOR_LETRAS.'),400
    cfg=load_drive_bridge_cfg(); cfg['webapp_url']=url; cfg['api_key']=DRIVE_BRIDGE_KEY_DEFAULT or cfg.get('api_key',''); save_drive_bridge_cfg(cfg)
    if not url: return jsonify(ok=True,configured=False)
    try:
        pong=drive_bridge_call('ping'); return jsonify(ok=True,configured=True,online=True,message=pong.get('message','Drive conectado'))
    except Exception as e: return jsonify(ok=False,configured=True,online=False,error=str(e)),400

@app.get('/api/dropbox/status')
def dropbox_status_api():
    token=request.args.get('token',''); session(token,'ADMIN')
    cfg=load_dropbox_cfg(); connected=dropbox_connected(cfg)
    if connected:
        try: cfg=dropbox_ensure_namespace_context()
        except Exception: pass
    default=dropbox_default_folder(validate=True) if connected else None
    return jsonify(ok=True,configured=bool(cfg.get('app_key') and cfg.get('app_secret')),
      connected=connected,app_key=cfg.get('app_key',DROPBOX_APP_KEY_DEFAULT),
      account_name=cfg.get('account_name',''),account_email=cfg.get('account_email',''),
      default_folder=default)

@app.post('/api/dropbox/default-folder')
def dropbox_default_folder_api():
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    if not dropbox_connected(): return jsonify(ok=False,error='Dropbox no está conectado.'),400
    folder_id=str(d.get('folder_id') or '').strip()
    if not folder_id.startswith('id:'): return jsonify(ok=False,error='Selecciona una carpeta real de Dropbox; no se guarda la ruta escrita a mano.'),400
    try:
        meta=dropbox_folder_meta(folder_id); cfg=load_dropbox_cfg()
        cfg['default_folder_id']=meta['id']; cfg['default_folder_display']=meta['path_display']; cfg['default_folder_path_lower']=meta['path_lower']; save_dropbox_cfg(cfg)
        return jsonify(ok=True,folder=meta)
    except ValueError as e: return jsonify(ok=False,error=str(e)),400

@app.post('/api/dropbox/config')
def dropbox_config_api():
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    app_key=str(d.get('app_key') or '').strip() or DROPBOX_APP_KEY_DEFAULT
    app_secret=str(d.get('app_secret') or '').strip()
    if not app_key or not app_secret: return jsonify(ok=False,error='Falta App Key o App Secret.'),400
    cfg=load_dropbox_cfg(); cfg.update({'app_key':app_key,'app_secret':app_secret})
    for k in ('refresh_token','access_token','expires_at','account_name','account_email','account_id','root_namespace_id','home_namespace_id','home_path'): cfg.pop(k,None)
    save_dropbox_cfg(cfg)
    return jsonify(ok=True,app_key=app_key)

@app.get('/dropbox/connect')
def dropbox_connect():
    token=request.args.get('token',''); session(token,'ADMIN')
    cfg=load_dropbox_cfg()
    if not cfg.get('app_key') or not cfg.get('app_secret'):
        return '<h3>Primero guarda App Key y App Secret desde el panel ADMIN.</h3>',400
    state=secrets.token_urlsafe(28); DROPBOX_OAUTH_STATES[state]={'token':token,'created':time.time()}
    from urllib.parse import urlencode
    q=urlencode({'client_id':cfg['app_key'],'response_type':'code','redirect_uri':DROPBOX_REDIRECT_URI,'token_access_type':'offline','state':state})
    return redirect('https://www.dropbox.com/oauth2/authorize?'+q)

@app.get('/dropbox/callback')
def dropbox_callback():
    code=request.args.get('code',''); state=request.args.get('state',''); err=request.args.get('error_description') or request.args.get('error')
    st=DROPBOX_OAUTH_STATES.pop(state,None)
    if err: return '<h3>Dropbox no autorizó la conexión.</h3><p>'+str(err)+'</p>',400
    if not code or not st or time.time()-st.get('created',0)>900:
        return '<h3>Autorización Dropbox vencida o inválida. Vuelve a pulsar Conectar Dropbox.</h3>',400
    cfg=load_dropbox_cfg()
    try:
        r=requests.post('https://api.dropboxapi.com/oauth2/token',data={'code':code,'grant_type':'authorization_code','redirect_uri':DROPBOX_REDIRECT_URI},auth=(cfg['app_key'],cfg['app_secret']),timeout=30)
        if not r.ok: raise ValueError(r.text[:800])
        j=r.json(); cfg['refresh_token']=j.get('refresh_token') or cfg.get('refresh_token')
        cfg['access_token']=j.get('access_token',''); cfg['expires_at']=time.time()+float(j.get('expires_in') or 14400)
        if not cfg.get('refresh_token'): raise ValueError('Dropbox no devolvió refresh_token. Revisa que la autorización use acceso offline.')
        acc=dropbox_rpc('users/get_current_account',{},token=cfg['access_token'],path_root=False)
        ri=acc.get('root_info') or {}
        cfg['account_name']=(acc.get('name') or {}).get('display_name',''); cfg['account_email']=acc.get('email',''); cfg['account_id']=acc.get('account_id','')
        cfg['root_namespace_id']=str(ri.get('root_namespace_id') or '')
        cfg['home_namespace_id']=str(ri.get('home_namespace_id') or ri.get('root_namespace_id') or '')
        cfg['home_path']=str(ri.get('home_path') or '')
        save_dropbox_cfg(cfg)
    except Exception as e:
        app.logger.exception('oauth dropbox'); return '<h3>No se pudo completar Dropbox.</h3><pre>'+str(e)+'</pre>',500
    html='<!doctype html><meta charset="utf-8"><title>Dropbox conectado</title><body style="font-family:Segoe UI;background:#12141A;color:#F1EFEA;padding:40px;text-align:center"><h2>Dropbox conectado</h2><p>Ya puedes volver al panel DJGABO.</p><script>try{window.opener&&window.opener.postMessage({type:"dropbox:connected"},"*");setTimeout(()=>window.close(),700)}catch(e){}</script></body>'
    return html

@app.get('/api/dropbox/folders')
def dropbox_folders_api():
    token=request.args.get('token',''); session(token,'ADMIN')
    ref=str(request.args.get('ref','') or request.args.get('path','') or '').strip()
    if ref=='/': ref=''
    try:
        tok=dropbox_access_token(); out=[]
        if ref:
            cur=dropbox_folder_meta(ref); list_ref=cur['id']
            pl=cur['path_lower']; parts=pl.rstrip('/').split('/')
            parent_path='/'.join(parts[:-1]) or '/'
            current={'id':cur['id'],'display_path':cur['path_display'],'path_lower':cur['path_lower'],'name':cur['name'],'parent_ref':parent_path}
        else:
            list_ref=''; current={'id':'','display_path':'/','path_lower':'','name':'Dropbox','parent_ref':''}
        res=dropbox_rpc('files/list_folder',{'path':list_ref,'recursive':False,'include_deleted':False,'limit':500},token=tok)
        while True:
            for e in res.get('entries',[]):
                if e.get('.tag')=='folder':
                    out.append({'name':e.get('name',''),'id':e.get('id',''),'display_path':e.get('path_display') or e.get('path_lower') or e.get('name',''),'path_lower':e.get('path_lower') or '', 'shared':bool(e.get('sharing_info'))})
            if not res.get('has_more'): break
            res=dropbox_rpc('files/list_folder/continue',{'cursor':res['cursor']},token=tok)
        out.sort(key=lambda x:x['name'].casefold())
        return jsonify(ok=True,current=current,folders=out)
    except ValueError as e: return jsonify(ok=False,error=str(e)),400

@app.post('/api/call/<fn>')
def api_call(fn):
    args=(request.get_json(silent=True) or {}).get('args',[])
    try:
        result=dispatch(fn,args)
        response=jsonify(result)
        if fn=='iniciarSesionConsola' and result.get('token'):
            response.set_cookie(PORTAL_COOKIE_NAME,result['token'],max_age=SESSION_TTL_SECONDS,
              secure=IS_PRODUCTION,httponly=True,samesite='Lax',path='/')
        elif fn=='validarSesionConsola' and result.get('ok') and args:
            response.set_cookie(PORTAL_COOKIE_NAME,str(args[0]),max_age=SESSION_TTL_SECONDS,
              secure=IS_PRODUCTION,httponly=True,samesite='Lax',path='/')
        elif fn=='cerrarSesionConsola':
            response.delete_cookie(PORTAL_COOKIE_NAME,path='/',secure=IS_PRODUCTION,
              httponly=True,samesite='Lax')
        return response
    except PermissionError as e: return jsonify(error=str(e),ok=False),401
    except ValueError as e: return jsonify(error=str(e),ok=False),400
    except Exception as e:
        app.logger.exception('api %s',fn); return jsonify(error=str(e),ok=False),500

def dispatch(fn,a):
    if fn=='iniciarSesionConsola':
        role=str(a[0] if a else '').upper(); pwd=str(a[1] if len(a)>1 else '')
        if role not in ('ADMIN','CORRECTORA'): raise ValueError('Rol inválido.')
        ip=_client_ip(); _login_rate_check(ip)
        expected=ADMIN_PASSWORD if role=='ADMIN' else CORRECTORA_PASSWORD
        if expected and not secrets.compare_digest(pwd,expected):
            _login_failed(ip)
            raise ValueError('Contraseña incorrecta.')
        LOGIN_FAILURES.pop(ip,None)
        tok=secrets.token_urlsafe(32); SESSIONS[tok]=role; SESSION_ACTIVITY[tok]=time.time()
        return {'token':tok,'rol':role}
    if fn=='validarSesionConsola':
        tok=a[0] if a else ''
        try: return {'ok':True,'rol':session(tok)}
        except PermissionError: return {'ok':False}
    if fn=='cerrarSesionConsola':
        tok=a[0] if a else ''; SESSIONS.pop(tok,None); SESSION_ACTIVITY.pop(tok,None); return {'ok':True}
    if fn=='listarTrabajos':
        session(a[0]);
        with db() as c: rows=c.execute('SELECT * FROM jobs WHERE deleted=0 ORDER BY id DESC').fetchall()
        # Casos urgentes: hay Destino pero falta WAV/CDG en DB. Se corrigen ya para
        # evitar mostrar FALTA cuando Dropbox sí lo tiene. Máximo 3 para no frenar UI.
        if dropbox_connected():
            urgent=[dict(r) for r in rows if r['dropbox_folder_id'] and (not r['instrumental_dropbox_id'] or not r['cdg_dropbox_id'])][:3]
            for j in urgent:
                try: dropbox_reconcile_job(j['id'],job=j,force=True)
                except Exception as e: app.logger.warning('reconciliar urgente %s: %s',j['id'],e)
            with db() as c: rows=c.execute('SELECT * FROM jobs WHERE deleted=0 ORDER BY id DESC').fetchall()
        schedule_dropbox_reconcile(rows)
        return [pubjob(r) for r in rows]
    if fn=='listarPapelera':
        session(a[0],'ADMIN')
        with db() as c:
            return [dict(pubjob(r),eliminado=r['updated']) for r in c.execute('SELECT * FROM jobs WHERE deleted=1 ORDER BY id DESC').fetchall()]
    if fn=='obtenerDashboard':
        session(a[0]);
        with db() as c:
            rows=c.execute('SELECT * FROM jobs WHERE deleted=0').fetchall()
            cnt={'pendientes':sum(r['status']==EST_P for r in rows),'enCorreccion':sum(r['status']==EST_C for r in rows),'corregidas':sum(r['status']==EST_OK for r in rows),'terminadas':sum(r['status']==EST_TERM for r in rows),'copiadas':sum(r['copied']=='SI' for r in rows),'papelera':c.execute('SELECT count(*) n FROM jobs WHERE deleted=1').fetchone()['n']}
            nxt=next((pubjob(r) for r in rows if r['status'] in (EST_C,EST_P)),None)
            acts=[{'idTrabajo':r['job_id'],'accion':r['action'],'estadoNuevo':r['state'],'fecha':r['at']} for r in c.execute('SELECT * FROM logs ORDER BY id DESC LIMIT 12').fetchall()]
            return {'contadores':cnt,'productividad':{'corregidasHoy':0,'promedioMinutos':None,'colaPendiente':cnt['pendientes']+cnt['enCorreccion']},'siguienteTrabajo':nxt,'actividad':acts}
    if fn=='verificarDuplicado':
        artist=str(a[0]).strip().lower(); title=str(a[1]).strip().lower()
        with db() as c:
            r=c.execute('SELECT * FROM jobs WHERE lower(artist)=? AND lower(title)=? ORDER BY id DESC LIMIT 1',(artist,title)).fetchone()
            return pubjob(r) if r else None
    if fn=='crearTrabajo':
        raise ValueError('La creación de trabajos usa ahora subida multipart directa. Recarga el panel.')
    if fn=='obtenerTrabajoEditor':
        jid,token=a[0],a[1]; role=session(token)
        with db() as c:
            r=jobrow(c,jid)
            if (r['origin'] or '')=='HISTORICO_DRIVE':
                r=refresh_historical_from_drive(c,r,open_job=True,actor='Valeria' if role=='CORRECTORA' else 'Augusto')
            elif r['status']==EST_P:
                c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(EST_C,now(),jid)); log(c,jid,'ABRIR TRABAJO',EST_C); r=jobrow(c,jid)
            managed=_sheet_managed(r); result=dict(pubjob(r),letraMoises=r['lyrics_moises'],letraCorregida=r['lyrics_corrected'],versiones=[])
        if managed and result['origen']!='HISTORICO_DRIVE':
            drive_bridge_call('open_job',{'id':jid,'actor':'Valeria' if role=='CORRECTORA' else 'Augusto'})
        return result
    if fn=='obtenerAudioTrabajo':
        jid,token=a[0],a[1]; session(token)
        with db() as c: r=jobrow(c,jid)
        if (r['origin'] or '')=='HISTORICO_DRIVE':
            raw,mime,name,dur=drive_bridge_get_audio(jid)
            return {'audioData':'data:'+mime+';base64,'+base64.b64encode(raw).decode(),'nombreAudio':name,'duracionGuardada':dur or r['duration']}
        raw=(JOBS/jid/r['voice_filename']).read_bytes(); ext=Path(r['voice_filename']).suffix.lower(); mime='audio/wav' if ext=='.wav' else 'audio/mpeg'
        return {'audioData':'data:'+mime+';base64,'+base64.b64encode(raw).decode(),'duracionGuardada':r['duration']}
    if fn=='obtenerLetraTrabajo':
        jid,token=a[0],a[1]; session(token)
        with db() as c:
            r=jobrow(c,jid); r=refresh_historical_from_drive(c,r)
            return {'letraMoises':r['lyrics_moises'],'letraCorregida':r['lyrics_corrected']}
    if fn in ('autoguardarLetra','guardarLetraCorregida'):
        jid,letra,token=a[0],str(a[1]),a[2]; role=session(token)
        with db() as c:
            r=jobrow(c,jid)
            if _sheet_managed(r):
                action='save_corrected' if fn=='guardarLetraCorregida' else 'autosave'
                d=drive_bridge_call(action,{'id':jid,'lyrics':letra,'actor':'Valeria' if role=='CORRECTORA' else 'Augusto'})
                ver=int(d.get('version') or r['version'] or 1); st=str(d.get('status') or (EST_OK if fn=='guardarLetraCorregida' else r['status']))
                c.execute('UPDATE jobs SET lyrics_corrected=?,status=?,version=?,updated=? WHERE id=?',(str(d.get('lyrics') or letra),st,ver,now(),jid))
            else:
                ver=int(r['version'])+(1 if fn=='guardarLetraCorregida' else 0); st=EST_OK if fn=='guardarLetraCorregida' else r['status']
                c.execute('UPDATE jobs SET lyrics_corrected=?,status=?,version=?,updated=? WHERE id=?',(letra,st,ver,now(),jid))
            log(c,jid,'GUARDAR CORRECCIÓN' if fn=='guardarLetraCorregida' else 'AUTOGUARDAR',st)
            return {'ok':True,'version':ver,'estado':st,'versiones':[]}
    if fn=='marcarLetraCopiada':
        jid,token=a[0],a[1]; session(token)
        with db() as c:
            r=jobrow(c,jid)
            if _sheet_managed(r): drive_bridge_call('mark_copied',{'id':jid})
            c.execute("UPDATE jobs SET copied='SI',updated=? WHERE id=?",(now(),jid)); log(c,jid,'COPIAR LETRA',''); return {'ok':True}
    if fn=='marcarKaraokeTerminado':
        jid,token=a[0],a[1]; session(token,'ADMIN')
        with db() as c: snap=dict(jobrow(c,jid))
        if snap.get('dropbox_folder_id') and dropbox_connected():
            dropbox_reconcile_job(jid,job=snap,force=True)
        with db() as c:
            r=jobrow(c,jid)
            if not _job_remote_complete(r):
                raise ValueError('No se puede marcar Terminado: Dropbox aún no confirma WAV + CDG en el destino.')
            c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(EST_TERM,now(),jid)); log(c,jid,'KARAOKE TERMINADO',EST_TERM)
        master_state(jid,EST_TERM,'Dropbox confirmó WAV + CDG'); return {'estado':EST_TERM}
    if fn=='revertirTrabajoTerminado':
        jid,token=a[0],a[1]; session(token,'ADMIN')
        with db() as c: c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(EST_OK,now(),jid))
        master_state(jid,EST_OK,'Trabajo reabierto'); return {'estado':EST_OK}
    if fn in ('moverTrabajoPapelera','moverTrabajosPapelera'):
        ids=[a[0]] if fn=='moverTrabajoPapelera' else list(a[0]); token=a[1]; session(token,'ADMIN')
        with db() as c:
            for jid in ids: c.execute('UPDATE jobs SET deleted=1,status=?,updated=? WHERE id=?',(EST_DEL,now(),jid)); log(c,jid,'MOVER A PAPELERA',EST_DEL)
        for jid in ids: master_state(jid,EST_DEL,'Movido a papelera desde OVH')
        return {'ok':True}
    if fn=='restaurarTrabajo':
        jid,token=a[0],a[1]; session(token,'ADMIN')
        with db() as c: c.execute('UPDATE jobs SET deleted=0,status=?,updated=? WHERE id=?',(EST_P,now(),jid)); log(c,jid,'RESTAURAR',EST_P)
        master_state(jid,EST_P,'Restaurado desde OVH'); return {'ok':True}
    if fn=='duplicarTrabajo':
        jid,token=a[0],a[1]; session(token,'ADMIN')
        with db() as c:
            r=jobrow(c,jid); new=next_id(c); shutil.copytree(JOBS/jid,JOBS/new)
            t=now(); c.execute('INSERT INTO jobs(id,artist,title,status,created,updated,voice_filename,voice_original_filename,voice_drive_status,instrumental_filename,lyrics_moises,lyrics_corrected,dropbox_path,duration,size_bytes,version,project_json,instrumental_dropbox_path,instrumental_dropbox_id,cdg_dropbox_path,cdg_dropbox_id,dropbox_status,dropbox_folder_id,dropbox_display_path,sheet_master_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (new,r['artist'],r['title'],EST_P,t,t,r['voice_filename'],r['voice_original_filename'] or r['voice_filename'],'PENDIENTE',r['instrumental_filename'],r['lyrics_moises'],r['lyrics_corrected'],r['dropbox_path'],r['duration'],r['size_bytes'],r['version'],r['project_json'],r['instrumental_dropbox_path'],r['instrumental_dropbox_id'],'','','WAV_SUBIDO' if r['instrumental_dropbox_path'] else '',r['dropbox_folder_id'],r['dropbox_display_path'],'RESERVADO'))
            log(c,new,'DUPLICAR TRABAJO',EST_P); snapshot=dict(jobrow(c,new))
        master_reserve(new,snapshot['artist'],snapshot['title'],snapshot['voice_original_filename'],snapshot['lyrics_moises'],snapshot['size_bytes'],snapshot['duration'])
        try: backup_voice_to_drive(new)
        except Exception as e: app.logger.warning('Drive al duplicar %s: %s',new,e)
        return {'idTrabajo':new}
    if fn=='obtenerSiguienteTrabajo':
        jid,token=a[0],a[1]; session(token)
        with db() as c:
            r=c.execute('SELECT * FROM jobs WHERE deleted=0 AND id>? AND status IN (?,?) ORDER BY id LIMIT 1',(jid,EST_P,EST_C)).fetchone(); return pubjob(r) if r else None
    if fn in ('renovarBloqueoEdicion','liberarBloqueoEdicion'): session(a[1]); return {'ok':True}
    if fn=='reemplazarAudio':
        jid,payload,token=a[0],a[1],a[2]; session(token,'ADMIN')
        with db() as c:
            r=jobrow(c,jid); ext=Path(payload.get('nombreAudio') or '.mp3').suffix or '.mp3'; vn=safe_name(f"{r['artist']} - {r['title']} (Voz){ext}"); size=dataurl_save(payload['audioData'],JOBS/jid/vn)
            c.execute('UPDATE jobs SET voice_filename=?,duration=?,size_bytes=?,updated=? WHERE id=?',(vn,float(payload.get('duracion') or 0),size,now(),jid)); return {'ok':True}
    if fn=='restaurarVersion': raise ValueError('Versiones históricas de Google se conectarán en la siguiente etapa.')
    raise ValueError('Función local no implementada: '+fn)


def _provisional_master_stem(job):
    if job.get('instrumental_filename'):
        return Path(job['instrumental_filename']).stem
    return safe_name((str(job.get('artist') or '').strip()+' - '+str(job.get('title') or '').strip()).strip(' -'))

def _local_cdg_path(job):
    # Sólo compatibilidad de migración. Los CDG nuevos viven en RAM/backend o Dropbox.
    name=str(job.get('cdg_local_filename') or '').strip(); p=OUTPUT/job['id']/name if name else None
    return p if p and p.is_file() else None

def _rename_local_cdg(job,new_stem):
    new_name=safe_name(new_stem)+'.cdg'
    data,old_name=_cdg_cache_get(job['id'])
    if data:
        _cdg_cache_put(job['id'],data,new_name); return new_name
    p=_local_cdg_path(job)
    if p:
        # legado: se lee una vez, pasa a cache online y se elimina el persistente
        data=p.read_bytes(); _cdg_cache_put(job['id'],data,new_name)
        try: p.unlink()
        except Exception: pass
        return new_name
    return new_name if (job.get('cdg_dropbox_id') or job.get('cdg_dropbox_path')) else ''

def _master_cdg_filename(instrumental_filename):
    """El WAV es la autoridad: el CDG conserva exactamente su stem."""
    return safe_name(Path(str(instrumental_filename or '')).stem)+'.cdg'

def _move_remote_cdg_to_master(job,new_filename):
    """Renombra el CDG confirmado dentro de la MISMA carpeta Dropbox.

    Valida carpeta, ID y ruta antes de mover. Si ya existe el destino, conserva
    siempre el CDG registrado por el trabajo: primero lo copia al nombre maestro
    y sólo después elimina el nombre viejo, evitando perder el archivo fuente.
    """
    folder_ref=str(job.get('dropbox_folder_id') or '').strip()
    old_display=str(job.get('cdg_dropbox_path') or '').strip()
    old_id=str(job.get('cdg_dropbox_id') or '').strip()
    new_filename=Path(str(new_filename or '')).name
    if not folder_ref or not new_filename or (not old_display and not old_id): return None
    folder=dropbox_folder_meta(folder_ref)
    if old_display and _norm_dropbox_path(Path(old_display).parent)!=_norm_dropbox_path(folder['path_display']):
        raise ValueError('El CDG registrado está fuera de la carpeta Dropbox del trabajo; no se renombró.')
    ns=folder.get('namespace_id') or dropbox_home_namespace_id()
    base=folder.get('api_path_lower') or folder['path_lower']
    old_name=Path(old_display).name if old_display else ''
    source=None
    if old_name:
        source=dropbox_get_metadata_optional(dropbox_join(base,old_name),namespace_id=ns)
    if not source and old_id:
        try: source=dropbox_rpc('files/get_metadata',{'path':old_id,'include_media_info':False,'include_deleted':False},namespace_id=ns)
        except Exception: source=None
    if not source or source.get('.tag')!='file':
        raise ValueError('Dropbox ya no encuentra el CDG registrado para cambiarle el nombre.')
    source_id=str(source.get('id') or '').strip()
    if old_id and source_id and source_id!=old_id:
        raise ValueError('El CDG de Dropbox cambió de identidad; se detuvo el renombrado por seguridad.')
    old_name=str(source.get('name') or old_name or '').strip()
    if not old_name: raise ValueError('Dropbox no devolvió el nombre actual del CDG.')
    if old_name==new_filename:
        return _dropbox_home_meta(source)

    source_api=dropbox_join(base,old_name); target_api=dropbox_join(base,new_filename)
    # Dropbox no admite renombrar sólo mayúsculas/minúsculas en un paso.
    if old_name.casefold()==new_filename.casefold():
        temp_name='.__djgabo_'+str(job.get('id') or 'cdg')+'_'+secrets.token_hex(4)+'.cdg'
        temp_api=dropbox_join(base,temp_name)
        dropbox_rpc('files/move_v2',{'from_path':source_api,'to_path':temp_api,'autorename':False,'allow_ownership_transfer':False},namespace_id=ns)
        dropbox_rpc('files/move_v2',{'from_path':temp_api,'to_path':target_api,'autorename':False,'allow_ownership_transfer':False},namespace_id=ns)
    else:
        existing=dropbox_get_metadata_optional(target_api,namespace_id=ns)
        if existing and str(existing.get('id') or '')!=source_id:
            # El origen sigue intacto hasta que la copia canónica haya terminado.
            dropbox_rpc('files/delete_v2',{'path':target_api},namespace_id=ns)
            dropbox_rpc('files/copy_v2',{'from_path':source_api,'to_path':target_api,'autorename':False,'allow_ownership_transfer':False},namespace_id=ns)
            copied=dropbox_get_metadata_optional(target_api,namespace_id=ns)
            if not copied or copied.get('.tag')!='file':
                raise ValueError('Dropbox no confirmó la copia del CDG con el nombre maestro.')
            dropbox_rpc('files/delete_v2',{'path':source_api},namespace_id=ns)
        else:
            dropbox_rpc('files/move_v2',{'from_path':source_api,'to_path':target_api,'autorename':False,'allow_ownership_transfer':False},namespace_id=ns)
    moved=dropbox_get_metadata_optional(target_api,namespace_id=ns)
    if not moved or moved.get('.tag')!='file': raise ValueError('Dropbox no confirmó el CDG renombrado.')
    home=_dropbox_home_meta(moved); actual=str((home or {}).get('path_display') or '')
    expected=dropbox_join(folder['path_display'],new_filename)
    if _norm_dropbox_path(actual)!=_norm_dropbox_path(expected):
        raise ValueError('Dropbox dejó el CDG en una ruta distinta. Esperado: '+expected+' · Real: '+actual)
    return home

def ensure_job_master_cdg_name(jid,job=None):
    """Alinea cache, base y Dropbox con el nombre del instrumental WAV."""
    if job is None:
        with db() as c: job=dict(jobrow(c,jid))
    instrumental=str(job.get('instrumental_filename') or '').strip()
    if not instrumental: return {'changed':False,'cdg_name':str(job.get('cdg_local_filename') or '')}
    desired=_master_cdg_filename(instrumental); _rename_local_cdg(job,Path(desired).stem)
    remote=None
    old_remote_name=Path(str(job.get('cdg_dropbox_path') or '')).name
    if (job.get('cdg_dropbox_id') or job.get('cdg_dropbox_path')) and old_remote_name!=desired:
        remote=_move_remote_cdg_to_master(job,desired)
    remote_path=str((remote or {}).get('path_display') or '')
    remote_id=str((remote or {}).get('id') or '')
    with db() as c:
        c.execute("""UPDATE jobs SET cdg_local_filename=?,
                     cdg_dropbox_path=CASE WHEN ?<>'' THEN ? ELSE cdg_dropbox_path END,
                     cdg_dropbox_id=CASE WHEN ?<>'' THEN ? ELSE cdg_dropbox_id END,
                     canonical_name=?,updated=? WHERE id=?""",
                  (desired,remote_path,remote_path,remote_id,remote_id,Path(instrumental).stem,now(),jid))
        _sync_terminal_status(c,jid)
    return {'changed':bool(remote or str(job.get('cdg_local_filename') or '')!=desired),'cdg_name':desired,'dropbox_path':remote_path,'dropbox_id':remote_id}

def _delete_old_remote_name_best_effort(job,old_display,new_filename):
    if not old_display or Path(str(old_display)).name.casefold()==Path(new_filename).name.casefold() or not job.get('dropbox_folder_id'): return
    try:
        folder=dropbox_folder_meta(job['dropbox_folder_id']); ns=folder.get('namespace_id') or dropbox_home_namespace_id()
        old_api=dropbox_join(folder.get('api_path_lower') or folder['path_lower'],Path(str(old_display)).name)
        dropbox_delete_best_effort(old_api,namespace_id=ns)
    except Exception: pass

def publish_job_to_dropbox(jid):
    """Publica disponibles sin exigir copias persistentes locales.
    Antes de decidir qué falta, reconcilia la carpeta real: Dropbox manda."""
    with db() as c: job=dict(jobrow(c,jid))
    if job.get('instrumental_filename'):
        try: ensure_job_master_cdg_name(jid,job=job)
        except Exception as e: app.logger.warning('alinear nombre CDG %s: %s',jid,e)
        with db() as c: job=dict(jobrow(c,jid))
    if job.get('dropbox_folder_id') and dropbox_connected():
        try: dropbox_reconcile_job(jid,job=job,force=True)
        except Exception as e: app.logger.warning('reconciliar antes de publicar %s: %s',jid,e)
        with db() as c: job=dict(jobrow(c,jid))
    cdg_data,cdg_cache_name=_cdg_cache_get(jid); wav_cache=_wav_cache_get(jid)
    if not job.get('dropbox_folder_id'):
        st='CDG_ONLINE_SIN_DESTINO' if (cdg_data or job.get('cdg_dropbox_id')) else ('WAV_ONLINE_SIN_DESTINO' if (wav_cache or job.get('instrumental_dropbox_id')) else 'SIN_DESTINO')
        with db() as c: c.execute('UPDATE jobs SET dropbox_status=?,updated=? WHERE id=?',(st,now(),jid))
        return {'status':st,'uploaded_cdg':False,'uploaded_wav':False,'folder':''}
    if not dropbox_connected():
        st='PENDIENTE_DROPBOX';
        with db() as c: c.execute('UPDATE jobs SET dropbox_status=?,updated=? WHERE id=?',(st,now(),jid))
        return {'status':st,'uploaded_cdg':False,'uploaded_wav':False,'folder':job.get('dropbox_display_path') or ''}
    folder_ref=job['dropbox_folder_id']; expected=job.get('dropbox_display_path') or ''
    uploaded_wav=bool(job.get('instrumental_dropbox_id') and _norm_dropbox_path(Path(str(job.get('instrumental_dropbox_path') or '')).parent)==_norm_dropbox_path(expected))
    uploaded_cdg=bool(job.get('cdg_dropbox_id') and _norm_dropbox_path(Path(str(job.get('cdg_dropbox_path') or '')).parent)==_norm_dropbox_path(expected))
    wav_display=str(job.get('instrumental_dropbox_path') or ''); wav_id=str(job.get('instrumental_dropbox_id') or '')
    cdg_display=str(job.get('cdg_dropbox_path') or ''); cdg_id=str(job.get('cdg_dropbox_id') or '')
    # Una copia pendiente significa una versión nueva: se publica aunque Dropbox
    # ya tenga una versión anterior con el mismo nombre.
    if wav_cache:
        up=dropbox_upload_bytes(wav_cache['data'],folder_ref,wav_cache['name'],mode='overwrite',expected_display_path=expected)
        uploaded_wav=True; wav_display=up.get('path_display') or wav_cache['name']; wav_id=up.get('id',''); _wav_cache_pop(jid)
    # compatibilidad con WAV local de versiones anteriores
    if not uploaded_wav and job.get('instrumental_filename'):
        ip=JOBS/jid/job['instrumental_filename']
        if ip.is_file():
            up=dropbox_upload_file(ip,folder_ref,ip.name,mode='overwrite',max_attempts=3,expected_display_path=expected)
            uploaded_wav=True; wav_display=up.get('path_display') or ip.name; wav_id=up.get('id','')
    if cdg_data:
        nm=cdg_cache_name or job.get('cdg_local_filename') or (_provisional_master_stem(job)+'.cdg')
        up=dropbox_upload_bytes(cdg_data,folder_ref,nm,mode='overwrite',expected_display_path=expected)
        uploaded_cdg=True; cdg_display=up.get('path_display') or nm; cdg_id=up.get('id','')
    cp=_local_cdg_path(job)
    if not uploaded_cdg and cp:
        up=dropbox_upload_file(cp,folder_ref,cp.name,mode='overwrite',max_attempts=3,expected_display_path=expected)
        uploaded_cdg=True; cdg_display=up.get('path_display') or cp.name; cdg_id=up.get('id','')
    if uploaded_cdg and uploaded_wav: st='COMPLETO'
    elif uploaded_cdg: st='CDG_SUBIDO_AUDIO_PENDIENTE'
    elif uploaded_wav: st='WAV_SUBIDO_CDG_PENDIENTE'
    else: st='DESTINO_ASIGNADO'
    with db() as c:
        c.execute("""UPDATE jobs SET instrumental_dropbox_path=CASE WHEN ?<>'' THEN ? ELSE instrumental_dropbox_path END,
                     instrumental_dropbox_id=CASE WHEN ?<>'' THEN ? ELSE instrumental_dropbox_id END,
                     cdg_dropbox_path=CASE WHEN ?<>'' THEN ? ELSE cdg_dropbox_path END,
                     cdg_dropbox_id=CASE WHEN ?<>'' THEN ? ELSE cdg_dropbox_id END,
                     dropbox_status=?,updated=? WHERE id=?""",
                  (wav_display,wav_display,wav_id,wav_id,cdg_display,cdg_display,cdg_id,cdg_id,st,now(),jid))
        _sync_terminal_status(c,jid)
        log(c,jid,'PUBLICAR DISPONIBLES DROPBOX',st)
    if cdg_data and uploaded_cdg and cdg_id: _cdg_cache_pop(jid)
    try: master_sync(jid,'Dropbox: '+st+' · WAV '+('OK' if uploaded_wav else 'PENDIENTE')+' · CDG '+('OK' if uploaded_cdg else 'PENDIENTE'))
    except Exception as e: app.logger.warning('Sheet maestro al publicar %s: %s',jid,e)
    return {'status':st,'uploaded_cdg':uploaded_cdg,'uploaded_wav':uploaded_wav,'folder':expected,'cdg_path':cdg_display,'wav_path':wav_display}

def attach_instrumental(jid,filestorage):
    if not filestorage or not filestorage.filename: raise ValueError('Falta el instrumental WAV.')
    if Path(filestorage.filename).suffix.lower()!='.wav': raise ValueError('El instrumental debe ser WAV.')
    artist,title=master_identity(filestorage.filename); inst_name=safe_name(Path(filestorage.filename).name); master_stem=Path(inst_name).stem; desired_cdg=_master_cdg_filename(inst_name)
    with db() as c: old=dict(jobrow(c,jid))
    wav_display=''; wav_id=''
    if old.get('dropbox_folder_id') and dropbox_connected():
        up=dropbox_upload_filestorage_online(filestorage,old['dropbox_folder_id'],inst_name,expected_display_path=old.get('dropbox_display_path') or '')
        wav_display=up.get('path_display') or inst_name; wav_id=up.get('id','')
    else:
        filestorage.stream.seek(0); data=filestorage.stream.read()
        if not data: raise ValueError('El WAV llegó vacío.')
        _wav_cache_put(jid,data,inst_name)
    with db() as c:
        c.execute("UPDATE jobs SET artist=?,title=?,instrumental_filename=?,canonical_name=?,cdg_local_filename=?, instrumental_dropbox_path=CASE WHEN ?<>'' THEN ? ELSE instrumental_dropbox_path END, instrumental_dropbox_id=CASE WHEN ?<>'' THEN ? ELSE instrumental_dropbox_id END,updated=? WHERE id=?",
                  (artist,title,inst_name,master_stem,desired_cdg,wav_display,wav_display,wav_id,wav_id,now(),jid))
        log(c,jid,'VINCULAR INSTRUMENTAL ONLINE',master_stem)
    ensure_job_master_cdg_name(jid)
    schedule_timings_rename(jid)
    result=publish_job_to_dropbox(jid)
    try: master_sync(jid,'WAV vinculado; su nombre define artista, título, CDG y timings')
    except Exception as e: app.logger.warning('Sheet maestro al vincular WAV %s: %s',jid,e)
    with db() as c: fresh=dict(jobrow(c,jid))
    return dict(result,job_id=jid,artist=artist,title=title,instrumental=inst_name,cdg_name=fresh.get('cdg_local_filename') or desired_cdg)

def _norm_match(v):
    v=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().lower()
    v=re.sub(r'\b(voz|voces|voice|vocals?|acapella|a\s*capella|solo\s+voz|voz\s+principal|instrumental|karaoke|pista|stem|stems|bs|roformer|lead|back|sw)\b',' ',v)
    v=re.sub(r'\b[abcdefg](?:#|b)?\s*(?:major|minor|mayor|menor)\b',' ',v)
    v=re.sub(r'\b\d+\s*(?:bpm|hz)\b',' ',v)
    v=re.sub(r'[^a-z0-9]+',' ',v); return ' '.join(v.split())

def _match_score(filename,job):
    try: a,t=master_identity(filename)
    except Exception: a=''; t=Path(filename).stem
    fa=_norm_match(a); ft=_norm_match(t); ja=_norm_match(job.get('artist')); jt=_norm_match(job.get('title')); jv=_norm_match(job.get('voice_filename'))
    seq=lambda x,y: difflib.SequenceMatcher(None,x,y).ratio() if x and y else 0.0
    def contained(x,y):
        xs=set(str(x or '').split()); ys=set(str(y or '').split())
        if not xs or not ys: return 0.0
        # Dos o más palabras completas permiten reconocer el título limpio
        # dentro de nombres técnicos largos (tono, BPM, modelo de separación).
        if min(len(xs),len(ys))<2: return 1.0 if xs==ys else 0.0
        return len(xs & ys)/min(len(xs),len(ys))
    title=max(seq(ft,jt),seq(ft,jv),contained(ft,jt),contained(ft,jv))
    wav_combo=(fa+' '+ft).strip(); job_combo=(ja+' '+jt+' '+jv).strip()
    combo=max(seq(wav_combo,(ja+' '+jt).strip()),contained(wav_combo,job_combo))
    artist=max(seq(fa,ja),contained(fa,ja))
    # Un título igual no basta si pertenece a otro artista. A la vez, el bag de
    # palabras permite recuperar históricos con artista/título intercambiados o
    # rodeados de etiquetas técnicas de separación vocal.
    structured=(0.58*title+0.32*artist+0.10*combo) if fa and ja else (0.82*title+0.18*combo)
    unordered=0.92*combo+0.08*artist
    return max(structured,unordered)

# ---------------------------------------------------------------------------
# MEJORA 1: Dropbox como fuente de verdad. Un trabajo puede tener su WAV/CDG ya
# publicados en Dropbox aunque el registro local no lo sepa (reinstalación del
# panel, edición manual en Dropbox, etc.). Esto reconcilia contra los archivos
# reales de la carpeta de destino y AUTO-REPARA los campos locales cuando los
# encuentra, para no volver a pedir subir algo que ya está publicado.
# ---------------------------------------------------------------------------
def _dropbox_candidate_names(job):
    names_wav=[]; names_cdg=[]
    inst=str(job.get('instrumental_filename') or '').strip()
    if inst: names_wav.append(inst)
    cdgn=str(job.get('cdg_local_filename') or '').strip()
    if cdgn: names_cdg.append(cdgn)
    stem=_provisional_master_stem(job)
    if stem:
        wav_alt=safe_name(stem)+'.wav'; cdg_alt=safe_name(stem)+'.cdg'
        if wav_alt not in names_wav: names_wav.append(wav_alt)
        if cdg_alt not in names_cdg: names_cdg.append(cdg_alt)
    return names_wav,names_cdg

def _dropbox_pick_remote(entries, expected_names, suffix, job):
    files=[e for e in entries if e.get('.tag')=='file' and str(e.get('name') or '').lower().endswith(suffix)]
    by_name={str(e.get('name') or '').casefold():e for e in files}
    for nm in expected_names:
        hit=by_name.get(Path(nm).name.casefold())
        if hit: return hit
    best=max(files,key=lambda e:_match_score(e.get('name',''),job),default=None)
    if best and _match_score(best.get('name',''),job)>=0.78: return best
    return None

def _dropbox_home_meta(entry):
    """Convierte metadata del namespace compartido a la ruta visible HOME."""
    if not entry: return None
    fid=str(entry.get('id') or '').strip()
    if not fid: return entry
    try:
        meta=dropbox_rpc('files/get_metadata',{'path':fid,'include_media_info':False,'include_deleted':False},namespace_id=dropbox_home_namespace_id())
        return meta if meta.get('.tag')=='file' else entry
    except Exception:
        return entry

def dropbox_reconcile_job(jid, job=None, force=False):
    """Dropbox es fuente de verdad BIDIRECCIONAL.

    Con Destino asignado, lista la carpeta real y decide desde esa evidencia:
    existe WAV/CDG -> guarda ID/path; no existe -> limpia IDs/paths antiguos.
    """
    jid=str(jid)
    if not force:
        with _DROPBOX_RECONCILE_LOCK:
            cached=_DROPBOX_RECONCILE_CACHE.get(jid)
        if cached and (time.time()-cached['ts'])<_DROPBOX_RECONCILE_TTL: return cached
    if job is None:
        with db() as c: job=dict(jobrow(c,jid))
    folder_ref=str(job.get('dropbox_folder_id') or '').strip()
    if not folder_ref or not dropbox_connected(): return None
    try:
        folder=dropbox_folder_meta(folder_ref)
        ns=folder.get('namespace_id') or dropbox_home_namespace_id()
        base_path=folder.get('api_path_lower') or folder.get('path_lower') or ''
        listing=dropbox_rpc('files/list_folder',{'path':base_path,'recursive':False},namespace_id=ns)
        entries=list(listing.get('entries') or [])
        while listing.get('has_more'):
            listing=dropbox_rpc('files/list_folder/continue',{'cursor':listing.get('cursor')},namespace_id=ns)
            entries.extend(listing.get('entries') or [])
        names_wav,names_cdg=_dropbox_candidate_names(job)
        found_wav=_dropbox_home_meta(_dropbox_pick_remote(entries,names_wav,'.wav',job))
        found_cdg=_dropbox_home_meta(_dropbox_pick_remote(entries,names_cdg,'.cdg',job))
    except Exception as e:
        app.logger.warning('reconciliar dropbox %s: %s',jid,e); return None

    wav_path=str((found_wav or {}).get('path_display') or '')
    wav_id=str((found_wav or {}).get('id') or '')
    cdg_path=str((found_cdg or {}).get('path_display') or '')
    cdg_id=str((found_cdg or {}).get('id') or '')
    wav_ready=bool(wav_id); cdg_ready=bool(cdg_id)
    st='COMPLETO' if (wav_ready and cdg_ready) else ('CDG_SUBIDO_AUDIO_PENDIENTE' if cdg_ready else ('WAV_SUBIDO_CDG_PENDIENTE' if wav_ready else 'DESTINO_ASIGNADO'))
    with db() as c:
        before=dict(jobrow(c,jid))
        c.execute('''UPDATE jobs SET instrumental_dropbox_path=?,instrumental_dropbox_id=?,
                     cdg_dropbox_path=?,cdg_dropbox_id=?,dropbox_status=?,updated=? WHERE id=?''',
                  (wav_path,wav_id,cdg_path,cdg_id,st,now(),jid))
        r=_sync_terminal_status(c,jid)
        changed=(str(before.get('instrumental_dropbox_id') or '')!=wav_id or
                 str(before.get('cdg_dropbox_id') or '')!=cdg_id or
                 str(before.get('dropbox_status') or '')!=st or
                 str(before.get('status') or '')!=str(r['status'] or ''))
        if changed: log(c,jid,'RECONCILIAR DROPBOX (fuente de verdad)',st)
    if changed and _sheet_managed(before):
        try: master_sync(jid,'Dropbox reconciliado: '+st)
        except Exception as e: app.logger.warning('Sheet maestro al reconciliar Dropbox %s: %s',jid,e)
    result={'ts':time.time(),'wav':wav_ready,'cdg':cdg_ready,'status':st}
    with _DROPBOX_RECONCILE_LOCK: _DROPBOX_RECONCILE_CACHE[jid]=result
    return result

def schedule_dropbox_reconcile(rows):
    """Valida en segundo plano también los ✓ existentes para detectar archivos
    movidos/eliminados. No bloquea la UI y usa TTL + límite por tanda."""
    if not dropbox_connected(): return
    candidates=[]
    for r in rows:
        job=dict(r); jid=str(job['id'])
        if not job.get('dropbox_folder_id'): continue
        with _DROPBOX_RECONCILE_LOCK: cached=_DROPBOX_RECONCILE_CACHE.get(jid)
        if cached and (time.time()-cached['ts'])<_DROPBOX_RECONCILE_TTL: continue
        with _DROPBOX_RECONCILE_LOCK:
            if jid in _DROPBOX_RECONCILE_INFLIGHT: continue
            _DROPBOX_RECONCILE_INFLIGHT.add(jid)
        candidates.append(job)
    if not candidates: return
    batch=candidates[:_DROPBOX_RECONCILE_MAX_PER_PASS]
    for job in candidates[_DROPBOX_RECONCILE_MAX_PER_PASS:]:
        with _DROPBOX_RECONCILE_LOCK: _DROPBOX_RECONCILE_INFLIGHT.discard(str(job['id']))
    def worker(items):
        for job in items:
            jid=str(job['id'])
            try: dropbox_reconcile_job(jid,job=job,force=True)
            except Exception as e: app.logger.warning('reconciliar dropbox %s: %s',jid,e)
            finally:
                with _DROPBOX_RECONCILE_LOCK: _DROPBOX_RECONCILE_INFLIGHT.discard(jid)
    threading.Thread(target=worker,args=(batch,),daemon=True,name='dropbox-reconcile').start()

@app.post('/api/jobs/assign-destination')
def assign_destination_api():
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    ids=[str(x) for x in (d.get('job_ids') or []) if str(x).strip()]; folder_id=str(d.get('folder_id') or '').strip()
    if not ids: return jsonify(ok=False,error='Selecciona al menos un trabajo.'),400
    if not folder_id.startswith('id:'): return jsonify(ok=False,error='Selecciona una carpeta real de Dropbox.'),400
    try:
        folder=validate_dropbox_folder(folder_id); cfg=load_dropbox_cfg(); cfg['default_folder_id']=folder['id']; cfg['default_folder_display']=folder['path_display']; cfg['default_folder_path_lower']=folder['path_lower']; save_dropbox_cfg(cfg)
        with db() as c:
            for jid in ids:
                jobrow(c,jid); c.execute('UPDATE jobs SET dropbox_folder_id=?,dropbox_display_path=?,dropbox_path=?,dropbox_status=?,updated=? WHERE id=?',(folder['id'],folder['path_display'],folder['path_lower'],'DESTINO_ASIGNADO',now(),jid)); log(c,jid,'ASIGNAR DESTINO',folder['path_display'])
        results=[]
        for jid in ids:
            try: results.append(dict(job_id=jid,**publish_job_to_dropbox(jid)))
            except Exception as e: results.append({'job_id':jid,'status':'PENDIENTE_DROPBOX','error':str(e)})
        return jsonify(ok=True,folder=folder,results=results,count=len(ids))
    except ValueError as e: return jsonify(ok=False,error=str(e)),400

@app.post('/api/jobs/<jid>/instrumental')
def upload_instrumental_api(jid):
    session(request.form.get('token'),'ADMIN')
    try: return jsonify(ok=True,**attach_instrumental(jid,request.files.get('instrumental')))
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e: app.logger.exception('instrumental %s',jid); return jsonify(ok=False,error=str(e)),500

@app.post('/api/instrumentals/prepare-direct')
def prepare_direct_instrumental_api():
    """Empareja por nombre y entrega un enlace PC -> Dropbox de ruta fija."""
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    filename=Path(str(d.get('filename') or '')).name; size=int(d.get('size') or 0)
    if Path(filename).suffix.lower()!='.wav': return jsonify(ok=False,error='Sólo se permite un instrumental WAV.'),400
    if size<=0: return jsonify(ok=False,error='El WAV está vacío.'),400
    if size>140*1024*1024:
        return jsonify(ok=False,error='Este WAV supera 140 MB. Cópialo con Dropbox para Windows a la carpeta destino y luego usa Comprobar Dropbox.'),413
    explicit=str(d.get('job_id') or '').strip()
    try:
        with db() as c:
            if explicit:
                rows=[dict(jobrow(c,explicit))]
            else:
                subset=d.get('candidate_ids') or []; subset={str(x) for x in subset} if isinstance(subset,list) else set()
                rows=[dict(r) for r in c.execute("SELECT * FROM jobs WHERE deleted=0 AND (instrumental_filename IS NULL OR instrumental_filename='')").fetchall()]
                if subset: rows=[r for r in rows if r['id'] in subset]
        if not rows: return jsonify(ok=False,error='No hay trabajos disponibles para vincular ese WAV.'),400
        ranked=sorted(((round(_match_score(filename,r),4),r) for r in rows),key=lambda x:x[0],reverse=True)
        score,best=ranked[0]; second=ranked[1][0] if len(ranked)>1 else 0
        if (not explicit) and (score<0.78 or (score-second)<0.035):
            return jsonify(ok=True,matched=False,best={'idTrabajo':best['id'],'artista':best['artist'],'titulo':best['title'],'score':score},second_score=second,filename=filename)
        if not best.get('dropbox_folder_id'):
            raise ValueError('El trabajo '+best['id']+' todavía no tiene carpeta destino en Dropbox.')
        prepared=dropbox_temporary_upload_link(best['dropbox_folder_id'],filename,expected_display_path=best.get('dropbox_display_path') or '',duration=3600)
        return jsonify(ok=True,matched=True,job_id=best['id'],artist=best['artist'],title=best['title'],score=score,**prepared)
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e: app.logger.exception('preparar wav directo'); return jsonify(ok=False,error='No se pudo preparar la subida directa: '+str(e)),500

@app.post('/api/instrumentals/prepare-new-direct')
def prepare_new_direct_instrumental_api():
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    filename=Path(str(d.get('filename') or '')).name; size=int(d.get('size') or 0)
    if Path(filename).suffix.lower()!='.wav': return jsonify(ok=False,error='El instrumental debe ser WAV.'),400
    if size<=0: return jsonify(ok=False,error='El WAV está vacío.'),400
    if size>140*1024*1024:
        return jsonify(ok=False,error='Este WAV supera 140 MB. Súbelo con Dropbox para Windows y crea/vincula el trabajo después.'),413
    folder_id=str(d.get('folder_id') or '').strip(); display=str(d.get('folder_display') or '').strip()
    if not folder_id.startswith('id:'): return jsonify(ok=False,error='Elige la carpeta final de Dropbox.'),400
    try: return jsonify(ok=True,**dropbox_temporary_upload_link(folder_id,filename,expected_display_path=display,duration=3600))
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e: app.logger.exception('preparar wav nuevo directo'); return jsonify(ok=False,error='No se pudo preparar Dropbox: '+str(e)),500

def register_direct_instrumental(jid,filename,expected_size=0):
    filename=Path(str(filename or '')).name
    if Path(filename).suffix.lower()!='.wav': raise ValueError('El archivo confirmado no es WAV.')
    with db() as c: old=dict(jobrow(c,jid))
    if not old.get('dropbox_folder_id'): raise ValueError('El trabajo no tiene carpeta destino.')
    home=dropbox_confirm_uploaded_file(old['dropbox_folder_id'],filename,expected_size,old.get('dropbox_display_path') or '')
    file_id=str(home.get('id') or '').strip(); actual=str(home.get('path_display') or '').strip()
    artist,title=master_identity(filename); master_stem=Path(filename).stem; desired_cdg=_master_cdg_filename(filename)
    with db() as c:
        c.execute("""UPDATE jobs SET artist=?,title=?,instrumental_filename=?,canonical_name=?,
                     cdg_local_filename=?,
                     instrumental_dropbox_path=?,instrumental_dropbox_id=?,updated=? WHERE id=?""",
                  (artist,title,filename,master_stem,desired_cdg,actual,file_id,now(),jid))
        log(c,jid,'WAV DIRECTO PC A DROPBOX · NOMBRE MAESTRO',master_stem)
    ensure_job_master_cdg_name(jid)
    schedule_timings_rename(jid)
    with db() as c:
        r=_sync_terminal_status(c,jid)
        st='COMPLETO' if r['cdg_dropbox_id'] else 'WAV_SUBIDO_CDG_PENDIENTE'
        c.execute('UPDATE jobs SET dropbox_status=?,updated=? WHERE id=?',(st,now(),jid)); log(c,jid,'WAV DIRECTO PC A DROPBOX',st)
    try: master_sync(jid,'WAV directo confirmado; nombre instrumental aplicado como identidad maestra')
    except Exception as e: app.logger.warning('Sheet maestro al confirmar WAV %s: %s',jid,e)
    return {'job_id':jid,'artist':artist,'title':title,'filename':filename,'dropbox_path':actual,'dropbox_id':file_id,'status':st}

@app.post('/api/jobs/<jid>/instrumental/direct-confirm')
def confirm_direct_instrumental_api(jid):
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    try: return jsonify(ok=True,**register_direct_instrumental(jid,d.get('filename'),int(d.get('size') or 0)))
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e: app.logger.exception('confirmar wav directo %s',jid); return jsonify(ok=False,error='No se pudo confirmar el WAV: '+str(e)),500

@app.post('/api/jobs/reconcile-dropbox')
def reconcile_selected_dropbox_api():
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    ids=[str(x) for x in (d.get('job_ids') or []) if str(x).strip()]
    if not ids: return jsonify(ok=False,error='Selecciona al menos un trabajo.'),400
    results=[]
    for jid in ids[:50]:
        try:
            with db() as c: snapshot=dict(jobrow(c,jid))
            result=dropbox_reconcile_job(jid,job=snapshot,force=True)
            results.append({'job_id':jid,'ok':True,**(result or {})})
        except Exception as e: results.append({'job_id':jid,'ok':False,'error':str(e)})
    return jsonify(ok=True,results=results)

@app.post('/api/instrumentals/auto-match')
def auto_match_instrumental_api():
    session(request.form.get('token'),'ADMIN'); f=request.files.get('instrumental')
    if not f or not f.filename: return jsonify(ok=False,error='Falta WAV.'),400
    if Path(f.filename).suffix.lower()!='.wav': return jsonify(ok=False,error='Sólo WAV.'),400
    try:
        subset=json.loads(request.form.get('candidate_ids') or '[]')
        subset={str(x) for x in subset} if isinstance(subset,list) else set()
    except Exception: subset=set()
    with db() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM jobs WHERE deleted=0 AND (instrumental_filename IS NULL OR instrumental_filename='')").fetchall()]
    if subset: rows=[r for r in rows if r['id'] in subset]
    if not rows: return jsonify(ok=False,error='No hay trabajos sin instrumental en ese grupo.'),400
    ranked=sorted(((round(_match_score(f.filename,r),4),r) for r in rows),key=lambda x:x[0],reverse=True)
    best_score,best=ranked[0]; second=ranked[1][0] if len(ranked)>1 else 0
    if best_score<0.78 or (best_score-second)<0.035:
        return jsonify(ok=True,matched=False,best={'idTrabajo':best['id'],'artista':best['artist'],'titulo':best['title'],'score':best_score},second_score=second,filename=f.filename)
    result=attach_instrumental(best['id'],f)
    return jsonify(ok=True,matched=True,score=best_score,**result)

def _ai_task_set(task_id, **fields):
    with _AI_TASK_LOCK:
        task=_AI_TASKS.setdefault(str(task_id),{
            'id':str(task_id),'status':'queued','progress':0,'stage':'Preparando…',
            'created':time.time(),'updated':time.time()
        })
        task.update(fields); task['updated']=time.time()
        if len(_AI_TASKS)>40:
            old=sorted(_AI_TASKS.items(),key=lambda kv:kv[1].get('updated',0))[:-30]
            for key,_ in old: _AI_TASKS.pop(key,None)
        return dict(task)


def _ai_task_public(task_id):
    with _AI_TASK_LOCK:
        task=dict(_AI_TASKS.get(str(task_id)) or {})
    if not task: return None
    return {k:v for k,v in task.items() if k not in ('voice_path','inst_path','tmp_folder')}


def _detect_untranscribed_voice(audio_path, words, duration=0.0):
    """QA conservador para acapella: energía vocal sin ninguna palabra de Scribe."""
    try:
        proc=subprocess.run([
            'ffmpeg','-v','error','-i',str(audio_path),'-ac','1','-ar','8000',
            '-f','s16le','-acodec','pcm_s16le','pipe:1'
        ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180,check=True)
        pcm=array('h'); pcm.frombytes(proc.stdout)
        if sys.byteorder!='little': pcm.byteswap()
        if not pcm: return []
    except Exception as e:
        app.logger.warning('QA voz no transcrita: no pude decodificar %s: %s',audio_path,e)
        return []

    sr=8000; frame_n=int(sr*.20)
    dbs=[]
    for i in range(0,len(pcm)-frame_n+1,frame_n):
        fr=pcm[i:i+frame_n]
        if not fr: continue
        rms=math.sqrt(sum(float(x)*float(x) for x in fr)/len(fr))/32768.0
        dbs.append(20.0*math.log10(max(rms,1e-7)))
    if not dbs: return []

    sorted_db=sorted(dbs)
    noise=sorted_db[max(0,min(len(sorted_db)-1,int(len(sorted_db)*.20)))]
    threshold=max(-52.0,min(-30.0,noise+11.0))

    spans=[]
    for w in words or []:
        try:
            a=float(w.get('start')); b=float(w.get('end'))
        except Exception:
            continue
        spans.append((max(0.0,a-.28),max(a,b)+.32))
    spans.sort()

    def covered(t):
        lo=0; hi=len(spans)
        while lo<hi:
            mid=(lo+hi)//2
            if spans[mid][1] < t: lo=mid+1
            else: hi=mid
        return lo<len(spans) and spans[lo][0] <= t <= spans[lo][1]

    active=[]
    for idx,dbv in enumerate(dbs):
        a=idx*.20; b=a+.20; mid=(a+b)/2
        if dbv>=threshold and not covered(mid):
            active.append((a,b,dbv))
    if not active: return []

    merged=[]
    for a,b,dbv in active:
        if not merged or a-merged[-1]['end']>.65:
            merged.append({'start':a,'end':b,'active':.20,'peak_db':dbv,'sum_db':dbv})
        else:
            m=merged[-1]; m['end']=b; m['active']+=.20
            m['peak_db']=max(m['peak_db'],dbv); m['sum_db']+=dbv

    out=[]; total_dur=float(duration or (len(pcm)/sr))
    for m in merged:
        dur=m['end']-m['start']
        if dur<1.20 or m['active']<.80: continue
        score=min(1.0,(m['active']/max(.2,dur))*.65 + max(0.0,(m['peak_db']-threshold)/18.0)*.35)
        if score<.28: continue
        out.append({
            'start':round(max(0.0,m['start']),3),
            'end':round(min(total_dur,m['end']),3),
            'duration':round(min(total_dur,m['end'])-max(0.0,m['start']),3),
            'kind':'untranscribed_voice','score':round(score,3),
            'peak_db':round(m['peak_db'],1),'threshold_db':round(threshold,1),
        })
    return out[:24]


def _ai_visual_units(text):
    """Aproxima el ancho visual de Impact sin depender de la UI."""
    total=0.0
    for ch in str(text or '').upper():
        if ch in 'MWÁÉÓÚQG': total+=1.20
        elif ch in 'IÍLJT1.,;:!¡?¿': total+=0.58
        elif ch==' ': total+=0.55
        else: total+=1.0
    return total


def _ai_balance_phrase(tokens, max_units=29.0):
    toks=[dict(t) for t in tokens if str(t.get('text') or '').strip()]
    if not toks: return []
    widths=[_ai_visual_units(t.get('text')) for t in toks]
    spaces=.55
    total=sum(widths)+spaces*max(0,len(toks)-1)
    n_lines=max(1,int(math.ceil(total/max_units)))
    n_lines=min(n_lines,max(1,len(toks)))
    prefix=[0.0]
    for i,w in enumerate(widths):
        prefix.append(prefix[-1]+w+(spaces if i else 0.0))
    target=total/n_lines
    inf=10**18
    dp=[[inf]*(len(toks)+1) for _ in range(n_lines+1)]
    back=[[None]*(len(toks)+1) for _ in range(n_lines+1)]
    dp[0][0]=0.0
    def span(i,j):
        val=prefix[j]-prefix[i]
        if i>0: val-=spaces
        return val
    for ln in range(1,n_lines+1):
        for j in range(ln,len(toks)+1):
            for i in range(ln-1,j):
                w=span(i,j)
                if w>max_units*1.12: continue
                count=j-i
                penalty=(w-target)**2
                if count==1 and len(toks)>2: penalty+=28.0
                last_txt=str(toks[j-1].get('text') or '')
                if re.search(r'[,;:]$',last_txt): penalty-=2.5
                if re.search(r'[.!?…]["”’\']?$',last_txt): penalty-=4.0
                score=dp[ln-1][i]+penalty
                if score<dp[ln][j]:
                    dp[ln][j]=score; back[ln][j]=i
    if not math.isfinite(dp[n_lines][len(toks)]) or dp[n_lines][len(toks)]>=inf/2:
        out=[]; cur=[]
        for t in toks:
            probe=cur+[t]
            txt=' '.join(str(x.get('text') or '').strip() for x in probe)
            if cur and _ai_visual_units(txt)>max_units:
                out.append(cur); cur=[t]
            else: cur=probe
        if cur: out.append(cur)
        return out
    cuts=[]; j=len(toks)
    for ln in range(n_lines,0,-1):
        i=back[ln][j]
        if i is None: return [toks]
        cuts.append((i,j)); j=i
    cuts.reverse()
    return [toks[i:j] for i,j in cuts]


def _ai_segment_scribe_words(items):
    """Automaquetado IA: frase -> líneas equilibradas; pausas >=2 s -> nueva pantalla."""
    clean=[dict(w) for w in (items or []) if str(w.get('text') or '').strip()]
    if not clean: return []
    groups=[]; phrase=[]
    SMART_PAGE_GAP=2.0
    PHRASE_GAP=.90
    def flush_phrase(page_break=False):
        nonlocal phrase
        if phrase:
            for line in _ai_balance_phrase(phrase):
                groups.append(('line',line))
            phrase=[]
        if page_break and groups and groups[-1][0]!='break':
            groups.append(('break',[]))
    for idx,item in enumerate(clean):
        phrase.append(item)
        txt=str(item.get('text') or '').strip()
        nxt=clean[idx+1] if idx+1<len(clean) else None
        gap=0.0
        if nxt is not None:
            try: gap=max(0.0,float(nxt.get('start'))-float(item.get('end')))
            except Exception: gap=0.0
        strong=bool(re.search(r'[.!?…]["”’\']?$',txt))
        phrase_cut=(nxt is None) or strong or gap>=PHRASE_GAP
        if phrase_cut:
            flush_phrase(page_break=(gap>=SMART_PAGE_GAP))
    flush_phrase(False)
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
            word={
                'id':f'w{wi:04d}','text':txt,
                'start_time':round(float(a),6) if a is not None else None,
                'end_time':round(float(b),6) if b is not None else None,
                'locked':False,'spoken':False,'vocal_role':None,
                'ai_confidence':float(item.get('confidence') or 0),
                'ai_status':str(item.get('qa_status') or ''),
                'scribe_text':item.get('scribe_text'),
                'ai_match_type':str(item.get('match_type') or ''),
            }
            if item.get('timing_repaired'):
                word['ai_timing_repaired']=True
                word['ai_timing_repair']=str(item.get('timing_repair') or 'repeat_microtiming_v1')
                word['ai_timing_repair_token']=str(item.get('timing_repair_token') or '')
                word['ai_original_start']=item.get('timing_original_start')
                word['ai_original_end']=item.get('timing_original_end')
                if not word['ai_match_type'] or word['ai_match_type']=='scribe_raw':
                    word['ai_match_type']='scribe_repeat_repaired'
            words.append(word)
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
        'ai':{'engine':'elevenlabs-scribe-v2','source_mode':source_mode,'format_version':3,
              'line_mode':'master' if source_mode=='compare_master' else 'balanced_visual_v3',
              'generated_at':now()}
    }


def _project_lyrics(project):
    return '\n'.join('' if seg.get('kind')=='break' else str(seg.get('text') or '')
                     for seg in (project.get('segments') or [])).strip()



def _ai_sync_http_with_progress(task_id,audio_path,voice_name,lyrics,duration):
    holder={}
    def do_call():
        try:
            with Path(audio_path).open('rb') as fh:
                holder['response']=requests.post(
                    'http://127.0.0.1:8097/api/elevenlabs/transcribe',
                    files={'audio':(voice_name,fh,'audio/mpeg')},
                    data={'lyrics':lyrics or '','language_code':'spa'},
                    timeout=(30,1200)
                )
        except Exception as exc:
            holder['error']=exc
    th=threading.Thread(target=do_call,daemon=True,name='scribe-http-'+str(task_id)[:8])
    th.start()
    started=time.monotonic()
    expected=max(18.0,min(150.0,10.0+max(0.0,float(duration or 0))*0.16))
    while th.is_alive():
        elapsed=time.monotonic()-started
        frac=min(.94,elapsed/max(1.0,expected))
        pct=55+int(frac*22)
        eta=max(0,int(round(expected-elapsed)))
        _ai_task_set(task_id,status='running',progress=pct,
                     stage='ElevenLabs Scribe v2 · sincronizando…',
                     eta_seconds=eta,elapsed_seconds=round(elapsed,1),
                     estimate=True)
        th.join(timeout=1.0)
    if holder.get('error'):
        raise holder['error']
    rr=holder.get('response')
    if rr is None:
        raise ValueError('ElevenLabs no devolvió respuesta.')
    if not rr.ok:
        try: detail=rr.json().get('detail') or rr.text[:800]
        except Exception: detail=rr.text[:800]
        raise ValueError('Scribe v2 no pudo sincronizar: '+str(detail))
    return rr

def _ai_local_voice_for_job(jid,job,tmp_dir):
    if (job.get('origin') or '')=='HISTORICO_DRIVE':
        info=drive_audio_info(jid)
        suffix=Path(str(info.get('name') or '')).suffix or '.mp3'
        dst=Path(tmp_dir)/('voice'+suffix)
        with dst.open('wb') as fh:
            for part in drive_audio_iter(jid):
                fh.write(part)
        return dst,Path(str(info.get('name') or 'audio.mp3')).name
    name=str(job.get('voice_filename') or '').strip()
    if not name: raise ValueError('El trabajo no tiene pista de voz.')
    src=JOBS/jid/name
    if not src.is_file(): raise ValueError('No encuentro la pista de voz del trabajo.')
    return src,name

def _ai_sync_existing_task(task_id,jid,use_existing_lyrics=True):
    try:
        with db() as c: job=dict(jobrow(c,jid))
        master_lyrics=str(job.get('lyrics_corrected') or job.get('lyrics_moises') or '').strip() if use_existing_lyrics else ''
        _ai_task_set(task_id,status='running',progress=53,stage='Preparando voz para ElevenLabs…',
                     idTrabajo=jid,eta_seconds=None,estimate=True)
        with tempfile.TemporaryDirectory(prefix='karaoke_full_ai_') as td0:
            voice_path,voice_name=_ai_local_voice_for_job(jid,job,td0)
            duration=float(job.get('duration') or 0)
            rr=_ai_sync_http_with_progress(task_id,voice_path,voice_name,master_lyrics,duration)
            _ai_task_set(task_id,status='running',progress=80,stage='Recibiendo letra y timings…',
                         eta_seconds=8,estimate=True)
            payload=rr.json(); ai_words=payload.get('words') or []
            if not ai_words: raise ValueError('Scribe v2 no devolvió palabras con tiempos.')
            _ai_task_set(task_id,status='running',progress=82,stage='Revisando repeticiones y microtimings…',eta_seconds=7,estimate=True)
            ai_words,repeat_micro_repairs=_ai_repair_repeated_microtimings(ai_words)
            source_mode='compare_master' if master_lyrics else 'scribe_only'
            _ai_task_set(task_id,progress=86,stage='Organizando líneas y estrofas…',eta_seconds=5,estimate=True)
            project=_ai_project_from_words(
                str(job.get('artist') or ''),str(job.get('title') or ''),str(job.get('voice_filename') or voice_name),
                duration,master_lyrics if source_mode=='compare_master' else '',ai_words,source_mode,jid=jid
            )
            old_project={}
            try: old_project=json.loads(job.get('project_json') or '{}')
            except Exception: old_project={}
            if isinstance(old_project,dict) and old_project.get('cdg_settings'):
                project['cdg_settings']=old_project['cdg_settings']
            final_lyrics=master_lyrics if source_mode=='compare_master' else _project_lyrics(project)
            if not final_lyrics:
                final_lyrics=str((payload.get('scribe') or {}).get('text') or '').strip()
            if not final_lyrics: raise ValueError('Scribe v2 no devolvió letra.')
            _ai_task_set(task_id,progress=92,stage='Revisando voz sin texto…',eta_seconds=3,estimate=True)
            gaps=_detect_untranscribed_voice(voice_path,ai_words,duration)
            ai=project.setdefault('ai',{})
            ai['voice_gaps']=gaps
            ai['scribe_word_count']=len(ai_words)
            ai['coverage_check']='audio_energy_vs_scribe'
            ai['repeat_microtiming_version']='REPEAT_MICROTIMING_V1'
            ai['repeat_microtiming_repairs']=repeat_micro_repairs
            diffs=sum(1 for w in ai_words if str(w.get('qa_status') or '').lower() not in ('','green') or str(w.get('match_type') or '').lower() in ('missing','substitution','mismatch'))
            flagged=sum(1 for w in ai_words if str(w.get('qa_status') or '').lower() not in ('','green'))
            ai['lyrics_diff_count']=diffs
            raw=json.dumps(project,ensure_ascii=False,indent=2).encode('utf-8')
            _ai_task_set(task_id,progress=97,stage='Guardando proyecto y respaldos…',eta_seconds=2,estimate=True)
            with db() as c:
                c.execute(
                    "UPDATE jobs SET project_json=?,lyrics_corrected=?,lyrics_moises=CASE WHEN COALESCE(lyrics_moises,'')='' THEN ? ELSE lyrics_moises END,status=?,updated=? WHERE id=?",
                    (json.dumps(project,ensure_ascii=False),final_lyrics,final_lyrics,EST_C,now(),jid)
                )
                log(c,jid,'ELEVENLABS · SINCRONIZAR LETRA COMPLETA',
                    source_mode+' · '+str(len(ai_words))+' palabras · diferencias='+str(diffs)+' · microtiming_repairs='+str(len(repeat_micro_repairs)))
            try:
                folder=JOBS/jid
                if folder.is_dir():
                    (folder/'letra_moises.txt').write_text(final_lyrics,encoding='utf-8')
            except Exception as e:
                app.logger.warning('No pude actualizar letra local %s: %s',jid,e)
            try: schedule_timings_backup(jid,raw)
            except Exception as e: app.logger.warning('Backup timings IA pendiente %s: %s',jid,e)
            try: master_sync(jid,'Sincronización completa con ElevenLabs Scribe v2')
            except Exception as e: app.logger.warning('Sheet maestro IA pendiente %s: %s',jid,e)
            _ai_task_set(task_id,status='done',progress=100,stage='Sincronización completada',
                         eta_seconds=0,estimate=False,
                         result={'idTrabajo':jid,'words':len(ai_words),'flagged':flagged,
                                 'diff_count':diffs,'voice_gaps':len(gaps),'source_mode':source_mode,
                                 'repeat_microtiming_repairs':len(repeat_micro_repairs)})
    except Exception as e:
        app.logger.exception('AI full sync %s',jid)
        _ai_task_set(task_id,status='error',progress=100,stage='Error al sincronizar',
                     eta_seconds=None,estimate=False,error=str(e))

@app.post('/api/jobs/<jid>/ai-sync/start')
def ai_sync_existing_start(jid):
    d=request.get_json(silent=True) or {}
    try:
        session(d.get('token'))
        with db() as c: job=dict(jobrow(c,jid))
        use_existing=bool(d.get('use_existing_lyrics',True))
        with _AI_TASK_LOCK:
            for tid,t in _AI_TASKS.items():
                if str(t.get('idTrabajo') or '')==str(jid) and t.get('status') in ('queued','running'):
                    return jsonify(ok=True,task_id=tid,idTrabajo=jid,reused=True),202
        task_id=secrets.token_urlsafe(12)
        _ai_task_set(task_id,status='queued',progress=52,stage='En cola para ElevenLabs…',
                     idTrabajo=jid,eta_seconds=None,estimate=True)
        threading.Thread(target=_ai_sync_existing_task,
                         args=(task_id,str(jid),use_existing),
                         daemon=True,name='ai-full-'+str(jid)).start()
        return jsonify(ok=True,task_id=task_id,idTrabajo=jid),202
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except Exception as e:
        app.logger.exception('AI full start %s',jid)
        return jsonify(ok=False,error='No se pudo iniciar la sincronización: '+str(e)),500

@app.get('/api/ai/tasks/<task_id>')
def ai_task_status_production(task_id):
    token=request.args.get('session_token','')
    try: session(token)
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    task=_ai_task_public(task_id)
    if not task: return jsonify(ok=False,error='Tarea IA no encontrada.'),404
    return jsonify(ok=True,task=task)

@app.get('/api/vendor/jsQR.js')
def vendor_jsqr_production():
    p=ROOT/'vendor'/'jsQR.js'
    if not p.is_file(): abort(404)
    return send_file(str(p),mimetype='application/javascript',conditional=True)


@app.post('/api/jobs/create')
def create_job_upload():
    """Crea trabajo y sube el WAV a la carpeta exacta elegida por ID estable."""
    token=request.form.get('session_token',''); uploaded_remote=''; uploaded_remote_namespace=''; uploaded_remote_was_new=False
    try:
        session(token,'ADMIN')
        if not dropbox_connected(): raise ValueError('Dropbox no está conectado. Configúralo una sola vez desde el panel ADMIN.')
        voice=request.files.get('voice'); inst=request.files.get('instrumental'); letra=str(request.form.get('lyrics','')).strip()
        direct_inst=str(request.form.get('instrumental_direct') or '')=='1'
        if not voice or not voice.filename: raise ValueError('Falta la voz MP3.')
        if (not direct_inst) and (not inst or not inst.filename): raise ValueError('Falta el instrumental WAV.')
        if Path(voice.filename).suffix.lower()!='.mp3': raise ValueError('La VOZ debe ser un archivo MP3.')
        source_inst_name=Path(str(request.form.get('instrumental_name') or (inst.filename if inst else ''))).name
        source_inst_size=int(request.form.get('instrumental_size') or 0)
        if Path(source_inst_name).suffix.lower()!='.wav': raise ValueError('El INSTRUMENTAL debe ser un archivo WAV.')
        req_artist=str(request.form.get('artist') or '').strip(); req_title=str(request.form.get('title') or '').strip()
        if req_artist and req_title: artist,title=req_artist,req_title
        else: artist,title=master_identity(source_inst_name)
        voice_duration=float(request.form.get('voice_duration') or 0)
        folder_id=str(request.form.get('dropbox_folder_id') or '').strip()
        if not folder_id.startswith('id:'): raise ValueError('Elige la carpeta final de Dropbox desde el navegador del panel.')
        folder=validate_dropbox_folder(folder_id)
        selected_display=str(request.form.get('dropbox_folder_display_path') or '').strip()
        if selected_display and _norm_dropbox_path(selected_display)!=_norm_dropbox_path(folder['path_display']):
            raise ValueError('La carpeta seleccionada en pantalla no coincide con el ID recibido. Vuelve a elegir la carpeta mensual antes de enviar.')
        # Actualizamos la carpeta mensual actual del ADMIN. Los trabajos guardan además su propio folder ID histórico.
        cfg=load_dropbox_cfg(); cfg['default_folder_id']=folder['id']; cfg['default_folder_display']=folder['path_display']; cfg['default_folder_path_lower']=folder['path_lower']; save_dropbox_cfg(cfg)
        with db() as c: jid=next_id(c)
        final_folder=JOBS/jid; tmp_folder=JOBS/f'.{jid}.uploading'
        if tmp_folder.exists(): shutil.rmtree(tmp_folder,ignore_errors=True)
        tmp_folder.mkdir(parents=True,exist_ok=True)
        inst_name=Path(source_inst_name).name if direct_inst else safe_name(source_inst_name); voice_name=safe_name(f'{artist} - {title} (Voz).mp3'); voice_original_name=safe_name(Path(voice.filename).name)
        inst_path=tmp_folder/inst_name; voice_path=tmp_folder/voice_name
        try:
            voice.save(voice_path)
            if not voice_path.exists() or voice_path.stat().st_size<=0: raise ValueError('La voz MP3 llegó vacía.')
            if direct_inst:
                up=dropbox_confirm_uploaded_file(folder['id'],inst_name,source_inst_size,folder['path_display'])
            else:
                inst.save(inst_path)
                if not inst_path.exists() or inst_path.stat().st_size<=0: raise ValueError('El instrumental WAV llegó vacío.')
                up=dropbox_upload_file(inst_path,folder['id'],inst_name,mode='overwrite',max_attempts=3,expected_display_path=folder['path_display'])
            wav_display=up.get('path_display') or dropbox_join(folder['path_display'],inst_name)
            if not direct_inst:
                uploaded_remote=up.get('_remote_api_path') or up.get('path_lower') or wav_display
                uploaded_remote_namespace=up.get('_folder_namespace_id') or ''
                uploaded_remote_was_new=not bool(up.get('_replaced_existing'))
            folder_display=up.get('_folder_display') or folder['path_display']; folder_path=up.get('_folder_path_lower') or folder['path_lower']; folder_id=up.get('_folder_id') or folder['id']
            meta={'idTrabajo':jid,'artista':artist,'titulo':title,'voz':voice_name,'instrumental':inst_name,'dropboxFolderId':folder_id,'dropboxPath':folder_path,'dropboxDisplayPath':folder_display,'instrumentalDropbox':wav_display,'creado':now()}
            (tmp_folder/'trabajo.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
            (tmp_folder/'letra_moises.txt').write_text(letra,encoding='utf-8')
            # El Sheet es el control maestro: la fila se reserva antes de aceptar
            # definitivamente el alta local. Así nunca queda un trabajo aislado.
            master_reserve(jid,artist,title,voice_original_name,letra,
                           size_bytes=voice_path.stat().st_size,duration=voice_duration)
            if final_folder.exists(): shutil.rmtree(final_folder,ignore_errors=True)
            tmp_folder.rename(final_folder)
            with db() as c:
                t=now(); c.execute('INSERT INTO jobs(id,artist,title,status,created,updated,voice_filename,voice_original_filename,voice_drive_status,instrumental_filename,lyrics_moises,dropbox_path,duration,size_bytes,instrumental_dropbox_path,instrumental_dropbox_id,dropbox_status,dropbox_folder_id,dropbox_display_path,sheet_master_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (jid,artist,title,EST_P,t,t,voice_name,voice_original_name,'PENDIENTE',inst_name,letra,folder_path,voice_duration,(final_folder/voice_name).stat().st_size,wav_display,up.get('id',''),'WAV_SUBIDO',folder_id,folder_display,'RESERVADO'))
                log(c,jid,'CREAR TRABAJO + SUBIR WAV DROPBOX',EST_P)
        except Exception:
            shutil.rmtree(tmp_folder,ignore_errors=True)
            if final_folder.exists(): shutil.rmtree(final_folder,ignore_errors=True)
            if uploaded_remote and uploaded_remote_was_new: dropbox_delete_best_effort(uploaded_remote,namespace_id=uploaded_remote_namespace)
            raise
        drive_warning=''
        try: backup_voice_to_drive(jid)
        except Exception as e:
            drive_warning=str(e); app.logger.warning('Acapella Drive pendiente %s: %s',jid,e)
        try: master_sync(jid,'Trabajo creado; WAV publicado directamente en Dropbox')
        except Exception as e: app.logger.warning('Sheet maestro pendiente %s: %s',jid,e)
        return jsonify(ok=True,idTrabajo=jid,artista=artist,titulo=title,vozGuardada=voice_name,vozDriveEstado='OK' if not drive_warning else 'PENDIENTE',vozDriveAviso=drive_warning,instrumentalGuardado=inst_name,instrumentalDropbox=wav_display,dropboxFolderId=folder_id,dropboxPath=folder_path,dropboxDisplayPath=folder_display)
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('crear trabajo multipart'); return jsonify(ok=False,error=str(e)),500

def _peak_task_set(jid, **kw):
    with _PEAKS_LOCK:
        t=_PEAK_TASKS.setdefault(str(jid),{'status':'queued','progress':0,'message':'Preparando audio online…'})
        t.update(kw); t['updated']=time.time()

def _peaks_fingerprint(job):
    """MEJORA 3: identifica si el audio de este trabajo cambió desde la última vez
    que se calculó su waveform, para poder reutilizar el cache sin recalcular."""
    if (job.get('origin') or '')=='HISTORICO_DRIVE':
        fid=str(job.get('legacy_audio_drive_id') or '')
        try: sz=int(drive_audio_info(job['id']).get('size') or 0)
        except Exception: sz=int(job.get('size_bytes') or 0)
        return f'drive:{fid}:{sz}'
    name=str(job.get('voice_filename') or '')
    p=JOBS/str(job['id'])/name if name else None
    try:
        st=p.stat(); return f'local:{name}:{st.st_size}:{int(st.st_mtime)}'
    except Exception:
        return f'local:{name}:{job.get("size_bytes") or 0}'

def _peaks_disk_path(jid): return PEAKS_DIR/f'{jid}.json'

def _peaks_disk_load(jid, fingerprint):
    p=_peaks_disk_path(jid)
    if not p.is_file(): return None
    try:
        data=json.loads(p.read_text(encoding='utf-8'))
        if str(data.get('fingerprint') or '')!=fingerprint: return None
        return {'ok':True,'rate':data['rate'],'min':data['min'],'max':data['max'],'duration':data['duration']}
    except Exception:
        return None

def _peaks_disk_save(jid, fingerprint, payload):
    try:
        out=dict(payload); out['fingerprint']=fingerprint
        tmp=_peaks_disk_path(jid).with_suffix('.tmp')
        tmp.write_text(json.dumps(out,ensure_ascii=False),encoding='utf-8')
        tmp.replace(_peaks_disk_path(jid))
    except Exception as e:
        app.logger.warning('No se pudo guardar el cache de waveform de %s: %s',jid,e)

def _build_peaks_task(jid):
    jid=str(jid)
    try:
        with db() as c: job=dict(jobrow(c,jid))
        _peak_task_set(jid,status='running',progress=1,message='Revisando cache de waveform…')
        fingerprint=_peaks_fingerprint(job)
        cached=_peaks_disk_load(jid,fingerprint)
        if cached:
            with _PEAKS_LOCK:
                _PEAKS_CACHE[jid]=cached
                while len(_PEAKS_CACHE)>12: _PEAKS_CACHE.pop(next(iter(_PEAKS_CACHE)))
            _peak_task_set(jid,status='ready',progress=100,message='Waveform cacheada · lista al instante')
            return
        # Si no hay cache, damos una pequeña prioridad al primer PLAY/seek antes de
        # iniciar la descarga completa necesaria para construir la waveform.
        if (job.get('origin') or '')=='HISTORICO_DRIVE':
            _peak_task_set(jid,status='queued',progress=2,message='Priorizando reproducción de audio…')
            time.sleep(1.0)
        _peak_task_set(jid,status='running',progress=3,message='Conectando con Google Drive…')
        if (job.get('origin') or '')=='HISTORICO_DRIVE':
            raw,info=drive_audio_to_bytes(jid,progress=lambda a,b:_peak_task_set(jid,progress=5+int((a/max(1,b))*68),message='Preparando waveform desde Drive…',loaded=a,total=b))
            mime=info['mime']; name=info['name']
        else:
            path=JOBS/jid/job['voice_filename']; raw=path.read_bytes(); mime='audio/wav' if path.suffix.lower()=='.wav' else 'audio/mpeg'; name=path.name
        _peak_task_set(jid,progress=78,message='Decodificando audio en backend…')
        from pydub import AudioSegment
        fmt=Path(name).suffix.lower().lstrip('.') or ('wav' if 'wav' in mime else 'mp3')
        seg=AudioSegment.from_file(BytesIO(raw),format=fmt).set_channels(1).set_frame_rate(8000)
        samples=seg.get_array_of_samples(); sample_rate=seg.frame_rate; peak_rate=500; step=max(1,int(sample_rate/peak_rate)); maxv=float(1<<(8*seg.sample_width-1))
        mn=[]; mx=[]; n=len(samples); buckets=(n+step-1)//step
        for bi,a in enumerate(range(0,n,step)):
            chunk=samples[a:min(n,a+step)]
            if chunk:
                mn.append(round(min(chunk)/maxv,4)); mx.append(round(max(chunk)/maxv,4))
            else: mn.append(0); mx.append(0)
            if bi%3000==0: _peak_task_set(jid,progress=80+int((bi/max(1,buckets))*18),message='Construyendo waveform…')
        payload={'ok':True,'rate':peak_rate,'min':mn,'max':mx,'duration':len(seg)/1000.0}
        with _PEAKS_LOCK:
            _PEAKS_CACHE[jid]=payload
            while len(_PEAKS_CACHE)>12: _PEAKS_CACHE.pop(next(iter(_PEAKS_CACHE)))
        _peaks_disk_save(jid,fingerprint,payload)
        _peak_task_set(jid,status='ready',progress=100,message='Audio online listo',loaded=len(raw),total=len(raw))
    except Exception as e:
        _peak_task_set(jid,status='error',progress=0,message='No se pudo preparar el audio online',error=str(e))

def ensure_peak_task(jid):
    jid=str(jid)
    with _PEAKS_LOCK:
        if jid in _PEAKS_CACHE: return
        t=_PEAK_TASKS.get(jid)
        if t and t.get('status') in ('queued','running'): return
        _PEAK_TASKS[jid]={'status':'queued','progress':0,'message':'En cola…','updated':time.time()}
    threading.Thread(target=_build_peaks_task,args=(jid,),daemon=True,name='peaks-'+jid).start()

@app.get('/api/jobs/<jid>/peaks-status')
def peaks_status_api(jid):
    token=request.args.get('token',''); session(token); ensure_peak_task(jid)
    with _PEAKS_LOCK: t=dict(_PEAK_TASKS.get(jid) or {})
    if jid in _PEAKS_CACHE: t.update(status='ready',progress=100,message='Audio online listo')
    return jsonify(ok=True,**t)

@app.get('/api/jobs/<jid>/peaks')
def peaks_api(jid):
    token=request.args.get('token',''); session(token); ensure_peak_task(jid)
    with _PEAKS_LOCK: data=_PEAKS_CACHE.get(jid); task=dict(_PEAK_TASKS.get(jid) or {})
    if data: return jsonify(data)
    if task.get('status')=='error': return jsonify(ok=False,error=task.get('error') or task.get('message')),502
    return jsonify(ok=False,error='Waveform todavía en preparación.',progress=task.get('progress',0)),202

@app.get('/api/jobs/<jid>/editor-data')
def editor_data(jid):
    token=request.args.get('token',''); role=session(token)
    with db() as c:
        r=jobrow(c,jid)
        if (r['origin'] or '')=='HISTORICO_DRIVE':
            r=refresh_historical_from_drive(c,r,open_job=True,actor='Valeria' if role=='CORRECTORA' else 'Augusto')
        elif r['status']==EST_P:
            c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(EST_C,now(),jid)); log(c,jid,'ABRIR MOTOR V1',EST_C); r=jobrow(c,jid)
        job_snapshot=dict(r); managed=_sheet_managed(r)
    if managed and job_snapshot.get('origin')!='HISTORICO_DRIVE':
        try: drive_bridge_call('open_job',{'id':jid,'actor':'Valeria' if role=='CORRECTORA' else 'Augusto'})
        except Exception as e: app.logger.warning('Sheet maestro al abrir %s: %s',jid,e)
    # MEJORA 1: al abrir el trabajo, reconciliar de inmediato contra Dropbox (fuera
    # de la transacción anterior para no bloquear la base con dos conexiones a la vez).
    try: dropbox_reconcile_job(jid,job=job_snapshot,force=True)
    except Exception as e: app.logger.warning('reconciliar dropbox %s (abrir editor): %s',jid,e)
    with db() as c:
        r=jobrow(c,jid)
        project=json.loads(r['project_json']) if r['project_json'] else None
        ensure_peak_task(jid)
        return jsonify(id=r['id'],artist=r['artist'],title=r['title'],lyrics=r['lyrics_corrected'] or r['lyrics_moises'],voice_name=r['voice_filename'],voice_url=f'/api/jobs/{jid}/voice',peaks_url=f'/api/jobs/{jid}/peaks',peaks_status_url=f'/api/jobs/{jid}/peaks-status',project=project,role=SESSIONS.get(token,''),duration=float(r['duration'] or 0))
@app.get('/api/jobs/<jid>/voice')
def voice(jid):
    """Sirve el audio histórico con HTTP Range real usando el puente existente."""
    try:
        token=request.args.get('token',''); session(token)
        with db() as c: r=dict(jobrow(c,jid))
        if (r.get('origin') or '')=='HISTORICO_DRIVE':
            info=drive_audio_info(jid); size=int(info.get('size') or 0); mime=info['mime']; name=info['name']
            raw,cached_info=drive_audio_bytes(jid)
            size=len(raw); mime=cached_info['mime']; name=cached_info['name']
            parsed=parse_http_byte_range(request.headers.get('Range'),size)
            if parsed is None:
                return Response(status=416,headers={
                    'Accept-Ranges':'bytes','Content-Range':'bytes */'+str(size),
                    'Content-Length':'0','Cache-Control':'private, max-age=300'
                })
            start,end,is_range=parsed
            headers={
                'Accept-Ranges':'bytes',
                'Content-Type':mime,
                'Content-Disposition':f'inline; filename="{Path(name).name}"',
                'Cache-Control':'private, max-age=300',
                'Content-Length':str(end-start+1),
            }
            if is_range: headers['Content-Range']=f'bytes {start}-{end}/{size}'
            return Response(drive_audio_iter(jid,start=start,end=end),status=206 if is_range else 200,headers=headers)
        p=JOBS/jid/r['voice_filename']; return send_file(p,conditional=True)
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: app.logger.warning('voice %s: %s',jid,e); return jsonify(ok=False,error=str(e)),502
    except Exception as e: app.logger.exception('voice %s',jid); return jsonify(ok=False,error='No se pudo cargar la voz: '+str(e)),500

@app.post('/api/jobs/<jid>/draft')
def draft(jid):
    d=request.get_json() or {}; role=session(d.get('token')); lyrics=str(d.get('lyrics',''))
    with db() as c:
        r=jobrow(c,jid)
        if _sheet_managed(r): drive_bridge_call('autosave',{'id':jid,'lyrics':lyrics,'actor':'Valeria' if role=='CORRECTORA' else 'Augusto'})
        c.execute('UPDATE jobs SET lyrics_corrected=?,updated=? WHERE id=?',(lyrics,now(),jid))
    return jsonify(ok=True)
@app.post('/api/jobs/<jid>/project')
def save_project(jid):
    d=request.get_json() or {}; session(d.get('token')); proj=d.get('project')
    if not isinstance(proj,dict): return jsonify(error='Proyecto inválido'),400
    raw=json.dumps(proj,ensure_ascii=False,indent=2).encode('utf-8')
    with db() as c:
        r=jobrow(c,jid); lyrics='\n'.join(seg.get('text','') if seg.get('kind')!='break' else '' for seg in proj.get('segments',[]))
        if _sheet_managed(r): drive_bridge_call('autosave',{'id':jid,'lyrics':lyrics,'actor':'Valeria' if SESSIONS.get(d.get('token'))=='CORRECTORA' else 'Augusto'})
        c.execute('UPDATE jobs SET project_json=?,lyrics_corrected=?,updated=? WHERE id=?',(json.dumps(proj,ensure_ascii=False),lyrics,now(),jid))
    schedule_timings_backup(jid,raw)
    return jsonify(ok=True)

def _ai_norm_repeat_token(text):
    txt=unicodedata.normalize('NFKD',str(text or '').lower())
    txt=''.join(ch for ch in txt if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+','',txt)


def _ai_repair_repeated_microtimings(words, min_run=3, tiny_seconds=0.060):
    """Repara sólo microtimings imposibles dentro de corridas repetidas.

    Contrato:
      - sólo corridas adyacentes de 3+ tokens iguales normalizados;
      - sólo actúa si hay <=60 ms o inicios prácticamente colapsados;
      - conserva el START del primer repetido y el START del último cuando cabe;
      - conserva el END del último si era válido;
      - nunca mueve la primera palabra posterior al bloque;
      - no toca ninguna palabra fuera de la repetición.

    Ejemplo: AY 106.94 / AY 107.42(10ms) / AY 107.44
    -> mantiene 106.94 y 107.44, redistribuye el AY central.
    """
    seq=list(words or [])
    repairs=[]
    if len(seq)<min_run:
        return seq,repairs

    def tok(item):
        return _ai_norm_repeat_token(
            item.get('master_text') or item.get('text') or item.get('scribe_text') or ''
        )

    i=0
    while i<len(seq):
        t=tok(seq[i])
        if not t:
            i+=1; continue
        j=i+1
        while j<len(seq) and tok(seq[j])==t:
            j+=1
        count=j-i
        if count<min_run:
            i=j; continue

        run=seq[i:j]
        parsed=[]
        valid=True
        for w in run:
            try:
                a=float(w.get('start')); b=float(w.get('end'))
            except Exception:
                valid=False; break
            if b<a:
                valid=False; break
            parsed.append((a,b))
        if not valid:
            i=j; continue

        durations=[max(0.0,b-a) for a,b in parsed]
        starts=[a for a,_ in parsed]
        collapsed=any((starts[k]-starts[k-1])<=0.030 for k in range(1,count))
        tiny=any(d<=tiny_seconds for d in durations)
        if not (tiny or collapsed):
            i=j; continue

        first_start=starts[0]
        last_start=starts[-1]
        last_end=parsed[-1][1]
        onset_span=last_start-first_start

        # Preferimos bloquear los dos onsets exteriores. Si ElevenLabs también
        # colapsó toda la corrida, usamos el END exterior como segundo ancla.
        min_step=0.090
        if onset_span>=min_step*(count-1):
            step=onset_span/(count-1)
            new_starts=[first_start+step*k for k in range(count)]
            method='repeat_locked_first_last_start'
        else:
            usable_end=last_end
            if j<len(seq):
                try:
                    next_start=float(seq[j].get('start'))
                    if next_start>first_start+.10:
                        usable_end=min(usable_end,next_start-.020) if usable_end>first_start else next_start-.020
                except Exception:
                    pass
            span=usable_end-first_start
            if span<min_step*count:
                i=j; continue
            step=span/count
            new_starts=[first_start+step*k for k in range(count)]
            method='repeat_locked_outer_bounds'

        original=[]
        for k,w in enumerate(run):
            oa,ob=parsed[k]
            original.append({
                'index':i+k,
                'text':str(w.get('text') or w.get('master_text') or ''),
                'start':round(oa,6),'end':round(ob,6),
            })

        new_times=[]
        for k,w in enumerate(run):
            ns=new_starts[k]
            if k<count-1:
                cap=new_starts[k+1]-.010
                local_step=max(.001,new_starts[k+1]-ns)
                desired_min=min(.20,max(.10,local_step*.70))
                ne=max(parsed[k][1],ns+desired_min)
                ne=min(cap,ne)
            else:
                ne=last_end
                if ne<=ns+.060:
                    ext_cap=None
                    if j<len(seq):
                        try:
                            ext=float(seq[j].get('start'))
                            if ext>ns+.08: ext_cap=ext-.020
                        except Exception:
                            pass
                    target=ns+min(.20,max(.10,step*.70))
                    ne=min(ext_cap,target) if ext_cap is not None else target
            if ne<=ns+.050:
                # Si ni siquiera caben 50 ms, preferimos no tocar esta corrida.
                new_times=[]; break
            new_times.append((ns,ne))

        if not new_times:
            i=j; continue

        for k,w in enumerate(run):
            oa,ob=parsed[k]; ns,ne=new_times[k]
            w['timing_original_start']=round(oa,6)
            w['timing_original_end']=round(ob,6)
            w['start']=round(ns,6)
            w['end']=round(ne,6)
            w['timing_repaired']=True
            w['timing_repair']='repeat_microtiming_v1'
            w['timing_repair_token']=t

        repairs.append({
            'version':'REPEAT_MICROTIMING_V1',
            'token':t,
            'count':count,
            'start_index':i,
            'end_index':j-1,
            'method':method,
            'trigger':'tiny_or_collapsed',
            'original':original,
            'repaired':[
                {'index':i+k,'start':round(a,6),'end':round(b,6)}
                for k,(a,b) in enumerate(new_times)
            ],
        })
        i=j

    return seq,repairs


def _ai_repeat_profile(clean):
    toks=[_ai_norm_repeat_token(x.get('text')) for x in clean]
    toks=[x for x in toks if x]
    n=len(toks)
    if not n:
        return {'repetitive':False,'unique_ratio':1.0,'dominant_ratio':0.0,'adjacent_ratio':0.0,'max_run':0}
    counts={}
    for t in toks: counts[t]=counts.get(t,0)+1
    unique_ratio=len(counts)/max(1,n)
    dominant_ratio=max(counts.values())/max(1,n)
    adjacent=sum(1 for i in range(1,n) if toks[i]==toks[i-1])/max(1,n-1)
    max_run=1; run=1
    for i in range(1,n):
        if toks[i]==toks[i-1]:
            run+=1; max_run=max(max_run,run)
        else:
            run=1
    # DJGABO_REPEAT_MICROTIMING_V1
    # La protección antigua cubría bloques repetitivos largos (Amor Rebelde).
    # Ahora 3 sílabas iguales seguidas también cuentan como repetición.
    repetitive=(max_run>=3) or (n>=8 and (unique_ratio<=0.45 or dominant_ratio>=0.34 or adjacent>=0.28))
    return {
        'repetitive':bool(repetitive),
        'unique_ratio':round(unique_ratio,4),
        'dominant_ratio':round(dominant_ratio,4),
        'adjacent_ratio':round(adjacent,4),
        'max_run':int(max_run),
    }

def _ai_alignment_quality(aligned, strict_repeat=False):
    n=len(aligned or [])
    if not n:
        return {'ok':False,'reasons':['empty'],'tiny':0,'huge':0,'collapsed':0,'median_duration':0.0}
    vals=[]; starts=[]; backward=0
    for aw in aligned:
        try:
            a=float(aw.get('start')); b=float(aw.get('end'))
        except Exception:
            return {'ok':False,'reasons':['invalid_time'],'tiny':0,'huge':0,'collapsed':0,'median_duration':0.0}
        if starts and a+0.005<starts[-1]: backward+=1
        starts.append(a); vals.append(max(0.0,b-a))
    pos=sorted(v for v in vals if v>0)
    med=pos[len(pos)//2] if pos else 0.0
    tiny=sum(1 for v in vals if v<=0.025)
    # En bloques repetitivos una sílaba de 30–50 ms suele ser señal de que
    # Forced Alignment comprimió una repetición. En modo normal no tocamos
    # este umbral para no perjudicar palabras cortas reales.
    repeat_short=sum(1 for v in vals if v<=0.060) if strict_repeat else 0
    huge_limit=max(2.0,med*6.0)
    huge=sum(1 for v in vals if v>=huge_limit)
    collapsed=1; run=1
    for i in range(1,n):
        if abs(starts[i]-starts[i-1])<=0.015:
            run+=1; collapsed=max(collapsed,run)
        else:
            run=1
    reasons=[]
    if backward: reasons.append('backward')
    if tiny>=max(3,int(math.ceil(n*.10))): reasons.append('tiny_cluster')
    if strict_repeat and repeat_short>=1: reasons.append('repeat_short_word')
    if huge>=max(2,int(math.ceil(n*.08))): reasons.append('stretched_words')
    if collapsed>=max(3,int(math.ceil(n*.10))): reasons.append('same_timestamp_cluster')
    return {
        'ok':not reasons,
        'reasons':reasons,
        'tiny':tiny,
        'repeat_short':repeat_short,
        'huge':huge,
        'collapsed':collapsed,
        'median_duration':round(med,4),
        'huge_limit':round(huge_limit,4),
    }


def _ai_voice_active_bounds(wav_path):
    """Devuelve el tramo vocal útil dentro del clip, ignorando padding silencioso."""
    try:
        with wave.open(str(wav_path),'rb') as wf:
            sr=wf.getframerate(); nch=wf.getnchannels(); sw=wf.getsampwidth()
            raw=wf.readframes(wf.getnframes())
        if sw!=2:
            return (0.0,0.0)
        pcm=array('h'); pcm.frombytes(raw)
        if sys.byteorder!='little': pcm.byteswap()
        if nch>1:
            mono=array('h',(pcm[i] for i in range(0,len(pcm),nch)))
        else:
            mono=pcm
        if not mono or sr<=0: return (0.0,0.0)
        frame=max(1,int(sr*.020))
        db=[]
        for i in range(0,len(mono)-frame+1,frame):
            fr=mono[i:i+frame]
            rms=math.sqrt(sum(float(x)*float(x) for x in fr)/len(fr))/32768.0
            db.append(20.0*math.log10(max(rms,1e-7)))
        if not db: return (0.0,len(mono)/sr)
        sd=sorted(db)
        p35=sd[int((len(sd)-1)*.35)]
        p80=sd[int((len(sd)-1)*.80)]
        thr=max(-48.0,min(-25.0,p35+max(4.0,min(9.0,(p80-p35)*.22))))
        # Suavizado corto; 3 de 5 frames sobre umbral.
        active=[]
        for i in range(len(db)):
            lo=max(0,i-2); hi=min(len(db),i+3)
            votes=sum(1 for x in db[lo:hi] if x>=thr)
            if votes>=3: active.append(i)
        if not active: return (0.0,len(mono)/sr)
        first=active[0]*.020
        last=(active[-1]+1)*.020
        total=len(mono)/sr
        # El frontend suele añadir ~1 s de margen. Evitamos comernos una voz vecina.
        first=max(0.0,min(first,total))
        last=max(first+.10,min(last,total))
        return (round(first,4),round(last,4))
    except Exception as e:
        app.logger.warning('active bounds failed: %s',e)
        return (0.0,0.0)


def _ai_forced_align_worker(audio_path,text):
    with Path(audio_path).open('rb') as fh:
        rr=requests.post(
            'http://127.0.0.1:8097/api/elevenlabs/forced-align',
            files={'audio':(Path(audio_path).name,fh,'audio/wav')},
            data={'text':text},
            timeout=(30,1200)
        )
    if not rr.ok:
        try: detail=rr.json().get('detail') or rr.text[:800]
        except Exception: detail=rr.text[:800]
        raise ValueError('ElevenLabs no pudo alinear el bloque: '+str(detail))
    return rr.json()


def _ai_align_repeat_chunks(clip, clean, td, active_start, active_end, max_words=8):
    """Fallback repetitivo: subdivide solo y conserva lo confiable; lo dudoso queda para revisión."""
    n=len(clean)
    if n<2: raise ValueError('Bloque repetitivo demasiado corto.')
    if active_end<=active_start+.25:
        active_start=0.0
        try:
            with wave.open(str(clip),'rb') as wf: active_end=wf.getnframes()/wf.getframerate()
        except Exception: active_end=0.0
    if active_end<=active_start+.25:
        raise ValueError('No pude detectar el tramo vocal del bloque repetitivo.')

    results=[]; reviews=[]; calls=0
    ranges=[]
    for lo in range(0,n,max_words):
        hi=min(n,lo+max_words)
        core_a=active_start+(active_end-active_start)*(lo/n)
        core_b=active_start+(active_end-active_start)*(hi/n)
        ranges.append((lo,hi,core_a,core_b))

    def mark_review(lo,hi,core_a,core_b,reason,detail=''):
        reviews.append({
            'start_idx':lo,'end_idx':hi-1,
            'ids':[clean[i]['id'] for i in range(lo,hi)],
            'texts':[clean[i]['text'] for i in range(lo,hi)],
            'reason':str(reason or 'ambiguous'),
            'detail':str(detail or '')[:300],
            'core_start':round(core_a,4),'core_end':round(core_b,4),
        })
        return []

    def split_or_review(lo,hi,core_a,core_b,depth,reason,detail=''):
        count=hi-lo
        if count>2 and depth<4:
            mid=lo+count//2
            mid_t=core_a+(core_b-core_a)*((mid-lo)/count)
            return run_piece(lo,mid,core_a,mid_t,depth+1)+run_piece(mid,hi,mid_t,core_b,depth+1)
        return mark_review(lo,hi,core_a,core_b,reason,detail)

    def run_piece(lo,hi,core_a,core_b,depth=0):
        nonlocal calls
        count=hi-lo
        span=max(.35,core_b-core_a)
        pad=min(.55,max(.20,span*.18))
        sub_a=max(0.0,core_a-pad)
        sub_b=min(active_end+.60,core_b+pad)
        part=Path(td)/f'repeat_{lo}_{hi}_{depth}.wav'
        try:
            proc=subprocess.run([
                'ffmpeg','-hide_banner','-loglevel','error','-ss',f'{sub_a:.6f}','-to',f'{sub_b:.6f}',
                '-i',str(clip),'-vn','-ac','1','-ar','44100','-c:a','pcm_s16le',str(part)
            ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=180)
            if proc.returncode!=0 or not part.is_file() or part.stat().st_size<500:
                return split_or_review(lo,hi,core_a,core_b,depth,'audio_prepare',(proc.stderr or '')[-250:])
            payload=_ai_forced_align_worker(part,' '.join(clean[i]['text'] for i in range(lo,hi)))
            calls+=1
            aligned=payload.get('words') or []
        except Exception as e:
            return split_or_review(lo,hi,core_a,core_b,depth,'align_error',str(e))

        if len(aligned)!=count:
            return split_or_review(lo,hi,core_a,core_b,depth,'word_count',f'IA={len(aligned)} esperado={count}')

        quality=_ai_alignment_quality(aligned, strict_repeat=True)
        if not quality['ok']:
            return split_or_review(lo,hi,core_a,core_b,depth,'quality',','.join(quality.get('reasons') or []))

        piece=[]
        for off,(srcw,aw) in enumerate(zip(clean[lo:hi],aligned)):
            a=float(aw.get('start'))+sub_a
            b=float(aw.get('end'))+sub_a
            if b<=a: b=a+.03
            a=max(active_start-.05,min(a,active_end+.05))
            b=max(a+.03,min(b,active_end+.08))
            piece.append({
                'idx':lo+off,'id':srcw['id'],'text':srcw['text'],
                'start':a,'end':b,'loss':aw.get('loss')
            })
        return piece

    for lo,hi,ca,cb in ranges:
        results.extend(run_piece(lo,hi,ca,cb,0))

    results.sort(key=lambda x:x['idx'])
    prev_end=None
    for item in results:
        if prev_end is not None and item['start']<prev_end-.08:
            item['start']=prev_end
            if item['end']<=item['start']: item['end']=item['start']+.03
        prev_end=max(item['start'],item['end'])

    quality=_ai_alignment_quality(results) if results else {
        'ok':False,'reasons':['no_confident_chunks'],'tiny':0,'huge':0,'collapsed':0,'median_duration':0.0
    }
    quality=dict(quality)
    quality['partial']=bool(reviews)
    quality['accepted_words']=len(results)
    quality['review_words']=sum(len(x.get('ids') or []) for x in reviews)
    quality['review_chunks']=len(reviews)
    if results:
        quality['ok']=True
        quality['reasons']=[] if not reviews else ['partial_review']
    return results,quality,calls,len(ranges),reviews


@app.post('/api/jobs/<jid>/ai-align-block')
def ai_align_block(jid):
    d=request.get_json() or {}
    try:
        # R10: disponible para cualquier sesión válida del editor, incluida CORRECTORA.
        session(d.get('token'))
        words=d.get('words') or []
        if not isinstance(words,list) or not words:
            raise ValueError('Selecciona primero las palabras del bloque.')
        clean=[]
        for item in words:
            if not isinstance(item,dict): continue
            wid=str(item.get('id') or '').strip(); txt=str(item.get('text') or '').strip()
            if wid and txt: clean.append({'id':wid,'text':txt})
        if not clean: raise ValueError('La selección no contiene texto utilizable.')
        clip_start=max(0.0,float(d.get('clip_start') or 0))
        clip_end=float(d.get('clip_end') or 0)
        if clip_end<=clip_start+.25:
            raise ValueError('No pude determinar el tramo de audio del bloque.')
        if clip_end-clip_start>180:
            raise ValueError('El bloque es demasiado largo. Selecciona un tramo menor de 3 minutos.')
        with db() as c: job=dict(jobrow(c,jid))
        if (job.get('origin') or '')=='HISTORICO_DRIVE':
            raise ValueError('Esta función IA de bloque está habilitada por ahora para trabajos nuevos del clon.')
        source=JOBS/jid/job['voice_filename']
        if not source.is_file(): raise ValueError('No encuentro la pista de voz del trabajo.')

        profile=_ai_repeat_profile(clean)
        with tempfile.TemporaryDirectory(prefix='karaoke_block_ai_') as td0:
            td=Path(td0); clip=td/'block.wav'
            dur=clip_end-clip_start
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-i',str(source),
                 '-ss',f'{clip_start:.6f}','-t',f'{dur:.6f}',
                 '-vn','-ac','1','-ar','44100','-c:a','pcm_s16le',str(clip)]
            proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=180)
            if proc.returncode!=0 or not clip.is_file() or clip.stat().st_size<1000:
                raise ValueError('No pude preparar el fragmento de audio: '+(proc.stderr or '')[-500:])

            text_block=' '.join(x['text'] for x in clean)
            payload=_ai_forced_align_worker(clip,text_block)
            aligned=payload.get('words') or []
            if len(aligned)!=len(clean):
                raise ValueError(
                    f'ElevenLabs devolvió {len(aligned)} palabras para una selección de {len(clean)}. '
                    'No aplicaré timings ambiguos; ajusta el texto del bloque y vuelve a intentar.'
                )

            quality=_ai_alignment_quality(aligned, strict_repeat=profile['repetitive'])
            strategy='full'
            engine='elevenlabs-forced-alignment'
            calls=1; chunks=1
            refined=[]; reviews=[]; review_chunks=[]
            if profile['repetitive'] and not quality['ok']:
                active_a,active_b=_ai_voice_active_bounds(clip)
                refined,refined_quality,extra_calls,chunks,reviews=_ai_align_repeat_chunks(
                    clip,clean,td,active_a,active_b,max_words=8
                )
                calls+=extra_calls
                quality=refined_quality
                strategy='repeat_chunks_partial' if reviews else 'repeat_chunks'
                engine='elevenlabs-forced-alignment+repeat-chunks'

            updates=[]
            review_chunks=[]
            if strategy.startswith('repeat_chunks'):
                for item in refined:
                    a=float(item.get('start'))+clip_start
                    b=float(item.get('end'))+clip_start
                    if b<=a: b=a+.03
                    updates.append({
                        'id':item['id'],'text':item['text'],
                        'start':round(a,6),'end':round(b,6),'loss':item.get('loss')
                    })
                for rv in reviews:
                    rr=dict(rv)
                    rr['clip_start']=round(clip_start+float(rv.get('core_start') or 0),6)
                    rr['clip_end']=round(clip_start+float(rv.get('core_end') or 0),6)
                    review_chunks.append(rr)
            else:
                for srcw,aw in zip(clean,aligned):
                    a=float(aw.get('start'))+clip_start
                    b=float(aw.get('end'))+clip_start
                    if b<=a: b=a+.03
                    updates.append({'id':srcw['id'],'text':srcw['text'],'start':round(a,6),'end':round(b,6),'loss':aw.get('loss')})

            with db() as c:
                log(c,jid,'ELEVENLABS · ALINEAR BLOQUE',
                    f"{len(updates)} palabras · {clip_start:.2f}-{clip_end:.2f} · {strategy} · calls={calls}")
            return jsonify(
                ok=True,engine=engine,strategy=strategy,updates=updates,
                clip_start=clip_start,clip_end=clip_end,loss=payload.get('loss'),
                elapsed_s=payload.get('elapsed_s'),quality=quality,
                repetition=profile,calls=calls,chunks=chunks,
                review_chunks=review_chunks,
                review_word_ids=[wid for rv in review_chunks for wid in (rv.get('ids') or [])],
                applied_words=len(updates),
                requested_words=len(clean)
            )
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('ai-align-block %s',jid)
        return jsonify(ok=False,error='No se pudo alinear el bloque: '+str(e)),500


def _render_set(task_id, **kw):
    jid=''
    with _RENDER_LOCK:
        t=_RENDER_TASKS.setdefault(task_id,{'status':'queued','progress':0,'message':'En cola…'})
        t.update(kw); t['updated']=time.time(); jid=str(t.get('job_id') or '')
        progress=int(t.get('progress') or 0); status=str(t.get('status') or '')
    if jid and status in ('queued','running'):
        try:
            with db() as c: c.execute('UPDATE jobs SET render_status=?,render_progress=?,updated=? WHERE id=?',('RENDERIZANDO',progress,now(),jid))
        except Exception: pass

def _next_correctora_job(current):
    with db() as c:
        r=c.execute('SELECT id FROM jobs WHERE deleted=0 AND id<? AND status<>? ORDER BY id DESC LIMIT 1',(current,EST_TERM)).fetchone()
        if not r: r=c.execute('SELECT id FROM jobs WHERE deleted=0 AND id<>? AND status<>? ORDER BY id DESC LIMIT 1',(current,EST_TERM)).fetchone()
        return r['id'] if r else ''

def resolve_render_font(family):
    family=str(family or 'impact').lower()
    explicit={
        'impact':os.getenv('DJGABO_FONT_IMPACT'),
        'arialbd':os.getenv('DJGABO_FONT_ARIAL_BOLD'),
        'arial':os.getenv('DJGABO_FONT_ARIAL'),
    }.get(family)
    candidates=[]
    if explicit: candidates.append(Path(explicit).expanduser())
    font_dir=str(os.getenv('DJGABO_FONT_DIR') or '').strip()
    if font_dir:
        base=Path(font_dir).expanduser()
        candidates.extend([base/('impact.ttf' if family=='impact' else ('arialbd.ttf' if family=='arialbd' else 'arial.ttf'))])
    if family=='impact':
        candidates.extend([Path(r'C:/Windows/Fonts/impact.ttf'),Path('/usr/local/share/fonts/impact.ttf'),Path('/usr/share/fonts/truetype/msttcorefonts/Impact.ttf')])
    elif family=='arialbd':
        candidates.extend([Path(r'C:/Windows/Fonts/arialbd.ttf'),Path('/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf'),Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf')])
    else:
        candidates.extend([Path(r'C:/Windows/Fonts/arial.ttf'),Path('/usr/share/fonts/truetype/msttcorefonts/Arial.ttf'),Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf')])
    candidates.extend([Path('/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')])
    for path in candidates:
        if path.is_file():
            if family=='impact' and 'impact' not in path.name.lower():
                app.logger.warning('Impact no está instalado; se usará la fuente alternativa %s.',path)
            return str(path)
    raise ValueError('No existe una fuente TTF utilizable. Configura DJGABO_FONT_IMPACT/DJGABO_FONT_DIR en el servidor.')

def _render_worker(task_id,jid,token,timings_bytes,opts):
    try:
        role=session(token); _render_set(task_id,status='running',progress=2,message='Preparando proyecto…')
        with db() as c:
            job=dict(jobrow(c,jid)); c.execute('UPDATE jobs SET render_status=?,render_progress=?,render_error=?,updated=? WHERE id=?',('RENDERIZANDO',2,'',now(),jid))
        with tempfile.TemporaryDirectory(prefix='karaoke_cdg_online_') as td0:
            td=Path(td0); timings=td/'proyecto.timings.json'; timings.write_bytes(timings_bytes)
            if (job.get('origin') or '')=='HISTORICO_DRIVE':
                info=drive_audio_info(jid); suffix=Path(info['name']).suffix or '.mp3'; audio=td/f'audio_helper{suffix}'
                total=info['size']; loaded=0
                with audio.open('wb') as f:
                    for part in drive_audio_iter(jid):
                        f.write(part); loaded+=len(part); pct=4+int((loaded/max(1,total))*22)
                        _render_set(task_id,progress=pct,message='Preparando audio online…',loaded=loaded,total=total)
            else:
                source=JOBS/jid/job['voice_filename']; audio=td/f'audio_helper{source.suffix or ".mp3"}'; shutil.copy2(source,audio)
            out=td/'salida'; style=json.loads((ROOT/'renderer'/'style.json').read_text(encoding='utf-8'))
            allowed={'intro_mode','instrumental_label','lines_per_page','intro_duration_seconds','intro_short_duration_seconds','font_family','font_size','stroke_width','lyric_y_offset','outro_line1','outro_line2','outro_size','outro_x','outro_y','outro_transition'}
            for k,v in (opts or {}).items():
                if k in allowed: style[k]=v
            fam=str(style.get('font_family','impact')); style['font']=resolve_render_font(fam)
            style['intro_mode']='auto'; style['intro_duration_seconds']=6.0; style['intro_short_duration_seconds']=3.0
            st=td/'style.json'; st.write_text(json.dumps(style,ensure_ascii=False,indent=2),encoding='utf-8')
            _render_set(task_id,progress=28,message='Esperando turno de render…')
            cmd=[sys.executable,str(RENDER),str(timings),str(audio),'-o',str(out),'-s',str(st)]
            logs=[]; timed_out=threading.Event()
            with _RENDER_SLOTS:
                _render_set(task_id,progress=28,message='Iniciando renderer CDG…')
                proc=subprocess.Popen(cmd,cwd=str(ROOT/'renderer'),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
                def stop_stuck_render():
                    if proc.poll() is None:
                        timed_out.set()
                        try: proc.kill()
                        except Exception: pass
                watchdog=threading.Timer(RENDER_TIMEOUT_SECONDS,stop_stuck_render); watchdog.daemon=True; watchdog.start()
                try:
                    for line in proc.stdout or []:
                        logs.append(line.rstrip()); mm=re.search(r'DJGABO_PROGRESS:(\d+):(.*)',line)
                        if mm:
                            rp=max(0,min(100,int(mm.group(1)))); _render_set(task_id,progress=28+int(rp*.62),message=mm.group(2).strip() or 'Generando CDG…')
                    rc=proc.wait(timeout=15)
                finally:
                    watchdog.cancel()
            if timed_out.is_set(): raise ValueError('El render superó el límite de '+str(RENDER_TIMEOUT_SECONDS//60)+' minutos y fue detenido para proteger el servidor.')
            if rc!=0: raise ValueError('Renderer CDG: '+('\n'.join(logs[-25:]) or 'error desconocido')[-1800:])
            cdgs=sorted(out.glob('*.cdg'))
            if not cdgs:
                for rz in sorted(out.glob('*.zip')):
                    try:
                        with zipfile.ZipFile(rz) as zf:
                            names=[n for n in zf.namelist() if n.lower().endswith('.cdg')]
                            if names:
                                data=zf.read(names[0]); break
                    except zipfile.BadZipFile: pass
                else: data=b''
            else: data=cdgs[0].read_bytes()
            if not data: raise ValueError('El renderer terminó pero no produjo el archivo .CDG.')
            master_stem=_provisional_master_stem(job); cdg_name=safe_name(master_stem)+'.cdg'; _cdg_cache_put(jid,data,cdg_name)
            with db() as c:
                current=jobrow(c,jid); next_status=EST_OK if _job_lyrics_ready(current) else current['status']
                c.execute("UPDATE jobs SET status=?,cdg_local_filename=?,canonical_name=CASE WHEN canonical_name='' THEN ? ELSE canonical_name END,render_status=?,render_progress=?,render_error=?,updated=? WHERE id=?",(next_status,cdg_name,master_stem,'CDG_LISTO',94,'',now(),jid)); log(c,jid,'CREAR CDG ONLINE','CDG_LISTO')
            _render_set(task_id,progress=94,message='CDG generado · publicando online…')
            try: pub=publish_job_to_dropbox(jid)
            except Exception as e: pub={'status':'PENDIENTE_DROPBOX','error':str(e),'uploaded_cdg':False,'uploaded_wav':False,'folder':''}
            with db() as c: c.execute('UPDATE jobs SET render_status=?,render_progress=?,render_error=?,updated=? WHERE id=?',('EXPORTADO',100,'',now(),jid))
            next_id=_next_correctora_job(jid) if role=='CORRECTORA' else ''
            _render_set(task_id,status='done',progress=100,message='Exportado con éxito',job_id=jid,cdg_name=cdg_name,dropbox_status=pub.get('status',''),dropbox_folder=pub.get('folder',''),next_job_id=next_id)
    except Exception as e:
        with db() as c:
            try: c.execute('UPDATE jobs SET render_status=?,render_error=?,updated=? WHERE id=?',('ERROR',str(e)[:1000],now(),jid))
            except Exception: pass
        _render_set(task_id,status='error',message='Error al exportar · Reintentar',error=str(e))

@app.post('/api/render/start')
def render_start_api():
    if 'timings' not in request.files: return jsonify(ok=False,error='Falta el proyecto de timings.'),400
    jid=str(request.form.get('job_id','')).strip(); token=str(request.form.get('session_token',''))
    if not jid: return jsonify(ok=False,error='El modo ONLINE requiere un trabajo del panel.'),400
    try: session(token)
    except Exception as e: return jsonify(ok=False,error=str(e)),401
    timings_bytes=request.files['timings'].read()
    if not timings_bytes: return jsonify(ok=False,error='El proyecto de timings llegó vacío.'),400
    try:
        local_path=_timings_local_path(jid); local_path.parent.mkdir(parents=True,exist_ok=True)
        tmp=local_path.with_suffix('.tmp'); tmp.write_bytes(timings_bytes); tmp.replace(local_path)
    except Exception as e: return jsonify(ok=False,error='No se pudo conservar el JSON de timings en OVH: '+str(e)),500
    schedule_timings_backup(jid,timings_bytes)
    try: opts=json.loads(request.form.get('render_options','{}'))
    except Exception: opts={}
    task_id=secrets.token_urlsafe(18); _render_set(task_id,status='queued',progress=1,message='Enviando render al backend…',job_id=jid)
    threading.Thread(target=_render_worker,args=(task_id,jid,token,timings_bytes,opts),daemon=True,name='render-'+jid).start()
    return jsonify(ok=True,task_id=task_id,status_url='/api/render/status/'+task_id),202

@app.post('/api/jobs/<jid>/drive/retry')
def retry_drive_backups(jid):
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    results={}
    try:
        with db() as c: job=dict(jobrow(c,jid))
        if (job.get('origin') or '')!='HISTORICO_DRIVE' and job.get('voice_drive_status')!='OK':
            results['voice']=backup_voice_to_drive(jid)
        path=_timings_local_path(jid)
        if path.is_file(): results['timings']=backup_timings_to_drive(jid,path.read_bytes())
        elif job.get('project_json'): results['timings']=backup_timings_to_drive(jid,json.dumps(json.loads(job['project_json']),ensure_ascii=False,indent=2).encode('utf-8'))
        results['sheet']=master_sync(jid,'Reintento manual de respaldos y registro maestro')
        voice_ok=bool(results.get('voice') or job.get('voice_drive_status')=='OK' or job.get('origin')=='HISTORICO_DRIVE')
        return jsonify(ok=True,voice='OK' if voice_ok else 'PENDIENTE',timings='OK' if results.get('timings') or job.get('timings_drive_status')=='OK' else 'SIN_PROYECTO',sheet='OK')
    except Exception as e: return jsonify(ok=False,error=str(e)),502

def reconcile_sheet_master(limit=500):
    """Repara el espejo completo sin duplicar filas ni archivos del Sheet."""
    with db() as c: rows=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY id LIMIT ?',(int(limit),)).fetchall()]
    results=[]
    for job in rows:
        jid=job['id']
        try:
            if (job.get('origin') or '')!='HISTORICO_DRIVE':
                master_reserve(jid,job.get('artist'),job.get('title'),
                               job.get('voice_original_filename') or job.get('voice_filename'),
                               job.get('lyrics_moises'),job.get('size_bytes'),job.get('duration'))
                with db() as c: c.execute("UPDATE jobs SET sheet_master_status='RESERVADO',sheet_master_error='' WHERE id=?",(jid,))
                if job.get('voice_drive_id'): master_file(jid,job['voice_drive_id'],'ACAPELLA',job.get('version') or 1)
                if job.get('timings_drive_id'): master_file(jid,job['timings_drive_id'],'TIMINGS_JSON',job.get('version') or 1)
                master_sync(jid,'Reconciliación segura OVH ↔ Sheet maestro')
            results.append({'id':jid,'ok':True})
        except Exception as e:
            with db() as c: c.execute("UPDATE jobs SET sheet_master_status='ERROR',sheet_master_error=? WHERE id=?",(str(e)[:1000],jid))
            results.append({'id':jid,'ok':False,'error':str(e)})
    return results

@app.post('/api/master-sheet/reconcile')
def reconcile_sheet_master_api():
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    results=reconcile_sheet_master(int(d.get('limit') or 500))
    return jsonify(ok=all(x['ok'] for x in results),results=results,
                   correctos=sum(bool(x['ok']) for x in results),errores=sum(not x['ok'] for x in results))

@app.get('/api/render/status/<task_id>')
def render_status_api(task_id):
    token=request.args.get('token',''); session(token)
    with _RENDER_LOCK: t=dict(_RENDER_TASKS.get(task_id) or {})
    if not t: return jsonify(ok=False,error='Render no encontrado.'),404
    return jsonify(ok=True,**t)

@app.post('/api/render')
def render_cdg_compat():
    return render_start_api()

@app.post('/api/jobs/<jid>/dropbox/retry-cdg')
def retry_cdg_dropbox(jid):
    d=request.get_json(silent=True) or {}; session(d.get('token'),'ADMIN')
    try:
        pub=publish_job_to_dropbox(jid)
        return jsonify(ok=True,dropbox_folder=pub.get('folder',''),dropbox_status=pub.get('status',''),uploaded_cdg=pub.get('uploaded_cdg'),uploaded_wav=pub.get('uploaded_wav'))
    except Exception as e: return jsonify(ok=False,error=str(e)),502

# PROD_FINAL_CDG_PREVIEW_V1
# Rutas del preview del archivo CDG REAL + Voz + WAV, registradas aparte para
# no mezclar el motor de reproduccion con el backend historico.
from cdg_preview_routes import register_cdg_preview_routes
register_cdg_preview_routes(app, globals())

if __name__=='__main__':
    print('\nCONTROL CDG DJGABO LOCAL 16.14 ONLINE: http://127.0.0.1:8765\n')
    print('LOCAL 16.14: carga única en backend + waveform cache + HTTP Range en memoria.\n')
    app.run(host='127.0.0.1',port=8765,debug=False,threaded=True)
