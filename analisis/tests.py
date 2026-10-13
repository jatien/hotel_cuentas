"""Tests de la app analisis. Todos los datos son ficticios."""

import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from .importacion import ErrorImportacion, importar_acreedores, importar_extras
from .models import AsignacionDepartamento, ApunteIva, CompraProveedor, ExtraPersonal, FacturaAcreedor, Reserva
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
        r = calcular_resumen(2026, hoy=HOY, con_iva=True)
        self.assertEqual(r["mensual"]["estancias"][3], D("100"))
        self.assertEqual(r["mensual"]["estancias"][4], D("300"))
        self.assertEqual(r["totales"]["noches_hab"], 4)

    def test_las_tasas_se_suman_al_ingreso(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 3), estancias="200", tasas="8", extras="12")
        t = calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]
        self.assertEqual(t["ingresos"], D("220"))
        self.assertEqual(t["tasas"], D("8"))
        self.assertEqual(t["adr"], D("100"))      # el ADR solo usa estancias

    def test_sin_iva_quita_el_10_por_ciento_salvo_a_las_tasas(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 3), estancias="220", tasas="8", extras="11")
        t = calcular_resumen(2026, hoy=HOY)["totales"]
        self.assertAlmostEqual(float(t["estancias"]), 200, places=6)
        self.assertAlmostEqual(float(t["extras"]), 10, places=6)
        self.assertEqual(t["tasas"], D("8"))

    def test_noches_futuras_son_previsto(self):
        reserva(1, date(2026, 6, 14), date(2026, 6, 18), estancias="400")   # 14 y 15 realizadas
        sin = calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]
        con = calcular_resumen(2026, incluir_previsto=True, hoy=HOY, con_iva=True)["totales"]
        self.assertEqual(sin["ingresos"], D("200"))
        self.assertEqual(sin["previsto"], D("200"))
        self.assertEqual(con["ingresos"], D("400"))

    def test_reserva_que_cruza_el_ano_solo_cuenta_sus_noches(self):
        reserva(1, date(2025, 12, 30), date(2026, 1, 2), estancias="300")  # 1 de 3 noches en 2026
        self.assertEqual(calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]["ingresos"], D("100"))


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
        t = calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]
        self.assertEqual(t["proveedores"], D("1100"))   # total con IVA
        self.assertEqual(t["acreedores"], D("500"))
        self.assertEqual(t["inversiones"], D("9000"))
        self.assertEqual(t["beneficio"], D("10000") - D("1600"))

    def test_sin_iva_usa_la_base_y_el_libro_de_iva(self):
        # IVA de la factura de luz en el libro de soportado (mismo asiento y documento)
        FacturaAcreedor.objects.filter(fichero="luz").update(asiento=10, documento="L1", importe=D("121"))
        ApunteIva.objects.create(libro="soportado", fecha=date(2026, 5, 5), documento="L1", asiento=10,
                                 concepto="S/FRA LUZ SA", cuenta="410000001", cuota=D("21"))
        t = calcular_resumen(2026, hoy=HOY)["totales"]
        self.assertEqual(t["proveedores"], D("1000"))   # base del diario
        self.assertEqual(t["acreedores"], D("100"))     # 121 - 21

    def test_inversion_del_sujeto_pasivo_no_descuenta_iva(self):
        FacturaAcreedor.objects.filter(fichero="luz").update(asiento=11, documento="B1")
        for libro in ("soportado", "repercutido"):
            ApunteIva.objects.create(libro=libro, fecha=date(2026, 5, 5), documento="B1", asiento=11,
                                     concepto="S/FRA EXTRANJERA", cuenta="410000002", cuota=D("105"))
        self.assertEqual(calcular_resumen(2026, hoy=HOY)["totales"]["acreedores"], D("500"))

    def test_departamentos(self):
        deps = {d["nombre"]: d for d in calcular_resumen(2026, hoy=HOY)["departamentos"]}
        self.assertEqual(deps["Cocina"]["proveedores"], D("1000"))   # base sin IVA
        self.assertEqual(deps[SIN_ASIGNAR]["acreedores"], D("500"))   # «luz» sin asignar

    def test_filtro_de_mes(self):
        self.assertEqual(calcular_resumen(2026, mes=4, hoy=HOY)["totales"]["gastos"], D("0"))
        self.assertEqual(calcular_resumen(2026, mes=5, hoy=HOY)["totales"]["gastos"], D("1500"))  # base 1000 + 500

    def test_bar_y_comedor_trimestral_se_reparte(self):
        ApunteIva.objects.create(libro="repercutido", fecha=date(2026, 6, 30), documento="3006",
                                 concepto="N/FRA BAR", cuenta="430000000", cuota=D("300"))
        r = calcular_resumen(2026, hoy=HOY)
        self.assertEqual(r["totales"]["bar"], D("3000"))          # base = cuota / 10 %
        self.assertEqual(r["mensual"]["bar"][3], D("1000"))        # abril, mayo y junio
        self.assertEqual(r["mensual"]["bar"][5], D("1000"))

    def test_extras_de_personal_por_departamento(self):
        ExtraPersonal.objects.create(anio=2026, mes=5, persona="Persona A", importe=D("400"))
        AsignacionDepartamento.objects.create(origen="personal", clave="Persona A", nombre="Persona A",
                                              departamento="Bar")
        r = calcular_resumen(2026, hoy=HOY)
        self.assertEqual(r["totales"]["extras_personal"], D("400"))
        deps = {d["nombre"]: d for d in r["departamentos"]}
        self.assertEqual(deps["Bar"]["nominas"], D("400"))

    def test_vista(self):
        resp = self.client.get(reverse("analisis:resumen"), {"anio": 2026, "mes": 5, "iva": "con"})
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


class ImportarExtrasTests(TestCase):
    def test_importa_y_propone_departamento(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig")
        f.write("anio;mes;persona;departamento;importe\n2026;5;Persona A;Cocina;400,00\n2026;6;Persona A;Cocina;250,50\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        r = importar_extras(Path(f.name))
        self.assertEqual(r["leidas"], 2)
        a = AsignacionDepartamento.objects.get(origen="personal", clave="Persona A")
        self.assertEqual(a.departamento, "Cocina")
        # Un cambio hecho en el admin no se pisa al reimportar
        a.departamento = "Bar"
        a.save()
        importar_extras(Path(f.name))
        a.refresh_from_db()
        self.assertEqual(a.departamento, "Bar")
