import json
from decimal import Decimal

from django.test import TestCase

from cajas.models import CajaDia, CajaMes
from copilot_pms.models import EstadoLlamada
from copilot_pms.tests.utils import ctx
from copilot_pms.tools.base import ejecutar
from nominas.models import Empleado, NominaMensual

CAMPOS_NOMINA = ("deveng", "base_din", "base_esp", "irpf_esp", "irpf_din", "embarg", "prima", "manut",
                 "enf_acc", "bonif", "s_neto", "ss_trabaj", "ss_empr", "rlc")


def run(nombre, **args):
    return ejecutar(nombre, json.dumps(args), ctx())


class CajasTests(TestCase):
    def setUp(self):
        julio = CajaMes.objects.create(anio=2026, mes=7)
        CajaDia.objects.create(caja_mes=julio, dia=1, bar=Decimal("100.50"), comedor=Decimal("200"),
                               ecotasa=Decimal("10"))
        CajaDia.objects.create(caja_mes=julio, dia=2, bar=Decimal("50"))

    def test_mes(self):
        r = run("resumen_cajas", anio=2026, mes=7)
        self.assertEqual(r.estado, EstadoLlamada.OK)
        self.assertEqual(r.datos["puntos_venta"]["Bar"], "150.50")
        self.assertEqual(r.datos["total_ventas"], "350.50")
        self.assertEqual(r.datos["otros_cobros"]["Ecotasa"], "10.00")

    def test_anio_parcial_avisa(self):
        r = run("resumen_cajas", anio=2026)
        self.assertEqual(r.datos["meses_con_datos"], ["Julio"])
        self.assertTrue(r.avisos)

    def test_mes_sin_datos(self):
        r = run("resumen_cajas", anio=2026, mes=8)
        self.assertEqual((r.estado, r.codigo), (EstadoLlamada.NO_DISPONIBLE, "SIN_DATOS_CAJAS"))
        self.assertEqual(r.datos["meses_con_datos"], [7])


class CostePersonalTests(TestCase):
    def setUp(self):
        for nif, dpto, coste, extra in (("1A", "Cocina", "2000", "100"), ("2B", "Cocina", "1800", "0"),
                                        ("3C", "Recepción", "1900", "50")):
            empleado = Empleado.objects.create(nif=nif, nombre=f"Empleado {nif}")
            NominaMensual.objects.create(empleado=empleado, anio=2026, mes=5, departamento=dpto,
                                         cost_tot=Decimal(coste), importe_extra=Decimal(extra),
                                         **{c: Decimal("0") for c in CAMPOS_NOMINA})

    def test_por_departamento_sin_datos_personales(self):
        r = run("coste_personal", anio=2026, mes=5)
        self.assertEqual(r.estado, EstadoLlamada.OK)
        cocina = r.datos["departamentos"][0]
        self.assertEqual((cocina["departamento"], cocina["total"], cocina["empleados"]), ("Cocina", "3900.00", 2))
        self.assertEqual(r.datos["total"]["total"], "5850.00")
        self.assertNotIn("Empleado", json.dumps(r.datos))

    def test_sin_nominas(self):
        r = run("coste_personal", anio=2026, mes=6)
        self.assertEqual(r.estado, EstadoLlamada.NO_DISPONIBLE)
        self.assertEqual(r.datos["meses_con_datos"], [5])
