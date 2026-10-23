"""
Copias de seguridad cifradas con age (https://age-encryption.org).

    base de datos   pg_dump (formato custom, comprimido) ──┐
    ficheros        tar.gz de media/ y private_media/   ───┴─► cifrado age ─► BACKUP_DIR/*.age

- Se cifra mientras se genera (tubería): el volcado en claro nunca toca el disco.
- Se cifra para las claves públicas de BACKUP_AGE_RECIPIENTS ("age1…"). La clave privada NO
  está en el servidor: quien robe el disco o las copias no puede abrirlas.
- Cada copia queda registrada en el modelo CopiaSeguridad (fecha, tamaño, SHA-256, verificación).
- Los .age se pueden abrir también con la herramienta oficial: age -d -i clave.txt fichero.age

Uso: python manage.py copia_seguridad  (ver la ayuda del comando y docs/security.md §5)
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from seguridad.models import CopiaSeguridad

logger = logging.getLogger("seguridad.copias")

EXT_BD = ".dump.age"
EXT_FICHEROS = ".tar.gz.age"
PREFIJO = "cuentas-"


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
def directorio() -> Path:
    destino = Path(settings.BACKUP_DIR)
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def destinatarios():
    from pyrage import x25519

    claves = [c.strip() for c in settings.BACKUP_AGE_RECIPIENTS if c.strip()]
    if not claves:
        raise ImproperlyConfigured(
            "Falta BACKUP_AGE_RECIPIENTS en el .env: la clave pública age1… de quien podrá restaurar "
            "(python manage.py copia_seguridad --generar-clave)."
        )
    try:
        return [x25519.Recipient.from_str(c) for c in claves]
    except Exception as exc:  # pyrage.RecipientError y similares
        raise ImproperlyConfigured(f"BACKUP_AGE_RECIPIENTS no es válida: {exc}") from exc


def _ejecutable(nombre: str) -> str:
    carpeta = getattr(settings, "BACKUP_PG_BIN_DIR", "")
    if carpeta:
        candidato = Path(carpeta) / (nombre + (".exe" if os.name == "nt" else ""))
        if candidato.exists():
            return str(candidato)
    ruta = shutil.which(nombre)
    if not ruta:
        raise ImproperlyConfigured(
            f"No se encuentra {nombre}. Instala el cliente de PostgreSQL o indica su carpeta en BACKUP_PG_BIN_DIR "
            r"(en Windows, p. ej. C:\Program Files\PostgreSQL\17\bin)."
        )
    return ruta


def _entorno_pg() -> dict:
    """Credenciales para pg_dump/pg_restore por variables de entorno (nunca en la línea de órdenes)."""
    bd = settings.DATABASES["default"]
    env = os.environ.copy()
    env.update({
        "PGHOST": str(bd.get("HOST") or "localhost"),
        "PGPORT": str(bd.get("PORT") or "5432"),
        "PGUSER": str(bd.get("USER") or ""),
        "PGPASSWORD": str(bd.get("PASSWORD") or ""),
        "PGDATABASE": str(bd.get("NAME") or ""),
    })
    opciones = bd.get("OPTIONS", {})
    if opciones.get("sslmode"):
        env["PGSSLMODE"] = opciones["sslmode"]
    if opciones.get("sslrootcert"):
        env["PGSSLROOTCERT"] = opciones["sslrootcert"]
    return env


# --------------------------------------------------------------------------- #
# Escritura cifrada
# --------------------------------------------------------------------------- #
def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _cifrar_a_fichero(origen, destino: Path, receptores) -> None:
    import pyrage

    with destino.open("wb") as salida:
        pyrage.encrypt_io(origen, salida, receptores)


@dataclass
class _Salida:
    registro: CopiaSeguridad
    ruta: Path | None


def _registrar(tipo, inicio, ruta: Path | None, error: str = "") -> _Salida:
    if ruta and ruta.exists():
        try:
            ruta.chmod(0o600)  # solo el usuario del servicio (en Windows no tiene efecto)
        except OSError:  # pragma: no cover
            pass
    registro = CopiaSeguridad.objects.create(
        tipo=tipo,
        estado=CopiaSeguridad.Estado.ERROR if error else CopiaSeguridad.Estado.OK,
        fichero=ruta.name if ruta else "",
        tamano=ruta.stat().st_size if ruta and ruta.exists() else 0,
        sha256=_sha256(ruta) if ruta and ruta.exists() else "",
        duracion_s=round(time.monotonic() - inicio, 2),
        error=error[:4000],
    )
    if error:
        logger.error("copia_fallida tipo=%s error=%s", tipo, error[:500])
    else:
        logger.info("copia_ok tipo=%s fichero=%s bytes=%s", tipo, registro.fichero, registro.tamano)
    return _Salida(registro, ruta)


def _nombre(extension: str) -> str:
    return f"{PREFIJO}{timezone.now():%Y%m%d-%H%M%S}{extension}"


def copia_base_datos() -> _Salida:
    """pg_dump -Fc → age → BACKUP_DIR/cuentas-AAAAMMDD-HHMMSS.dump.age"""
    inicio = time.monotonic()
    receptores = destinatarios()
    final = directorio() / _nombre(EXT_BD)
    parcial = final.with_suffix(final.suffix + ".partial")
    try:
        proceso = subprocess.Popen(
            [_ejecutable("pg_dump"), "--format=custom", "--compress=6", "--no-password"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_entorno_pg(),
        )
        errores = []
        hilo_err = threading.Thread(target=lambda: errores.append(proceso.stderr.read()), daemon=True)
        hilo_err.start()
        _cifrar_a_fichero(proceso.stdout, parcial, receptores)
        codigo = proceso.wait()
        hilo_err.join(timeout=5)
        if codigo != 0:
            mensaje = b"".join(errores).decode("utf-8", "replace").strip() or f"pg_dump terminó con código {codigo}"
            raise RuntimeError(mensaje)
        parcial.replace(final)
        return _registrar(CopiaSeguridad.Tipo.BASE_DATOS, inicio, final)
    except Exception as exc:  # se registra y se informa; nunca queda un .age a medias
        parcial.unlink(missing_ok=True)
        return _registrar(CopiaSeguridad.Tipo.BASE_DATOS, inicio, None, error=str(exc) or type(exc).__name__)


def carpetas_ficheros() -> list[tuple[str, Path]]:
    """Carpetas de ficheros subidos que entran en la copia: (nombre dentro del tar, ruta)."""
    carpetas = [("media", Path(settings.MEDIA_ROOT))]
    try:
        from invoice_agent import conf as conf_ia

        carpetas.append(("private_media", Path(conf_ia.private_root())))
    except Exception:  # pragma: no cover - si invoice_agent no está instalada
        pass
    return [(n, r) for n, r in carpetas if r.exists()]


def copia_ficheros() -> _Salida:
    """tar.gz de media/ y private_media/ → age, por una tubería (sin tar en claro en disco)."""
    inicio = time.monotonic()
    receptores = destinatarios()
    final = directorio() / _nombre(EXT_FICHEROS)
    parcial = final.with_suffix(final.suffix + ".partial")
    lectura, escritura = os.pipe()
    fallo: list[BaseException] = []

    def empaquetar():
        try:
            with os.fdopen(escritura, "wb") as tubo, tarfile.open(fileobj=tubo, mode="w|gz") as tar:
                for nombre, ruta in carpetas_ficheros():
                    tar.add(ruta, arcname=nombre)
        except BaseException as exc:  # noqa: BLE001 — se informa en el hilo principal
            fallo.append(exc)

    hilo = threading.Thread(target=empaquetar, daemon=True)
    hilo.start()
    try:
        with os.fdopen(lectura, "rb") as tubo:
            _cifrar_a_fichero(tubo, parcial, receptores)
        hilo.join()
        if fallo:
            raise fallo[0]
        parcial.replace(final)
        return _registrar(CopiaSeguridad.Tipo.FICHEROS, inicio, final)
    except Exception as exc:
        hilo.join(timeout=5)
        parcial.unlink(missing_ok=True)
        return _registrar(CopiaSeguridad.Tipo.FICHEROS, inicio, None, error=str(exc) or type(exc).__name__)


# --------------------------------------------------------------------------- #
# Retención
# --------------------------------------------------------------------------- #
def podar(dias: int, minimo: int = 3) -> list[str]:
    """Borra copias de más de `dias` días, conservando siempre las `minimo` más recientes de cada tipo."""
    borradas = []
    limite = timezone.now() - timedelta(days=dias)
    for extension in (EXT_BD, EXT_FICHEROS):
        ficheros = sorted(directorio().glob(f"{PREFIJO}*{extension}"), key=lambda p: p.name, reverse=True)
        for ruta in ficheros[minimo:]:
            fecha = datetime.fromtimestamp(ruta.stat().st_mtime, tz=timezone.get_current_timezone())
            if fecha < limite:
                ruta.unlink()
                borradas.append(ruta.name)
    if borradas:
        logger.info("copias_podadas %s", ",".join(borradas))
    return borradas


# --------------------------------------------------------------------------- #
# Verificación (con la clave privada, desde fuera del servidor o en una restauración de prueba)
# --------------------------------------------------------------------------- #
def verificar(ruta: Path, fichero_identidad: Path) -> dict:
    """Descifra la copia en memoria y comprueba que es restaurable, sin restaurar nada.

    - .dump.age → `pg_restore --list` debe leer el índice del volcado.
    - .tar.gz.age → el tar se recorre entero.
    Marca la copia como verificada si está registrada y la huella coincide.
    """
    import pyrage
    from pyrage import x25519

    identidades = [x25519.Identity.from_str(linea.strip())
                   for linea in fichero_identidad.read_text(encoding="utf-8").splitlines()
                   if linea.strip().startswith("AGE-SECRET-KEY-")]
    if not identidades:
        raise ValueError(f"{fichero_identidad} no contiene ninguna clave AGE-SECRET-KEY-…")

    huella = _sha256(ruta)
    with ruta.open("rb") as entrada:
        claro = io.BytesIO()
        pyrage.decrypt_io(entrada, claro, identidades)
    claro.seek(0)

    if ruta.name.endswith(EXT_BD):
        resultado = subprocess.run([_ejecutable("pg_restore"), "--list"], input=claro.getvalue(),
                                   capture_output=True)
        if resultado.returncode != 0:
            raise ValueError("pg_restore no reconoce el volcado: " + resultado.stderr.decode("utf-8", "replace")[:500])
        detalle = {"objetos": sum(1 for linea in resultado.stdout.splitlines() if linea and not linea.startswith(b";"))}
    elif ruta.name.endswith(EXT_FICHEROS):
        with tarfile.open(fileobj=claro, mode="r:gz") as tar:
            miembros = tar.getmembers()
        detalle = {"ficheros": sum(1 for m in miembros if m.isfile())}
    else:
        raise ValueError(f"{ruta.name} no es una copia de cuentas")

    registro = CopiaSeguridad.objects.filter(fichero=ruta.name, sha256=huella).first()
    if registro:
        registro.verificada = timezone.now()
        registro.save(update_fields=["verificada"])
    logger.info("copia_verificada fichero=%s %s", ruta.name, detalle)
    return {"fichero": ruta.name, "sha256": huella, "registrada": bool(registro), **detalle}
