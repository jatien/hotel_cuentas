"""
Crea las empresas iniciales a partir de settings.INVOICE_OWN_NIF e
INVOICE_OWN_NAMES (los mismos datos que ya usa invoice_data), sin escribir
ningún NIF ni nombre en el código. Si no están configurados no crea nada:
las empresas se pueden dar de alta en el admin.

Reversible: al deshacer la migración borra solo las empresas sin documentos.
"""

from django.conf import settings
from django.db import migrations


def crear(apps, schema_editor):
    Empresa = apps.get_model("invoice_agent", "Empresa")
    nifs = tuple(getattr(settings, "INVOICE_OWN_NIF", ()) or ())
    nombres = tuple(getattr(settings, "INVOICE_OWN_NAMES", ()) or ())
    for index, nif in enumerate(nifs):
        nombre = nombres[index] if index < len(nombres) else nif
        Empresa.objects.get_or_create(nif=nif.strip().upper(), defaults={"nombre": nombre.title()})


def deshacer(apps, schema_editor):
    Empresa = apps.get_model("invoice_agent", "Empresa")
    nifs = [n.strip().upper() for n in getattr(settings, "INVOICE_OWN_NIF", ()) or ()]
    Empresa.objects.filter(nif__in=nifs, documentos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("invoice_agent", "0001_initial")]

    operations = [migrations.RunPython(crear, deshacer)]
