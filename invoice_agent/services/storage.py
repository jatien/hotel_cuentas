"""
Almacenamiento privado de los originales y URLs firmadas de corta duración.

Los ficheros van a PRIVATE_STORAGE_ROOT (por defecto <proyecto>/private_media),
que NO es MEDIA_ROOT: Django nunca los sirve como estáticos. Solo se
descargan mediante un token firmado (django.core.signing) que caduca a los
SIGNED_URL_SECONDS segundos. La ruta interna no usa el nombre original del
fichero (evita rutas manipuladas y nombres con datos personales).
"""

import uuid
from pathlib import Path

from django.core import signing
from django.core.files.storage import FileSystemStorage
from django.utils import timezone

from invoice_agent import conf

SIGNING_SALT = "invoice_agent.original"


def private_storage() -> FileSystemStorage:
    return FileSystemStorage(location=str(conf.private_root()), base_url=None)


def new_storage_key(empresa_id: int, extension: str) -> str:
    now = timezone.now()
    return f"{empresa_id}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{extension}"


def path_for(storage_key: str) -> Path:
    return Path(private_storage().path(storage_key))


def make_token(public_id) -> str:
    return signing.TimestampSigner(salt=SIGNING_SALT).sign(str(public_id))


def read_token(token: str) -> str:
    """Devuelve el public_id del documento; lanza signing.BadSignature / SignatureExpired."""
    return signing.TimestampSigner(salt=SIGNING_SALT).unsign(token, max_age=int(conf.get("SIGNED_URL_SECONDS")))
