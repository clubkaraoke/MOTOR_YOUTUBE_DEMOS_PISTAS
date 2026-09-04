from __future__ import annotations
import sys
from pathlib import Path

p=Path(sys.argv[1] if len(sys.argv)>1 else "/etc/nginx/sites-available/panel.kitkaraoke.com")
if not p.is_file(): raise SystemExit("No existe: "+str(p))
s=p.read_text(encoding="utf-8")
marker="# DJGABO_CDG_ENGINE_V2_CLONE"
if marker in s:
    print("NGINX_ROUTE=ALREADY_PRESENT "+str(p)); raise SystemExit(0)
block=r'''    # DJGABO_CDG_ENGINE_V2_CLONE
    # Clon aislado: Nginx elimina /cdg-v2 y Flask escucha sólo en 127.0.0.1:8787.
    location = /cdg-v2 { return 301 /cdg-v2/; }
    location ^~ /cdg-v2/ {
        client_max_body_size 800m;
        rewrite ^/cdg-v2/(.*)$ /$1 break;
        proxy_pass http://127.0.0.1:8787;
        include /etc/nginx/proxy_params;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_connect_timeout 15s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

'''
starts=[];pos=0
while True:
    i=s.find("server {",pos)
    if i<0: break
    starts.append(i);pos=i+1
target=None
for i in starts:
    depth=0;j=i;quote=None;esc=False
    while j<len(s):
        ch=s[j]
        if quote:
            if esc: esc=False
            elif ch=="\\": esc=True
            elif ch==quote: quote=None
        else:
            if ch in ("'",'"'): quote=ch
            elif ch=="{": depth+=1
            elif ch=="}":
                depth-=1
                if depth==0:
                    body=s[i:j+1]
                    if "server_name panel.kitkaraoke.com;" in body and ("listen 443" in body or "listen [::]:443" in body): target=(i,j)
                    break
        j+=1
    if target: break
if not target: raise SystemExit("No encontre server HTTPS panel.kitkaraoke.com en "+str(p))
i,j=target;s=s[:j]+block+s[j:];p.write_text(s,encoding="utf-8")
print("NGINX_ROUTE=INSTALLED "+str(p))
