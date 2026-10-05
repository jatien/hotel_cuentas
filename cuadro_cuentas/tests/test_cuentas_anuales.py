from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from cuadro_cuentas.cuentas_anuales import calcular_cuentas_anuales, evaluar_abreviado
from cuadro_cuentas.modelos_asiento import crear_desde_modelo
from cuadro_cuentas.models import CuentaContable, DatosEmpresa, Ejercicio
from cuadro_cuentas.services import Linea, crear_asiento

from .test_modelos_asiento import SUBCUENTAS
from .utils import D, crear_plan_minimo, crear_subcuenta_directa

FIN = date(2026, 12, 31)


def fila(estado, ref):
    for _, _, _, _, filas in estado.secciones:
        for f in filas:
            if f.ref == ref:
                return f
    raise KeyError(ref)


def importes(estado, ref):
    f = fila(estado, ref)
    return (f.actual, f.anterior)


def totales(balance):
    """(total activo, total patrimonio neto y pasivo) del ejercicio."""
    return balance.secciones[0][2], balance.secciones[1][2]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        for codigo, nombre, base in SUBCUENTAS:
            crear_subcuenta_directa(codigo, nombre, base=base)

    def ejercicio_completo(self, anio=2026, base_venta="1000"):
        crear_desde_modelo("factura_proveedor", date(anio, 3, 2), {
            "proveedor": "40000001", "compra": "60000001", "base": "100", "iva": "10",
            "cuenta_iva": "47200001"})
        crear_desde_modelo("factura_cliente", date(anio, 4, 1), {
            "cliente": "43000001", "ingreso": "70500001", "base": base_venta, "iva": "10",
            "cuenta_iva": "47700001"})
        cobrado = str(Decimal(base_venta) * Decimal("1.1"))
        crear_desde_modelo("cobro", date(anio, 5, 2), {"tesoreria": "57200001", "cliente": "43000001",
                                                       "importe": cobrado})
        crear_desde_modelo("pago", date(anio, 5, 3), {"tercero": "40000001", "tesoreria": "57200001",
                                                      "importe": "110"})
        return Ejercicio.objects.get(anio=anio)


