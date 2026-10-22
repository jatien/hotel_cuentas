"""
Genera el dataset de conversaciones del copilot sobre el hotel ficticio.

    # .env: DB_NAME=cuentas_ficticio  ·  antes: migrate + copilot_hotel_ficticio
    python manage.py copilot_dataset --n 3000 --salida dataset_copilot

Salida: dataset_copilot/{train,val,test}.jsonl + manifest.json (carpeta ignorada por git).
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from copilot_pms.entrenamiento.escenarios import Catalogo, Generador
from copilot_pms.entrenamiento.exportar import escribir
from copilot_pms.entrenamiento.hotel_ficticio import HOY_FICTICIO


class Command(BaseCommand):
    help = "Genera conversaciones de entrenamiento y evaluación sobre el hotel ficticio."

    def add_arguments(self, parser):
        parser.add_argument("--n", type=int, default=3000, help="Número de conversaciones.")
        parser.add_argument("--salida", default="dataset_copilot")
        parser.add_argument("--semilla", type=int, default=1)

    def handle(self, *args, n, salida, semilla, **opciones):
        nombre = settings.DATABASES["default"]["NAME"]
        if "ficticio" not in str(nombre):
            raise CommandError(f"La base de datos «{nombre}» no es la ficticia. Pon DB_NAME=cuentas_ficticio en el .env.")
        catalogo = Catalogo.desde_bd(HOY_FICTICIO)
        if not catalogo.ejercicios:
            raise CommandError("La base ficticia está vacía: ejecuta antes copilot_hotel_ficticio.")
        ejemplos = Generador(catalogo, semilla=semilla).generar(n)
        manifest = escribir(ejemplos, Path(salida), semilla=semilla, hoy=HOY_FICTICIO)
        self.stdout.write(self.style.SUCCESS(f"{len(ejemplos)} conversaciones en {salida}/: {manifest['ejemplos']}"))
