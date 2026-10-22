"""
Crea el hotel ficticio (datos inventados) para generar el dataset y evaluar el copilot.

    # en el .env: DB_NAME=cuentas_ficticio   (una base de datos APARTE, nunca la del hotel)
    python manage.py migrate
    python manage.py copilot_hotel_ficticio

Se niega a ejecutarse si la base de datos no se llama *ficticio* o si ya tiene contabilidad.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from copilot_pms.entrenamiento.hotel_ficticio import GeneradorHotel, base_de_datos_vacia


class Command(BaseCommand):
    help = "Llena una base de datos aparte con un hotel ficticio (datos inventados)."

    def add_arguments(self, parser):
        parser.add_argument("--semilla", type=int, default=7)

    def handle(self, *args, semilla, **opciones):
        nombre = settings.DATABASES["default"]["NAME"]
        if "ficticio" not in str(nombre):
            raise CommandError(f"La base de datos «{nombre}» no es la ficticia. Pon DB_NAME=cuentas_ficticio en el .env.")
        if not base_de_datos_vacia():
            raise CommandError("La base de datos ya tiene contabilidad: bórrala y vuelve a crearla (migrate).")
        hotel = GeneradorHotel(semilla=semilla).crear()
        self.stdout.write(self.style.SUCCESS(
            f"Hotel ficticio creado: {hotel.empresa} · {len(hotel.proveedores)} proveedores · "
            f"{len(hotel.facturas)} facturas · «hoy» = {hotel.hoy:%d/%m/%Y}"))
