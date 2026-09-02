import os

# Debe permanecer en un solo worker: sesiones, audio caliente y progreso de tareas
# se comparten en RAM. Los threads permiten atender PLAY/seek mientras renderiza.
bind = os.getenv("DJGABO_BIND", "127.0.0.1:8765")
workers = 1
threads = int(os.getenv("DJGABO_GUNICORN_THREADS", "8"))
worker_class = "gthread"
timeout = int(os.getenv("DJGABO_HTTP_TIMEOUT_SECONDS", "3600"))
graceful_timeout = 120
keepalive = 5
# No reciclar por número de requests: un reciclado borraría sesiones y tareas
# que aún son memoria del único proceso. systemd sí reinicia ante un fallo real.
max_requests = 0
max_requests_jitter = 0
preload_app = False
# Nginx registra sólo $uri (sin query string) para no guardar tokens de sesión.
accesslog = None
errorlog = "-"
capture_output = True
