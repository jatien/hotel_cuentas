from django.core.management.base import BaseCommand
from django.db import transaction

from cuadro_cuentas.models import CuentaContable

PLAN_BASE = [
    ("1", "Financiación básica"),
    ("10", "Capital"), ("100", "Capital social"),
    ("12", "Resultados pendientes de aplicación"), ("129", "Resultado del ejercicio"),
    ("2", "Activo no corriente"),
    ("21", "Inmovilizaciones materiales"), ("211", "Construcciones"), ("216", "Mobiliario"),
    ("217", "Equipos para procesos de información"),
    ("28", "Amortización acumulada del inmovilizado"),
    ("281", "Amortización acumulada del inmovilizado material"),
    ("3", "Existencias"),
    ("30", "Comerciales"), ("300", "Mercaderías"),
    ("4", "Acreedores y deudores por operaciones comerciales"),
    ("40", "Proveedores"), ("400", "Proveedores"),
    ("41", "Acreedores varios"), ("410", "Acreedores por prestaciones de servicios"),
    ("43", "Clientes"), ("430", "Clientes"),
    ("46", "Personal"), ("465", "Remuneraciones pendientes de pago"),
    ("47", "Administraciones públicas"),
    ("472", "Hacienda Pública, IVA soportado"),
    ("475", "Hacienda Pública, acreedora por conceptos fiscales"),
    ("4751", "Hacienda Pública, acreedora por retenciones practicadas"),
    ("476", "Organismos de la Seguridad Social, acreedores"),
    ("477", "Hacienda Pública, IVA repercutido"),
    ("5", "Cuentas financieras"),
    ("52", "Deudas a corto plazo por préstamos recibidos y otros conceptos"),
    ("520", "Deudas a corto plazo con entidades de crédito"),
    ("57", "Tesorería"), ("570", "Caja, euros"),
    ("572", "Bancos e instituciones de crédito c/c vista, euros"),
    ("6", "Compras y gastos"),
    ("60", "Compras"), ("600", "Compras de mercaderías"),
    ("602", "Compras de otros aprovisionamientos"),
    ("61", "Variación de existencias"), ("610", "Variación de existencias de mercaderías"),
    ("62", "Servicios exteriores"), ("621", "Arrendamientos y cánones"),
    ("622", "Reparaciones y conservación"), ("623", "Servicios de profesionales independientes"),
    ("625", "Primas de seguros"), ("626", "Servicios bancarios y similares"),
    ("627", "Publicidad, propaganda y relaciones públicas"), ("628", "Suministros"),
    ("629", "Otros servicios"),
    ("63", "Tributos"), ("631", "Otros tributos"),
    ("64", "Gastos de personal"), ("640", "Sueldos y salarios"),
    ("642", "Seguridad Social a cargo de la empresa"),
    ("66", "Gastos financieros"), ("662", "Intereses de deudas"),
    ("68", "Dotaciones para amortizaciones"), ("681", "Amortización del inmovilizado material"),
    ("7", "Ventas e ingresos"),
    ("70", "Ventas de mercaderías, de producción propia, de servicios, etc."),
    ("700", "Ventas de mercaderías"), ("705", "Prestaciones de servicios"),
    ("75", "Otros ingresos de gestión"), ("759", "Ingresos por servicios diversos"),
]


class Command(BaseCommand):
    help = "Carga (o actualiza) el plan de cuentas base del PGC."

    @transaction.atomic
    def handle(self, *args, **options):
        codigos = [c for c, _ in PLAN_BASE]
        creadas = 0
        for codigo, nombre in sorted(PLAN_BASE, key=lambda x: (len(x[0]), x[0])):
            tiene_hijas = any(o != codigo and o.startswith(codigo) for o in codigos)
            _, creada = CuentaContable.objects.update_or_create(
                codigo=codigo,
                defaults={"nombre": nombre, "imputable": not tiene_hijas},
            )
            creadas += creada
        self.stdout.write(self.style.SUCCESS(
            f"Plan de cuentas cargado: {creadas} nuevas, {len(PLAN_BASE) - creadas} actualizadas."
        ))