class BalanceYPerdidasYGananciasTests(Base):
    def test_balance_cuadra_y_recoge_el_resultado_antes_de_regularizar(self):
        ejercicio = self.ejercicio_completo()
        ca = calcular_cuentas_anuales(ejercicio, "normal")
        b = ca["balance"]
        self.assertEqual(importes(b, "B.VII.1")[0], D("990.00"))      # banco
        self.assertEqual(importes(b, "B.III.6")[0], D("10.00"))       # IVA soportado
        self.assertEqual(importes(b, "PC.V.6")[0], D("100.00"))       # IVA repercutido
        self.assertEqual(importes(b, "PN.1.VII")[0], D("900.00"))     # resultado aún en los grupos 6 y 7
        self.assertEqual(totales(b), (D("1000.00"), D("1000.00")))
        self.assertEqual(b.sin_clasificar, [])

    def test_perdidas_y_ganancias_normal_y_abreviada(self):
        ejercicio = self.ejercicio_completo()
        normal = calcular_cuentas_anuales(ejercicio, "normal")["pyg"]
        self.assertEqual(importes(normal, "1b")[0], D("1000.00"))
        self.assertEqual(importes(normal, "4a")[0], D("-100.00"))
        self.assertEqual(importes(normal, "A1")[0], D("900.00"))
        self.assertEqual(importes(normal, "A5")[0], D("900.00"))
        abreviada = calcular_cuentas_anuales(ejercicio, "abreviado")["pyg"]
        self.assertEqual(importes(abreviada, "1")[0], D("1000.00"))
        self.assertEqual(importes(abreviada, "4")[0], D("-100.00"))
        self.assertEqual(importes(abreviada, "D")[0], D("900.00"))

    def test_regularizacion_y_cierre_no_cambian_las_cuentas_anuales(self):
        ejercicio = self.ejercicio_completo()
        crear_desde_modelo("regularizacion", FIN, {"resultado": "12900001"})
        crear_desde_modelo("cierre", FIN, {})
        ca = calcular_cuentas_anuales(ejercicio, "normal")
        self.assertEqual(importes(ca["balance"], "PN.1.VII")[0], D("900.00"))
        self.assertEqual(totales(ca["balance"]), (D("1000.00"), D("1000.00")))
        self.assertEqual(importes(ca["pyg"], "A5")[0], D("900.00"))

    def test_columna_del_ejercicio_anterior(self):
        self.ejercicio_completo(2026)
        crear_desde_modelo("regularizacion", FIN, {"resultado": "12900001"})
        crear_desde_modelo("cierre", FIN, {})
        crear_desde_modelo("apertura", date(2027, 1, 1), {})
        ejercicio = self.ejercicio_completo(2027, base_venta="2000")
        ca = calcular_cuentas_anuales(ejercicio, "normal")
        self.assertEqual(importes(ca["pyg"], "1b"), (D("2000.00"), D("1000.00")))
        # el resultado de 2026 sigue en la 129 hasta que se aplique; se suma el de 2027
        self.assertEqual(importes(ca["balance"], "PN.1.VII"), (D("2800.00"), D("900.00")))
        b = ca["balance"]
        self.assertEqual(totales(b)[0], totales(b)[1])

    def test_los_borradores_no_cuentan(self):
        ejercicio = self.ejercicio_completo()
        crear_asiento(date(2026, 6, 1), "Borrador", [
            Linea("60000001", debe=D("50")), Linea("57200001", haber=D("50"))], contabilizar=False)
        ca = calcular_cuentas_anuales(ejercicio, "normal")
        self.assertEqual(importes(ca["pyg"], "A5")[0], D("900.00"))
        self.assertEqual(ca["borradores"], 1)

    def test_partidas_vacias_ocultas_y_totales_siempre_visibles(self):
        b = calcular_cuentas_anuales(self.ejercicio_completo(), "normal")["balance"]
        self.assertFalse(fila(b, "A.I.1").visible)
        self.assertTrue(fila(b, "A").visible)
        self.assertTrue(fila(b, "B.VII.1").visible)

    def test_detalle_de_subcuentas_de_cada_partida(self):
        b = calcular_cuentas_anuales(self.ejercicio_completo(), "normal")["balance"]
        self.assertEqual(fila(b, "B.VII.1").detalle, [("57200001", "Banco Prueba", D("990.00"), D("0"))])


class CuentasEspecialesTests(Base):
    def test_cuenta_mixta_va_al_activo_o_al_pasivo_segun_su_saldo(self):
        CuentaContable.objects.create(codigo="55", nombre="Otras cuentas no bancarias", imputable=False)
        CuentaContable.objects.create(codigo="551", nombre="Cuenta corriente con socios y administradores",
                                      imputable=False)
        crear_subcuenta_directa("55100001", "Socio A")
        crear_subcuenta_directa("55100002", "Socio B")
        crear_asiento(date(2026, 2, 1), "Préstamo del socio A", [
            Linea("57200001", debe=D("300")), Linea("55100001", haber=D("300"))])
        crear_asiento(date(2026, 2, 2), "Préstamo al socio B", [
            Linea("55100002", debe=D("80")), Linea("57200001", haber=D("80"))])
        b = calcular_cuentas_anuales(Ejercicio.objects.get(anio=2026), "normal")["balance"]
        self.assertEqual(importes(b, "PC.III.5")[0], D("300.00"))
        self.assertEqual(importes(b, "B.V.5")[0], D("80.00"))
        self.assertEqual(totales(b), (D("300.00"), D("300.00")))

    def test_cuentas_que_no_encajan_en_el_modelo_se_avisan(self):
        CuentaContable.objects.create(codigo="473", nombre="Hacienda Pública, retenciones y pagos a cuenta",
                                      imputable=False)
        crear_subcuenta_directa("47300001", "Pagos a cuenta del impuesto")
        crear_asiento(date(2026, 10, 20), "Pago fraccionado", [
            Linea("47300001", debe=D("50")), Linea("57200001", haber=D("50"))])
        b = calcular_cuentas_anuales(Ejercicio.objects.get(anio=2026), "normal")["balance"]
        self.assertEqual(b.sin_clasificar, [("47300001", "Pagos a cuenta del impuesto", D("50.00"))])

    def test_gastos_excepcionales_van_a_otros_resultados(self):
        CuentaContable.objects.create(codigo="67", nombre="Pérdidas procedentes de activos no corrientes",
                                      imputable=False)
        CuentaContable.objects.create(codigo="678", nombre="Gastos excepcionales", imputable=False)
        crear_subcuenta_directa("67800001", "Multas")
        crear_asiento(date(2026, 7, 1), "Multa", [
            Linea("67800001", debe=D("40")), Linea("57200001", haber=D("40"))])
        pyg = calcular_cuentas_anuales(Ejercicio.objects.get(anio=2026), "normal")["pyg"]
        self.assertEqual(importes(pyg, "OR")[0], D("-40.00"))
        self.assertEqual(importes(pyg, "A1")[0], D("-40.00"))


