from __future__ import annotations
import sys
from pathlib import Path

p=Path(sys.argv[1] if len(sys.argv)>1 else "/etc/nginx/sites-available/panel.kitkaraoke.com")
s=p.read_text(encoding="utf-8")
marker="# DJGABO_CDG_RENDER_LAB_V1"
if marker in s:
    print("NGINX_ROUTE=ALREADY_PRESENT")
    raise SystemExit(0)

anchor="    location = /p-youtube { return 301 /p-youtube/; }"
if anchor not in s:
    raise SystemExit("No encontré ancla segura para Nginx")

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
p.write_text(s.replace(anchor,block+anchor,1),encoding="utf-8")
print("NGINX_ROUTE=INSTALLED")
