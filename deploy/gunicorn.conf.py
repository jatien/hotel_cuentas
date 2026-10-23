"""
Gunicorn (Debian / Docker). Uso: gunicorn -c deploy/gunicorn.conf.py mysite.wsgi

Solo escucha en la red interna (Docker) o en 127.0.0.1: a la app se llega por Caddy.
"""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8001")
workers = int(os.environ.get("GUNICORN_WORKERS", min(4, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", 4))
worker_class = "gthread"

# El copilot y la extracción de facturas llaman a modelos que pueden tardar.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 300))
graceful_timeout = 30
keepalive = 5

# Reciclar procesos de vez en cuando (fugas de memoria del OCR / PDF).
max_requests = 1000
max_requests_jitter = 100

# Límites de cabeceras (las peticiones grandes ya las corta Caddy).
limit_request_line = 8190
limit_request_fields = 100

# La IP real y el esquema los resuelve Django con TRUSTED_PROXIES (seguridad/middleware.py).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
