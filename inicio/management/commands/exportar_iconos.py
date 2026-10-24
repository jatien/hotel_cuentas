"""
Exporta los iconos del inicio como SVG con colores reales, listos para importar en Penpot.

    python manage.py exportar_iconos C:\\ruta\\carpeta

Deja en la carpeta un SVG por rama (reservas.svg, facturas.svg...) y paleta.svg con las
muestras de color. En Penpot: arrastrar los SVG al proyecto «PMS icons» (se importan como
vectores editables) y añadir los colores de paleta.svg a la biblioteca del fichero.
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from django.template.loader import get_template

from inicio.iconos_svg import PALETA, exportar
from inicio.modulos import MODULOS


def paleta_svg() -> str:
    filas = []
    for i, (nombre, (color, *_)) in enumerate(PALETA.items()):
        y = 10 + i * 34
        filas.append(f'  <rect x="10" y="{y}" width="28" height="28" rx="6" fill="{color}" stroke="#33403E"/>\n'
                     f'  <text x="48" y="{y + 19}" font-family="sans-serif" font-size="13" fill="#33403E">'
                     f'{nombre} {color}</text>')
    alto = 20 + len(PALETA) * 34
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="{alto}" viewBox="0 0 240 {alto}">\n'
            + "\n".join(filas) + "\n</svg>\n")


class Command(BaseCommand):
    help = "Exporta los iconos del inicio a SVG con colores reales (para Penpot)."

    def add_arguments(self, parser):
        parser.add_argument("carpeta", help="Carpeta donde dejar los SVG.")

    def handle(self, carpeta, **opciones):
        destino = Path(carpeta)
        destino.mkdir(parents=True, exist_ok=True)
        for m in MODULOS:
            origen = Path(get_template(m.icono).origin.name)
            (destino / f"{m.clave}.svg").write_text(exportar(origen.read_text(encoding="utf-8")), encoding="utf-8")
            self.stdout.write(f"  {m.clave}.svg")
        (destino / "paleta.svg").write_text(paleta_svg(), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Iconos y paleta exportados a {destino}"))
