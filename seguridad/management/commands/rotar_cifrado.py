"""
Claves de los campos cifrados (seguridad.cifrado.CampoCifrado).

    python manage.py rotar_cifrado --generar-clave     # clave nueva para FIELD_ENCRYPTION_KEYS
    python manage.py rotar_cifrado --generar-indice    # clave para BLIND_INDEX_KEY
    python manage.py rotar_cifrado                     # recifra todo con la primera clave

Rotar: poner la clave nueva DELANTE en FIELD_ENCRYPTION_KEYS (nueva,antigua), ejecutar el comando
y, cuando termine, quitar la antigua. También cifra los valores que aún estén en claro.
"""

import secrets

from django.core.management.base import BaseCommand
from django.db import transaction

from seguridad.cifrado import campos_cifrados

LOTE = 500


class Command(BaseCommand):
    help = "Genera claves de cifrado de campos o recifra todos los CampoCifrado con la clave principal."

    def add_arguments(self, parser):
        parser.add_argument("--generar-clave", action="store_true")
        parser.add_argument("--generar-indice", action="store_true")

    def handle(self, *args, **o):
        if o["generar_clave"]:
            from cryptography.fernet import Fernet

            self.stdout.write(Fernet.generate_key().decode())
            return
        if o["generar_indice"]:
            self.stdout.write(secrets.token_urlsafe(48))
            return

        total = 0
        for modelo, campo in campos_cifrados():
            pks = list(modelo._base_manager.order_by("pk").values_list("pk", flat=True))
            for i in range(0, len(pks), LOTE):
                with transaction.atomic():
                    objetos = list(modelo._base_manager.filter(pk__in=pks[i:i + LOTE]).only("pk", campo.attname))
                    modelo._base_manager.bulk_update(objetos, [campo.attname])
            total += len(pks)
            self.stdout.write(f"{modelo._meta.label}.{campo.name}: {len(pks)} filas")
        self.stdout.write(self.style.SUCCESS(f"Recifradas {total} filas." if total else "No hay campos cifrados."))
