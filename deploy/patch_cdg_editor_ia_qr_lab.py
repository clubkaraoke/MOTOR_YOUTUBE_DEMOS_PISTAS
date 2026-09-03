#!/usr/bin/env python3
from pathlib import Path
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
PANEL=ROOT/'panel.html'
QR_TEST=ROOT/'qr_test.html'
VENDOR=ROOT/'vendor'/'jsQR.js'

for p in (SERVER,PANEL):
    if not p.is_file():
        raise SystemExit('MISSING '+str(p))

if not Path('/tmp/jsQR.js').is_file():
    raise SystemExit('MISSING /tmp/jsQR.js')
if not Path('/tmp/qr_test.html').is_file():
    raise SystemExit('MISSING /tmp/qr_test.html')

VENDOR.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2('/tmp/jsQR.js',VENDOR)
shutil.copy2('/tmp/qr_test.html',QR_TEST)

panel=PANEL.read_text(encoding='utf-8')
preload='<script src="/cdg-editor-ia/vendor/jsQR.js"></script>'
if preload not in panel:
    pos=panel.find('<script>')
    if pos<0:
        raise SystemExit('NO MAIN SCRIPT TAG')
    panel=panel[:pos]+preload+'\n'+panel[pos:]

old_sources="""  const fuentes=[
    'https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js',
    'https://unpkg.com/jsqr@1.4.0/dist/jsQR.js'
  ];"""
new_sources="""  const fuentes=[
    '/cdg-editor-ia/vendor/jsQR.js',
    'https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js'
  ];"""
if old_sources in panel:
    panel=panel.replace(old_sources,new_sources,1)
PANEL.write_text(panel,encoding='utf-8')

server=SERVER.read_text(encoding='utf-8')
# UVR QR URLs use changing subdomains, e.g. a111p.uvronline.app.
server=server.replace(
    "if parsed.scheme!='https' or host not in ('uvronline.app','www.uvronline.app'):",
    "if parsed.scheme!='https' or not (host=='uvronline.app' or host.endswith('.uvronline.app')):"
)

if "@app.get('/qr-test')" not in server:
    anchor="@app.get('/vendor/jsQR.js')\ndef vendor_jsqr():"
    if anchor not in server:
        raise SystemExit('NO VENDOR ROUTE ANCHOR')
    lab="""@app.get('/qr-test')
def qr_test_page():
    if not TEST_MODE:
        abort(404)
    p=Path(__file__).resolve().parent/'qr_test.html'
    if not p.is_file():
        abort(404)
    return send_file(str(p),mimetype='text/html')

@app.post('/api/qr-lab/check')
def qr_lab_check():
    if not TEST_MODE:
        return jsonify(ok=False,error='Sólo IA TEST.'),403
    d=request.get_json(silent=True) or {}
    source_url=str(d.get('url') or '').strip()
    try:
        parsed=urlparse(source_url)
        host=(parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme!='https' or not (host=='uvronline.app' or host.endswith('.uvronline.app')):
            raise ValueError('La URL no pertenece a UVR Online.')
        r=requests.get(source_url,stream=True,allow_redirects=True,
                       headers={'User-Agent':'DJGABO-QR-LAB/1.0'},timeout=(20,90))
        if r.status_code>=400:
            code=r.status_code
            r.close()
            raise ValueError('UVR respondió HTTP '+str(code))
        first=b''
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                first=chunk
                break
        final_url=str(r.url or source_url)
        ctype=str(r.headers.get('Content-Type') or '')
        clen=str(r.headers.get('Content-Length') or '')
        r.close()
        if not first:
            raise ValueError('UVR respondió sin datos de audio.')
        return jsonify(ok=True,status=200,content_type=ctype,content_length=clen,
                       bytes_received=len(first),final_url=final_url)
    except ValueError as e:
        return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('QR LAB')
        return jsonify(ok=False,error='Fallo en prueba OVH: '+str(e)),500

"""
    server=server.replace(anchor,lab+anchor,1)


# UVR puede rechazar clientes automatizados por User-Agent. Usamos cabeceras de navegador real.
server=server.replace(
    "headers={'User-Agent':'DJGABO-CDG-IA-TEST/1.0'},",
    "headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36','Accept':'audio/mpeg,audio/*;q=0.9,*/*;q=0.8','Accept-Language':'es-PE,es;q=0.9,en;q=0.8','Referer':'https://nextgen.uvronline.app/'},"
)
server=server.replace(
    "headers={'User-Agent':'DJGABO-QR-LAB/1.0'},timeout=(20,90))",
    "headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36','Accept':'audio/mpeg,audio/*;q=0.9,*/*;q=0.8','Accept-Language':'es-PE,es;q=0.9,en;q=0.8','Referer':'https://nextgen.uvronline.app/'},timeout=(20,90))"
)

SERVER.write_text(server,encoding='utf-8')
print('QR_LAB_PATCH=OK')
print('PRELOAD=',preload in PANEL.read_text(encoding='utf-8'))
print('SUBDOMAIN_OK=',"host.endswith('.uvronline.app')" in SERVER.read_text(encoding='utf-8'))
print('LAB_ROUTE=',"@app.get('/qr-test')" in SERVER.read_text(encoding='utf-8'))
