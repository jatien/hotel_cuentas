"""
Desarrollo: DEBUG activado y runserver.

No usar en la red del hotel con datos reales: todo viaja sin cifrar y DEBUG enseña
trazas con datos internos. Para eso está prod.py (ver docs/security.md).
"""

from decouple import Csv, config

from .base import *  # noqa: F401,F403

DEBUG = config("DEBUG", default=True, cast=bool)

# Clave solo para desarrollo si el .env no trae SECRET_KEY. Nunca sirve en prod (prod.py la exige).
SECRET_KEY = config("SECRET_KEY", default="dev-solo-local-no-usar-en-produccion-0f3c9a")

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1,[::1]", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
