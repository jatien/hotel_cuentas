"""Tests de la app analisis. Todos los datos son ficticios."""

import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from .importacion import ErrorImportacion, importar_acreedores
from .models import AsignacionDepartamento, CompraProveedor, FacturaAcreedor, Reserva
from .resumen import SIN_ASIGNAR, calcular_resumen, normalizar_departamento

HOY = date(2026, 6, 15)


def reserva(numero, entrada, salida, estancias="0", tasas="0", extras="0", habitaciones=1, agencia="AGENCIA A"):
    return Reserva.objects.create(
        numero=numero, fecha_reserva=date(2026, 1, 1), agencia=agencia, estado="Salida",
        entrada=entrada, salida=salida, habitaciones=habitaciones,
        fact_estancias=D(estancias), fact_impuesto=D(tasas), fact_extras=D(extras),
        total_estancias=D(estancias),
    )


class RepartoIngresosTests(TestCase):
    def test_reparte_por_noches_entre_meses(self):
        # 4 noches: 30/4, 1/5, 2/5, 3/5 -> 1 noche en abril y 3 en mayo
        reserva(1, date(2026, 4, 30), date(2026, 5, 4), estancias="400")
        r = calcular_resumen(2026, hoy=HOY)
        self.assertEqual(r["mensual"]["estancias"][3], D("100"))
        self.assertEqual(r["mensual"]["estancias"][4], D("300"))
        self.assertEqual(r["totales"]["noches_hab"], 4)

    def test_las_tasas_se_suman_al_ingreso(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 3), estancias="200", tasas="8", extras="12")
        t = calcular_resumen(2026, hoy=HOY)["totales"]
        self.assertEqual(t["ingresos"], D("220"))
        self.assertEqual(t["tasas"], D("8"))
        self.assertEqual(t["adr"], D("100"))      # el ADR solo usa estancias

    def test_noches_futuras_son_previsto(self):
        reserva(1, date(2026, 6, 14), date(2026, 6, 18), estancias="400")   # 14 y 15 realizadas
        sin = calcular_resumen(2026, hoy=HOY)["totales"]
        con = calcular_resumen(2026, incluir_previsto=True, hoy=HOY)["totales"]
        self.assertEqual(sin["ingresos"], D("200"))
        self.assertEqual(sin["previsto"], D("200"))
        self.assertEqual(con["ingresos"], D("400"))

    def test_reserva_que_cruza_el_ano_solo_cuenta_sus_noches(self):
        reserva(1, date(2025, 12, 30), date(2026, 1, 2), estancias="300")  # 1 de 3 noches en 2026
        self.assertEqual(calcular_resumen(2026, hoy=HOY)["totales"]["ingresos"], D("100"))


class GastosTests(TestCase):
    def setUp(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 11), estancias="10000")
        CompraProveedor.objects.create(tipo="A", numero="1", fecha=date(2026, 5, 3), codigo_proveedor=7,
                                       proveedor="PROVEEDOR X", bruto=D("1000"), iva=D("100"), total=D("1100"))
        FacturaAcreedor.objects.create(fichero="luz", acreedor="LUZ SA", fecha=date(2026, 5, 5),
                                       cuenta="628000001", importe=D("500"))
        FacturaAcreedor.objects.create(fichero="obra", acreedor="OBRAS SL", fecha=date(2026, 5, 6),
                                       cuenta="212000001", importe=D("9000"))
        AsignacionDepartamento.objects.create(origen="proveedor", clave="7", nombre="PROVEEDOR X",
                                              departamento="Cocina")

    def test_beneficio_excluye_inversiones(self):
        t = calcular_resumen(2026, hoy=HOY)["totales"]
        self.assertEqual(t["proveedores"], D("1100"))   # total con IVA
        self.assertEqual(t["acreedores"], D("500"))
        self.assertEqual(t["inversiones"], D("9000"))
        self.assertEqual(t["beneficio"], D("10000") - D("1600"))

    def test_departamentos(self):
        deps = {d["nombre"]: d for d in calcular_resumen(2026, hoy=HOY)["departamentos"]}
        self.assertEqual(deps["Cocina"]["proveedores"], D("1100"))
        self.assertEqual(deps[SIN_ASIGNAR]["acreedores"], D("500"))   # «luz» sin asignar

    def test_filtro_de_mes(self):
        self.assertEqual(calcular_resumen(2026, mes=4, hoy=HOY)["totales"]["gastos"], D("0"))
        self.assertEqual(calcular_resumen(2026, mes=5, hoy=HOY)["totales"]["gastos"], D("1600"))

    def test_vista(self):
        resp = self.client.get(reverse("analisis:resumen"), {"anio": 2026, "mes": 5})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Análisis de beneficios")
        self.assertContains(resp, "datos-graficos")


class NormalizarDepartamentoTests(TestCase):
    def test_variantes(self):
        self.assertEqual(normalizar_departamento("COCINA "), "Cocina")
        self.assertEqual(normalizar_departamento("recepcion"), "Recepción")
        self.assertEqual(normalizar_departamento(""), SIN_ASIGNAR)
        self.assertEqual(normalizar_departamento("pisos"), "Pisos")


class ImportarAcreedoresTests(TestCase):
    def _csv(self, contenido):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig")
        f.write(contenido)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return Path(f.name)

    def test_importa_y_crea_asignaciones(self):
        ruta = self._csv("fichero;acreedor;fecha;documento;cuenta;importe;asiento;linea\n"
                         "luz;LUZ SA;05/05/2026;F1;628000001;1.210,50;10;1\n"
                         "luz;LUZ SA;06/05/2026;A1;628000001;-10,50;11;1\n")
        r = importar_acreedores(ruta)
        self.assertEqual(r["leidas"], 2)
        self.assertEqual(sum(f.importe for f in FacturaAcreedor.objects.all()), D("1200"))
        self.assertTrue(AsignacionDepartamento.objects.filter(origen="acreedor", clave="luz").exists())
        # Reimportar reemplaza, no duplica
        importar_acreedores(ruta)
        self.assertEqual(FacturaAcreedor.objects.count(), 2)

    def test_csv_antiguo_da_error_claro(self):
        ruta = self._csv("fichero;acreedor;fecha;documento;tipo;concepto;contrapartida;debe;haber;asiento;linea\n")
        with self.assertRaises(ErrorImportacion):
            importar_acreedores(ruta)
