"""
Ajustes del proyecto, separados por entorno.

- base.py  → común a todos los entornos.
- dev.py   → desarrollo en el PC (DEBUG, runserver).
- prod.py  → servidor real detrás de HTTPS (Waitress/Gunicorn + Caddy).

El entorno se elige con DJANGO_ENV en el .env (o en el entorno del proceso):
    DJANGO_ENV=dev    (por defecto)
    DJANGO_ENV=prod

También se puede apuntar directamente a un módulo con
DJANGO_SETTINGS_MODULE=mysite.settings.prod; este fichero solo se usa cuando
DJANGO_SETTINGS_MODULE es "mysite.settings".
"""

from decouple import config

_ENTORNO = config("DJANGO_ENV", default="dev").strip().lower()

if _ENTORNO == "prod":
    from .prod import *  # noqa: F401,F403
elif _ENTORNO == "dev":
    from .dev import *  # noqa: F401,F403
else:
    raise RuntimeError(f"DJANGO_ENV={_ENTORNO!r} no es válido: usa 'dev' o 'prod'.")
