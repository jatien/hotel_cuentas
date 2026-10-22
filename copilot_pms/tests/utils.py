"""Datos ficticios para los tests del copilot."""

import json
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from copilot_pms.tools.base import Contexto, ejecutar
from cuadro_cuentas.models import Apunte, Asiento, DatosEmpresa
from cuadro_cuentas.tests.utils import crear_plan_minimo, crear_subcuenta_directa, ejercicio

HOY = date(2026, 10, 22)


def ctx(**kw) -> Contexto:
    datos = {"hoy": HOY, "actor_label": "test"}
    datos.update(kw)
    return Contexto(**datos)


def asiento(fecha, concepto, apuntes, tipo=Asiento.Tipo.OPERACION, contabilizado=True):
    a = Asiento.objects.create(fecha=fecha, concepto=concepto, tipo=tipo)
    for orden, (cuenta, debe, haber) in enumerate(apuntes):
        Apunte.objects.create(asiento=a, orden=orden, cuenta=cuenta,
                              debe=Decimal(debe), haber=Decimal(haber))
    if contabilizado:
        a.contabilizar()
    return a


class ContabilidadBase(TestCase):
    """Ejercicio 2026 con apertura, una venta contabilizada y un pago en borrador."""

    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        DatosEmpresa.objects.create(denominacion="Hotel Prueba S.L.", nif="B12345674")
        cls.banco = crear_subcuenta_directa("57200001", "Banco Demo c/c")
        cls.proveedor = crear_subcuenta_directa("40000001", "Suministros Demo", nif="A12345674")
        cls.ventas = crear_subcuenta_directa("70500001", "Alojamiento")
        cls.ejercicio = ejercicio(2026)
        asiento(date(2026, 1, 1), "Apertura", [(cls.banco, "1000", "0"), (cls.proveedor, "0", "1000")],
                tipo=Asiento.Tipo.APERTURA)
        asiento(date(2026, 3, 15), "Venta habitaciones", [(cls.banco, "500", "0"), (cls.ventas, "0", "500")])
        asiento(date(2026, 6, 10), "Pago proveedor", [(cls.proveedor, "200", "0"), (cls.banco, "0", "200")],
                contabilizado=False)

    def ejecutar(self, nombre, contexto=None, **argumentos):
        return ejecutar(nombre, json.dumps(argumentos, default=str), contexto or ctx())


def ahora():
    return timezone.now()
