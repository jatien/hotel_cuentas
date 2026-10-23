"""
Cifrado de campos concretos de la base de datos (además del cifrado del disco).

Para datos que no deben poder leerse aunque alguien se lleve un volcado de PostgreSQL o una
copia de seguridad sin cifrar: DNI/pasaporte de huéspedes, IBAN, NIF de empleados…

- Algoritmo: Fernet (AES-128-CBC + HMAC-SHA256, de la librería `cryptography`).
- Claves en el .env, nunca en la base de datos:
      FIELD_ENCRYPTION_KEYS=<clave nueva>,<clave anterior>
  La primera cifra; todas descifran. Rotar = poner una nueva delante y ejecutar
  `python manage.py rotar_cifrado`. Generar una clave:
      python manage.py rotar_cifrado --generar-clave
- Valor guardado: "enc1:<token>". Lo que no lleva el prefijo se lee tal cual, así una columna
  existente se puede pasar a cifrada sin migrar los datos de golpe (rotar_cifrado los cifra).

Un campo cifrado no se puede buscar ni ordenar en SQL (cada cifrado es distinto). Si hace falta
buscar por él (p. ej. por NIF), se añade al lado un índice ciego con `indice_ciego()`: un HMAC
determinista que permite `filter(nif_indice=indice_ciego(nif))` sin guardar el NIF en claro.
"""

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import models

PREFIJO = "enc1:"


class ErrorCifrado(Exception):
    pass


def _claves() -> list[str]:
    claves = [c.strip() for c in getattr(settings, "FIELD_ENCRYPTION_KEYS", ()) if c and c.strip()]
    if not claves:
        raise ImproperlyConfigured(
            "Falta FIELD_ENCRYPTION_KEYS en el .env (python manage.py rotar_cifrado --generar-clave)."
        )
    return claves


@lru_cache(maxsize=8)
def _fernet(claves: tuple) -> MultiFernet:
    try:
        return MultiFernet([Fernet(c.encode()) for c in claves])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS contiene una clave Fernet no válida.") from exc


def fernet() -> MultiFernet:
    return _fernet(tuple(_claves()))


def esta_cifrado(valor) -> bool:
    return isinstance(valor, str) and valor.startswith(PREFIJO)


def cifrar(texto: str) -> str:
    return PREFIJO + fernet().encrypt(texto.encode("utf-8")).decode("ascii")


def descifrar(valor: str) -> str:
    if not esta_cifrado(valor):
        return valor  # dato antiguo aún sin cifrar
    try:
        return fernet().decrypt(valor[len(PREFIJO):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ErrorCifrado("No se puede descifrar: ninguna clave de FIELD_ENCRYPTION_KEYS sirve.") from exc


def recifrar(valor: str) -> str:
    """Vuelve a cifrar con la clave principal (rotación). Los valores en claro se cifran."""
    if not esta_cifrado(valor):
        return cifrar(valor)
    token = fernet().rotate(valor[len(PREFIJO):].encode("ascii"))
    return PREFIJO + token.decode("ascii")


def indice_ciego(texto: str, *, normalizar=True) -> str:
    """HMAC-SHA256 determinista para buscar por igualdad un valor cifrado.

    Usa una clave propia (BLIND_INDEX_KEY), distinta de las de cifrado, para que conocer el índice
    no ayude a descifrar. Por defecto normaliza mayúsculas y espacios (NIF «12345678z » = «12345678Z»).
    """
    clave = getattr(settings, "BLIND_INDEX_KEY", "")
    if not clave:
        raise ImproperlyConfigured("Falta BLIND_INDEX_KEY en el .env.")
    if normalizar:
        texto = "".join(texto.split()).upper()
    return hmac.new(clave.encode(), texto.encode("utf-8"), hashlib.sha256).hexdigest()


class CampoCifrado(models.TextField):
    """TextField que se guarda cifrado y se lee en claro.

        documento = CampoCifrado(blank=True)

    Se lee y escribe como un texto normal; en la base de datos solo hay "enc1:…".
    No admite búsquedas por contenido (ver indice_ciego).
    """

    description = "Texto cifrado con Fernet"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return descifrar(value)

    def to_python(self, value):
        if value is None or not isinstance(value, str):
            return value
        return descifrar(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        if esta_cifrado(value):
            return value
        return cifrar(value)

    def get_lookup(self, lookup_name):
        # Cada cifrado es distinto: un filtro por igualdad no encontraría nada sin avisar.
        if lookup_name == "isnull":
            return super().get_lookup(lookup_name)
        raise FieldError(f"Un CampoCifrado no admite el filtro «{lookup_name}»: usa un índice ciego.")


def campos_cifrados():
    """(modelo, campo) de todos los CampoCifrado del proyecto."""
    from django.apps import apps

    for modelo in apps.get_models():
        for campo in modelo._meta.get_fields():
            if isinstance(campo, CampoCifrado):
                yield modelo, campo
