# Imagen de la app para el servidor Debian (Gunicorn). Construir desde la raíz del repositorio:
#   docker compose -f deploy/docker/compose.yaml build
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract con español para el OCR de invoice_data; cliente de PostgreSQL (pg_dump/pg_restore)
# para las copias de seguridad, misma versión que el servidor de compose.yaml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuario sin privilegios: la app no puede modificar su propio código.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin cuentas \
    && mkdir -p /app/media /app/private_media /app/logs /app/staticfiles /app/backups \
    && chown -R cuentas:cuentas /app/media /app/private_media /app/logs /app/staticfiles /app/backups

ENV DJANGO_ENV=prod \
    TESSERACT_CMD=/usr/bin/tesseract \
    GUNICORN_BIND=0.0.0.0:8001

USER cuentas
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8001/seguridad/health/', headers={'Host': 'localhost'}), timeout=4)"

CMD ["sh", "-c", "python manage.py collectstatic --noinput -v0 && gunicorn -c deploy/gunicorn.conf.py mysite.wsgi"]