class AbreviadoTests(Base):
    def test_empresa_pequena_puede_presentar_todo_abreviado(self):
        ejercicio = self.ejercicio_completo()
        ejercicio.trabajadores_medios = D("12")
        ejercicio.save()
        r = evaluar_abreviado(ejercicio)
        self.assertTrue(r["balance"]["puede"])
        self.assertTrue(r["pyg"]["puede"])
        self.assertIsNone(r["balance"]["anterior"])

    def test_cifra_y_activo_grandes_impiden_el_balance_abreviado(self):
        ejercicio = self.ejercicio_completo(base_venta="9000000")
        ejercicio.trabajadores_medios = D("12")
        ejercicio.save()
        r = evaluar_abreviado(ejercicio)
        self.assertFalse(r["balance"]["puede"])   # activo > 4 M y cifra > 8 M
        self.assertTrue(r["pyg"]["puede"])        # pero por debajo de 11,4 M y 22,8 M


class VistaCuentasAnualesTests(Base):
    def test_la_pagina_muestra_los_estados(self):
        self.ejercicio_completo()
        DatosEmpresa.objects.create(denominacion="Hotel Prueba SL", nif="B65410011")
        r = self.client.get(reverse("cuadro_cuentas:cuentas_anuales"), {"ejercicio": 2026, "modelo": "normal"})
        self.assertEqual(r.status_code, 200)
        for texto in ("Hotel Prueba SL", "TOTAL ACTIVO (A + B)", "A.5) RESULTADO DEL EJERCICIO",
                      "Estado de ingresos y gastos reconocidos", "990,00"):
            self.assertContains(r, texto)

    def test_modelo_abreviado(self):
        self.ejercicio_completo()
        r = self.client.get(reverse("cuadro_cuentas:cuentas_anuales"), {"ejercicio": 2026, "modelo": "abreviado"})
        self.assertContains(r, "Balance abreviado")
        self.assertContains(r, "D) RESULTADO DEL EJERCICIO")

    def test_guardar_trabajadores_y_fecha_de_formulacion(self):
        self.ejercicio_completo()
        url = reverse("cuadro_cuentas:cuentas_anuales")
        r = self.client.post(f"{url}?ejercicio=2026", {"trabajadores_medios": "14,5",
                                                       "fecha_formulacion": "2027-03-15"})
        self.assertEqual(r.status_code, 302)
        ejercicio = Ejercicio.objects.get(anio=2026)
        self.assertEqual(ejercicio.trabajadores_medios, D("14.5"))
        self.assertEqual(ejercicio.fecha_formulacion, date(2027, 3, 15))

    def test_sin_ejercicios(self):
        r = self.client.get(reverse("cuadro_cuentas:cuentas_anuales"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Todavía no hay ejercicios")