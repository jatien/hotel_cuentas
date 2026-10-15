"""Categorías de acreedores que faltaban en el listado de terceros (corrección de Javi, oct 2026).

Solo rellena la categoría si está vacía (no pisa cambios hechos en el admin) y crea
la ficha si todavía no se había importado el listado. La importación de terceros
conserva estas categorías mientras el fichero las traiga vacías.
"""

from django.db import migrations

CATEGORIAS = {
    "410000220": ("The Acid Agency S.L.", "Marketing y Publicidad"),
    "410000195": ("Fomento Agricola Castellonense S.A FACSA", "Agua"),
    "410000138": ("Muson y Mateu S.L.", "Gestoría"),
    "410000147": ("Neobookings S.L.", "Channel"),
    "410000051": ("CAN PEP PISCINAS S.L.", "Piscina"),
    "410000212": ("META PLATFORMS IRELAND LIMITED", "Publicidad"),
    "410000999": ("ACREEDORES VARIOS", "Varios"),
}


def aplicar(apps, schema_editor):
    Tercero = apps.get_model("analisis", "Tercero")
    for cuenta, (nombre, categoria) in CATEGORIAS.items():
        tercero, _ = Tercero.objects.get_or_create(
            cuenta=cuenta, defaults={"nombre": nombre, "tipo": "acreedor"})
        if not tercero.categoria:
            tercero.categoria = categoria
            tercero.save(update_fields=["categoria"])


def deshacer(apps, schema_editor):
    Tercero = apps.get_model("analisis", "Tercero")
    for cuenta, (_, categoria) in CATEGORIAS.items():
        Tercero.objects.filter(cuenta=cuenta, categoria=categoria).update(categoria="")


class Migration(migrations.Migration):

    dependencies = [
        ("analisis", "0005_departamentos_albaranes_y_terceros"),
    ]

    operations = [
        migrations.RunPython(aplicar, deshacer),
    ]
