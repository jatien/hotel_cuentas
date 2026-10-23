"""
Comprobaciones de seguridad que se suman a `manage.py check` (y a `check --deploy`).
"""

from django.conf import settings
from django.core.checks import Error, Warning, register

_LOCALES = {"", "localhost", "127.0.0.1", "::1", "db"}


@register(deploy=True)
def base_de_datos(app_configs, **kwargs):
    avisos = []
    bd = settings.DATABASES.get("default", {})
    host = str(bd.get("HOST") or "")
    sslmode = bd.get("OPTIONS", {}).get("sslmode", "prefer")
    if host not in _LOCALES and sslmode != "verify-full":
        avisos.append(Warning(
            f"PostgreSQL está en otro equipo ({host}) y la conexión no verifica el certificado (sslmode={sslmode}).",
            hint="DB_SSLMODE=verify-full y DB_SSLROOTCERT=<ruta del certificado de la CA> (docs/security.md §5).",
            id="seguridad.W001",
        ))
    if str(bd.get("USER")) == "postgres":
        avisos.append(Warning(
            "La app se conecta a PostgreSQL con el superusuario «postgres».",
            hint="Crear roles propios con deploy/postgres/roles.sql y poner DB_USER=cuentas_app (docs/security.md §5).",
            id="seguridad.W002",
        ))
    return avisos


@register(deploy=True)
def copias(app_configs, **kwargs):
    if not [c for c in settings.BACKUP_AGE_RECIPIENTS if c.strip()]:
        return [Warning(
            "No hay copias de seguridad configuradas (BACKUP_AGE_RECIPIENTS vacío).",
            hint="python manage.py copia_seguridad --generar-clave y programar la copia diaria (docs/security.md §5).",
            id="seguridad.W003",
        )]
    return []


@register()
def claves_de_cifrado(app_configs, **kwargs):
    from seguridad.cifrado import campos_cifrados

    campos = [f"{m._meta.label}.{c.name}" for m, c in campos_cifrados()]
    if campos and not [c for c in settings.FIELD_ENCRYPTION_KEYS if c.strip()]:
        return [Error(
            f"Hay campos cifrados ({', '.join(campos)}) pero falta FIELD_ENCRYPTION_KEYS.",
            hint="python manage.py rotar_cifrado --generar-clave",
            id="seguridad.E001",
        )]
    return []
