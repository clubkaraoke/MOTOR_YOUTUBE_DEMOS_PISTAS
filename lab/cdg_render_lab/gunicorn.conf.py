import os
bind = os.getenv("CDG_RENDER_LAB_BIND", "127.0.0.1:8786")
workers = 1
threads = int(os.getenv("CDG_RENDER_LAB_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.getenv("CDG_RENDER_LAB_TIMEOUT", "3600"))
graceful_timeout = 60
accesslog = "-"
errorlog = "-"
