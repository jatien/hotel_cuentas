"""
Importa al inicio los iconos exportados de Penpot (o de cualquier editor de SVG).

    python manage.py importar_iconos C:\\ruta\\carpeta            # todos los <rama>.svg que haya
    python manage.py importar_iconos C:\\ruta\\carpeta --solo facturas contabilidad
    python manage.py importar_iconos C:\\ruta\\carpeta --comprobar  # solo avisa, no escribe

Cada fichero se llama como la rama (reservas.svg, facturas.svg, contabilidad.svg,
analisis.svg). Los colores de la paleta pasan a clases del tema; los que no son de la
paleta se dejan y se avisa. El resultado va a templates/inicio/iconos/.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.template.loader import get_template

from inicio.iconos_svg import importar
from inicio.modulos import MODULOS


class Command(BaseCommand):
    help = "Importa iconos SVG exportados de Penpot a templates/inicio/iconos/."

    def add_arguments(self, parser):
        parser.add_argument("carpeta", help="Carpeta con los SVG exportados.")
        parser.add_argument("--solo", nargs="+", metavar="RAMA", help="Importar solo estas ramas.")
        parser.add_argument("--comprobar", action="store_true", help="Revisar sin escribir nada.")

    def handle(self, carpeta, solo=None, comprobar=False, **opciones):
        origen = Path(carpeta)
        if not origen.is_dir():
            raise CommandError(f"No existe la carpeta {origen}")
        claves = {m.clave: m for m in MODULOS}
        if solo:
            desconocidas = set(solo) - set(claves)
            if desconocidas:
                raise CommandError(f"Ramas desconocidas: {', '.join(sorted(desconocidas))}")
        importados = 0
        for clave, m in claves.items():
            if solo and clave not in solo:
                continue
            fichero = origen / f"{clave}.svg"
            if not fichero.exists():
                if solo:
                    self.stderr.write(f"  Falta {fichero.name}")
                continue
            svg, avisos = importar(fichero.read_text(encoding="utf-8"))
            for aviso in avisos:
                self.stdout.write(self.style.WARNING(f"  {clave}: {aviso}"))
            if not comprobar:
                Path(get_template(m.icono).origin.name).write_text(svg, encoding="utf-8")
            importados += 1
            self.stdout.write(f"  {clave}.svg {'revisado' if comprobar else 'importado'}")
        if not importados:
            raise CommandError("No había ningún <rama>.svg en la carpeta.")
        self.stdout.write(self.style.SUCCESS(f"{importados} icono(s) {'revisados' if comprobar else 'importados'}."))
