"""Datos de prueba ficticios comunes a los tests de cuadro_cuentas."""
from datetime import date
from decimal import Decimal

from cuadro_cuentas.models import CuentaContable, Ejercicio

# Plan mínimo: lo justo del PGC 2007 para probar sin cargar las 915 cuentas
PLAN_MINIMO = [
    ("1", "Financiación básica"),
    ("12", "Resultados pendientes de aplicación"),
    ("129", "Resultado del ejercicio"),
    ("2", "Activo no corriente"),
    ("21", "Inmovilizaciones materiales"),
    ("216", "Mobiliario"),
    ("28", "Amortización acumulada del inmovilizado"),
    ("281", "Amortización acumulada del inmovilizado material"),
    ("2816", "Amortización acumulada de mobiliario"),
    ("4", "Acreedores y deudores por operaciones comerciales"),
    ("40", "Proveedores"),
    ("400", "Proveedores"),
    ("4000", "Proveedores (euros)"),
    ("41", "Acreedores varios"),
    ("410", "Acreedores por prestaciones de servicios"),
    ("43", "Clientes"),
    ("430", "Clientes"),
    ("46", "Personal"),
    ("465", "Remuneraciones pendientes de pago"),
    ("47", "Administraciones públicas"),
    ("470", "Hacienda Pública, deudora por diversos conceptos"),
    ("4700", "Hacienda Pública, deudora por IVA"),
    ("472", "Hacienda Pública, IVA soportado"),
    ("475", "Hacienda Pública, acreedora por conceptos fiscales"),
    ("4750", "Hacienda Pública, acreedora por IVA"),
    ("4751", "Hacienda Pública, acreedora por retenciones practicadas"),
    ("476", "Organismos de la Seguridad Social, acreedores"),
    ("477", "Hacienda Pública, IVA repercutido"),
    ("5", "Cuentas financieras"),
    ("57", "Tesorería"),
    ("570", "Caja, euros"),
    ("572", "Bancos e instituciones de crédito c/c vista, euros"),
    ("6", "Compras y gastos"),
    ("60", "Compras"),
    ("600", "Compras de mercaderías"),
    ("62", "Servicios exteriores"),
    ("623", "Servicios de profesionales independientes"),
    ("626", "Servicios bancarios y similares"),
    ("628", "Suministros"),
    ("64", "Gastos de personal"),
    ("640", "Sueldos y salarios"),
    ("642", "Seguridad Social a cargo de la empresa"),
    ("68", "Dotaciones para amortizaciones"),
    ("681", "Amortización del inmovilizado material"),
    ("7", "Ventas e ingresos"),
    ("70", "Ventas de mercaderías, de producción propia, de servicios, etc."),
    ("705", "Prestaciones de servicios"),
    ("76", "Ingresos financieros"),
    ("762", "Ingresos de créditos"),
    ("7620", "Ingresos de créditos a largo plazo"),
    ("76200", "Ingresos de créditos a largo plazo, empresas del grupo"),
]

DEFINICIONES = {
    "400": "Deudas con suministradores de mercancías y de los demás bienes definidos en el grupo 3.",
    "62": "Servicios de naturaleza diversa adquiridos por la empresa.",
}
UBICACIONES = {"400": "Figurará en el pasivo corriente del balance."}

# NIF/CIF ficticios con dígito de control correcto
CIF_PROVEEDOR = "A12345674"
CIF_ACREEDOR = "G1234567D"


def crear_plan_minimo():
    for codigo, nombre in sorted(PLAN_MINIMO, key=lambda x: (len(x[0]), x[0])):
        CuentaContable.objects.create(
            codigo=codigo, nombre=nombre, imputable=False,
            definicion=DEFINICIONES.get(codigo, ""), ubicacion=UBICACIONES.get(codigo, ""),
        )


def crear_subcuenta_directa(codigo, nombre, nif="", base=None):
    """Subcuenta sin pasar por el servicio, para preparar datos.

    Como el asistente, cuelga de la cuenta de 3 dígitos salvo que se indique otra base."""
    padre = CuentaContable.objects.filter(codigo=base or codigo[:3]).first()
    return CuentaContable.objects.create(codigo=codigo, nombre=nombre, nif=nif, imputable=True, padre=padre)


def ejercicio(anio=2026, cerrado=False):
    return Ejercicio.objects.create(
        anio=anio, fecha_inicio=date(anio, 1, 1), fecha_fin=date(anio, 12, 31), cerrado=cerrado)


def D(valor):
    return Decimal(valor)