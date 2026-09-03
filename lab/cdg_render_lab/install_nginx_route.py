from __future__ import annotations
import sys
from pathlib import Path

p=Path(sys.argv[1] if len(sys.argv)>1 else "/etc/nginx/sites-available/panel.kitkaraoke.com")
if not p.is_file():
    raise SystemExit("No existe: "+str(p))
s=p.read_text(encoding="utf-8")
marker="# DJGABO_CDG_RENDER_LAB_V1"
if marker in s:
    print("NGINX_ROUTE=ALREADY_PRESENT "+str(p))
    raise SystemExit(0)

block=r'''    # DJGABO_CDG_RENDER_LAB_V1
    # Sólo el upload con token QR es público. El laboratorio principal sigue
    # protegido por la sesión ADMIN del portal.
    location ^~ /cdg-render-lab/upload/ {
        client_max_body_size 800m;
        rewrite ^/cdg-render-lab/(.*)$ /$1 break;
        proxy_pass http://127.0.0.1:8786;
        include /etc/nginx/proxy_params;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location ^~ /cdg-render-lab/api/qr-upload/ {
        client_max_body_size 800m;
        rewrite ^/cdg-render-lab/(.*)$ /$1 break;
        proxy_pass http://127.0.0.1:8786;
        include /etc/nginx/proxy_params;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location = /cdg-render-lab { return 301 /cdg-render-lab/; }
    location /cdg-render-lab/ {
        client_max_body_size 800m;
        auth_request /_portal_auth_admin;
        error_page 401 403 = @admin_required;
        rewrite ^/cdg-render-lab/(.*)$ /$1 break;
        proxy_pass http://127.0.0.1:8786;
        include /etc/nginx/proxy_params;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_connect_timeout 15s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

'''

# Localiza el server HTTPS que atiende panel.kitkaraoke.com y añade el bloque
# justo antes de su llave final. No depende de nombres de otras rutas.
starts=[]
pos=0
while True:
    i=s.find("server {",pos)
    if i<0: break
    starts.append(i); pos=i+1

target=None
for i in starts:
    depth=0
    j=i
    in_quote=None
    esc=False
    while j<len(s):
        ch=s[j]
        if in_quote:
            if esc: esc=False
            elif ch=="\\": esc=True
            elif ch==in_quote: in_quote=None
        else:
            if ch in ("'",'"'): in_quote=ch
            elif ch=="{": depth+=1
            elif ch=="}":
                depth-=1
                if depth==0:
                    body=s[i:j+1]
                    if "server_name panel.kitkaraoke.com;" in body and ("listen 443" in body or "listen [::]:443" in body):
                        target=(i,j)
                    break
        j+=1
    if target: break

if not target:
    raise SystemExit("No encontré el server HTTPS de panel.kitkaraoke.com en "+str(p))
i,j=target
s=s[:j]+block+s[j:]
p.write_text(s,encoding="utf-8")
print("NGINX_ROUTE=INSTALLED "+str(p))
