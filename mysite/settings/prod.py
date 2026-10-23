"""
Producción: Django detrás del proxy HTTPS (Caddy) con Waitress (Windows) o Gunicorn (Debian).

Todo lo que falte en el .env y sea imprescindible (SECRET_KEY, ALLOWED_HOSTS, DB_PASSWORD)
hace fallar el arranque en vez de usar un valor inseguro. Comprobación completa:

    python manage.py check --deploy
"""

from decouple import Csv, config

from .base import *  # noqa: F401,F403

DEBUG = False

SECRET_KEY = config("SECRET_KEY")  # obligatorio: python -c "import secrets; print(secrets.token_urlsafe(64))"
if SECRET_KEY.startswith(("django-insecure", "dev-solo-local")) or len(SECRET_KEY) < 50:
    raise RuntimeError("SECRET_KEY de producción insegura: genera una nueva (ver .env.example).")

# Nombres con los que se llega al servidor, p. ej. "pms.hotel.lan,192.168.1.20". Nunca "*".
ALLOWED_HOSTS = config("ALLOWED_HOSTS", cast=Csv())
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise RuntimeError("En producción ALLOWED_HOSTS debe listar los nombres reales, sin '*'.")

# Orígenes HTTPS desde los que se aceptan formularios, p. ej. "https://pms.hotel.lan".
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default=",".join(f"https://{h}" for h in ALLOWED_HOSTS if not h.startswith(".")),
    cast=Csv(),
)


# --- HTTPS ---------------------------------------------------------------------------
# Caddy termina TLS y reenvía a Django por 127.0.0.1 con X-Forwarded-Proto. Confiar en esa
# cabecera solo es seguro porque Waitress/Gunicorn escuchan únicamente en localhost (o en la red
# interna de Docker) y porque ProxyClientIPMiddleware borra X-Forwarded-* que no vengan del proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
TRUSTED_PROXIES = config("TRUSTED_PROXIES", default="127.0.0.1,::1", cast=Csv())
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
# El health check del proxy entra por HTTP local; no se redirige.
SECURE_REDIRECT_EXEMPT = [r"^seguridad/health/$"]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS: el navegador solo usará HTTPS con este servidor. Empieza corto (1 hora) y se sube a
# 31536000 (1 año) cuando el certificado esté instalado en todos los PCs y funcione bien.
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=3600, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False, cast=bool)
SECURE_HSTS_PRELOAD = False  # solo tiene sentido con un dominio público


# --- Estáticos -----------------------------------------------------------------------
# Nombres con hash (caché larga y segura). Requiere "python manage.py collectstatic".
STORAGES = {
    **STORAGES,  # noqa: F405
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}


# --- Correo --------------------------------------------------------------------------
# SMTP real (el de base.py solo imprime en consola). Ahora mismo la app no envía correo;
# queda listo para avisos y recuperación de contraseña.
MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
        'OPTIONS': {
            'host': config("EMAIL_HOST", default="localhost"),
            'port': config("EMAIL_PORT", default=587, cast=int),
            'username': config("EMAIL_HOST_USER", default=""),
            'password': config("EMAIL_HOST_PASSWORD", default=""),
            'use_tls': config("EMAIL_USE_TLS", default=True, cast=bool),
            'timeout': 10,
        },
    },
}


# --- Avisos de check --deploy aceptados a conciencia ---------------------------------
SILENCED_SYSTEM_CHECKS = [
    "security.W019",  # X_FRAME_OPTIONS=SAMEORIGIN: los PDF de facturas se ven en un <iframe> propio
    "security.W021",  # HSTS preload: solo para dominios públicos, no para la LAN
]
