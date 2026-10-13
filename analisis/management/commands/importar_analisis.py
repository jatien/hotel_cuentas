from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from analisis.importacion import IMPORTADORES, ErrorImportacion


class Command(BaseCommand):
    help = ("Importa un fichero de análisis. Ejemplos:\n"
            "  python manage.py importar_analisis reservas analisis/datos/2026/reservas.xlsx\n"
            "  python manage.py importar_analisis compras analisis/datos/2026/compras.xlsx\n"
            "  python manage.py importar_analisis acreedores analisis/datos/2026/acreedores.csv")

    def add_arguments(self, parser):
        parser.add_argument("fuente", choices=sorted(IMPORTADORES))
        parser.add_argument("ruta", type=Path)

    def handle(self, fuente, ruta, **opts):
        if not ruta.is_file():
            raise CommandError(f"No existe el fichero {ruta}")
        try:
            r = IMPORTADORES[fuente](ruta)
        except ErrorImportacion as e:
            raise CommandError(str(e))

        partes = [f"{r['leidas']} registros importados", f"{r['reemplazadas']} anteriores reemplazados"]
        if "años" in r:
            partes.append(f"años {', '.join(map(str, r['años']))}")
        if r.get("control"):
            partes.append("totales de control OK")
        self.stdout.write(self.style.SUCCESS(f"{fuente}: " + "; ".join(partes)))
