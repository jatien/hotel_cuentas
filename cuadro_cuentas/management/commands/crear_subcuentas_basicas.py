from django.core.management.base import BaseCommand

from cuadro_cuentas.models import CuentaContable
from cuadro_cuentas.services import crear_subcuenta

# Subcuentas que usan los asientos modelo y que casi cualquier hotel necesita.
# (cuenta del plan, nombre de la subcuenta)
BASICAS = [
    ("129", "Resultado del ejercicio"),
    ("465", "Remuneraciones pendientes de pago"),
    ("4700", "Hacienda Pública, IVA a compensar"),
    ("472", "IVA soportado"),
    ("4750", "Hacienda Pública, IVA a ingresar"),
    ("4751", "Retenciones de IRPF practicadas"),
    ("4752", "Impuesto sobre sociedades a pagar"),
    ("476", "Seguridad Social acreedora"),
    ("477", "IVA repercutido"),
    ("570", "Caja recepción"),
    ("626", "Comisiones bancarias"),
    ("640", "Sueldos y salarios"),
    ("642", "Seguridad Social a cargo de la empresa"),
]


class Command(BaseCommand):
    help = "Crea las subcuentas de 8 dígitos básicas (IVA, retenciones, nóminas, resultado...) si no existen."

    def handle(self, *args, **opciones):
        creadas = 0
        for base, nombre in BASICAS:
            if CuentaContable.objects.filter(padre__codigo=base, nombre=nombre).exists():
                continue
            sub = crear_subcuenta(base, nombre)
            self.stdout.write(f"  {sub.codigo} {sub.nombre}")
            creadas += 1
        self.stdout.write(self.style.SUCCESS(f"{creadas} subcuentas creadas."))