"""Importa un Excel de cajas desde la línea de comandos.

    python manage.py importar_cajas ruta/cajas_26.xlsx              # todas las hojas mensuales
    python manage.py importar_cajas ruta/cajas_26.xlsx --mes 5      # solo mayo
    python manage.py importar_cajas ruta/cajas_26.xlsx --anio 2026 --reemplazar
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from cajas.importacion import ErrorImportacion, importar_libro
from cajas.models import MESES_NOMBRES


class Command(BaseCommand):
    help = "Importa las cajas mensuales de un Excel (una hoja por mes)."

    def add_arguments(self, parser):
        parser.add_argument("ruta", type=Path)
        parser.add_argument("--anio", type=int, help="Año (si no, se deduce de la hoja o del fichero)")
        parser.add_argument("--mes", type=int, choices=range(1, 13), help="Importar solo este mes")
        parser.add_argument("--reemplazar", action="store_true", help="Reemplazar meses ya existentes")

    def handle(self, ruta, anio, mes, reemplazar, **opts):
        if not ruta.exists():
            raise CommandError(f"No existe {ruta}")
        try:
            resultados = importar_libro(ruta, ruta.name, anio=anio, mes=mes, reemplazar=reemplazar)
        except ErrorImportacion as e:
            raise CommandError(str(e))

        for r in resultados:
            nombre = f"{MESES_NOMBRES[r['mes']]} {r['anio'] or '?'} ({r['hoja']})"
            if r["estado"] == "importado":
                self.stdout.write(self.style.SUCCESS(f"{nombre}: importado, {r['dias']} días con datos"))
            elif r["estado"] == "omitido":
                self.stdout.write(f"{nombre}: ya existe, no se toca (usa --reemplazar)")
            elif r["estado"] == "vacio":
                self.stdout.write(f"{nombre}: hoja vacía")
            else:
                self.stdout.write(self.style.ERROR(f"{nombre}: {r['error']}"))
            for aviso in r["avisos"]:
                self.stdout.write(self.style.WARNING(f"    aviso: {aviso}"))
