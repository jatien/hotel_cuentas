import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.functions import Length

from cuadro_cuentas.models import CuentaContable

ARCHIVO_POR_DEFECTO = Path(__file__).resolve().parents[2] / "data" / "pgc_2007.json"


class Command(BaseCommand):
    help = "Carga o actualiza el cuadro de cuentas, con definiciones, desde un archivo JSON."

    def add_arguments(self, parser):
        parser.add_argument("--archivo", default=str(ARCHIVO_POR_DEFECTO))
        parser.add_argument(
            "--limpiar", action="store_true",
            help="Elimina cuentas de 1 a 5 dígitos que no estén en el plan y no tengan apuntes ni subcuentas.",
        )

    @transaction.atomic
    def handle(self, *args, **opciones):
        datos = json.loads(Path(opciones["archivo"]).read_text(encoding="utf-8"))
        cuentas = datos["cuentas"]
        codigos = {c["codigo"] for c in cuentas}

        creadas = 0
        # por longitud: los padres existen antes que sus hijas
        for c in sorted(cuentas, key=lambda x: (len(x["codigo"]), x["codigo"])):
            _, creada = CuentaContable.objects.update_or_create(
                codigo=c["codigo"],
                defaults={
                    "nombre": c["nombre"],
                    "definicion": c.get("definicion", ""),
                    "ubicacion": c.get("ubicacion", ""),
                    "imputable": False,
                    "padre": None,  # save() lo recalcula por prefijo
                },
            )
            creadas += creada

        borradas = 0
        if opciones["limpiar"]:
            sobrantes = (CuentaContable.objects.annotate(largo=Length("codigo"))
                         .filter(largo__lte=5).exclude(codigo__in=codigos).order_by("-largo"))
            for cuenta in sobrantes:
                if cuenta.apuntes.exists() or cuenta.hijas.exists():
                    self.stdout.write(self.style.WARNING(f"Se conserva {cuenta}: tiene apuntes o subcuentas."))
                else:
                    cuenta.delete()
                    borradas += 1

        self.stdout.write(self.style.SUCCESS(
            f"{datos.get('plan', 'Plan')}: {creadas} nuevas, {len(cuentas) - creadas} actualizadas, "
            f"{borradas} eliminadas."
        ))