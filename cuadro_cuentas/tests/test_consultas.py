import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from cuadro_cuentas.consultas import buscar_apuntes, extracto, parse_importe, sumas_y_saldos
from cuadro_cuentas.forms_consultas import BuscadorForm
from cuadro_cuentas.models import Asiento, Ejercicio, Hoja
from cuadro_cuentas.services import Linea, crear_asiento

from .utils import D, crear_plan_minimo, crear_subcuenta_directa


class Base(TestCase):
    """Un mes de prueba: compra a proveedor, factura a cliente, cobro y pago."""

    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        for codigo, nombre in [("40000001", "Frutas Prueba SL"), ("40000002", "Bebidas Prueba SA"),
                               ("60000001", "Compras alimentos"), ("47200001", "IVA soportado"),
                               ("43000001", "Agencia Prueba"), ("70500001", "Alojamiento"),
                               ("47700001", "IVA repercutido"), ("57200001", "Banco Prueba"),
                               ("12900001", "Resultado del ejercicio")]:
            crear_subcuenta_directa(codigo, nombre)
        cls.bancos = Hoja.objects.create(nombre="Bancos", color=Hoja.Color.VERDE)
        cls.compras = Hoja.objects.create(nombre="Compras")
        cls.compra = crear_asiento(date(2026, 3, 2), "Factura Frutas F-1", [
            Linea("60000001", debe=D("100")), Linea("47200001", debe=D("10")), Linea("40000001", haber=D("110"))],
            referencia_origen="F-1", hoja=cls.compras)
        cls.compra2 = crear_asiento(date(2026, 3, 5), "Factura Bebidas B-77", [
            Linea("60000001", debe=D("1234.56")), Linea("40000002", haber=D("1234.56"))],
            referencia_origen="B-77", hoja=cls.compras)
        cls.venta = crear_asiento(date(2026, 4, 1), "Factura agencia", [
            Linea("43000001", debe=D("1100")), Linea("70500001", haber=D("1000")), Linea("47700001", haber=D("100"))])
        cls.cobro = crear_asiento(date(2026, 5, 2), "Cobro agencia", [
            Linea("57200001", debe=D("1100")), Linea("43000001", haber=D("1100"))], hoja=cls.bancos)
        cls.pago = crear_asiento(date(2026, 5, 3), "Pago Frutas", [
            Linea("40000001", debe=D("110")), Linea("57200001", haber=D("110"))], hoja=cls.bancos)
        cls.ejercicio = Ejercicio.objects.get(anio=2026)


# ===================== Hojas =====================

class HojasTests(Base):
    def patch(self, asiento, datos):
        return self.client.patch(reverse("cuadro_cuentas:api_asiento", args=[asiento.id]),
                                 json.dumps(datos), content_type="application/json")

    def test_mover_un_asiento_de_hoja(self):
        r = self.patch(self.venta, {"hoja": self.bancos.id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["hoja"], self.bancos.id)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.hoja, self.bancos)

    def test_quitar_de_la_hoja(self):
        self.patch(self.cobro, {"hoja": ""})
        self.cobro.refresh_from_db()
        self.assertIsNone(self.cobro.hoja)

    def test_hoja_inexistente(self):
        r = self.patch(self.cobro, {"hoja": 99999})
        self.assertEqual(r.status_code, 400)

    def test_cambiar_de_hoja_no_toca_ni_numero_ni_estado(self):
        antes = (self.compra.numero, self.compra.estado)
        self.patch(self.compra, {"hoja": self.bancos.id})
        self.compra.refresh_from_db()
        self.assertEqual((self.compra.numero, self.compra.estado), antes)

    def test_el_libro_filtra_por_hoja(self):
        url = reverse("cuadro_cuentas:libro_diario")
        ids = lambda r: {a["id"] for a in r.context["asientos"]}  # noqa: E731
        self.assertEqual(ids(self.client.get(url, {"hoja": self.bancos.id, "mes": ""})), {self.cobro.id, self.pago.id})
        self.assertEqual(ids(self.client.get(url, {"hoja": "sin", "mes": ""})), {self.venta.id})
        self.assertEqual(len(ids(self.client.get(url, {"mes": ""}))), 5)

    def test_asiento_nuevo_se_crea_en_la_hoja_activa(self):
        r = self.client.post(reverse("cuadro_cuentas:api_modelo_crear", args=["libre"]),
                             json.dumps({"fecha": "2026-06-01", "datos": {"tipo": "operacion"},
                                         "hoja": self.bancos.id}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Asiento.objects.get(pk=r.json()["id"]).hoja, self.bancos)

    def test_crear_editar_y_borrar_hojas(self):
        url = reverse("cuadro_cuentas:hojas")
        self.client.post(url, {"accion": "crear", "nueva-nombre": "Revisar", "nueva-color": Hoja.Color.AMBAR})
        revisar = Hoja.objects.get(nombre="Revisar")
        self.client.post(url, {"accion": "editar", "id": revisar.id, f"h{revisar.id}-nombre": "Pendiente",
                               f"h{revisar.id}-color": Hoja.Color.GRIS})
        revisar.refresh_from_db()
        self.assertEqual((revisar.nombre, revisar.color), ("Pendiente", Hoja.Color.GRIS))
        # borrar una hoja deja sus asientos sin hoja, no los borra
        self.client.post(url, {"accion": "borrar", "id": self.bancos.id})
        self.assertFalse(Hoja.objects.filter(pk=self.bancos.id).exists())
        self.cobro.refresh_from_db()
        self.assertIsNone(self.cobro.hoja)
        self.assertEqual(Asiento.objects.count(), 5)

    def test_nombre_de_hoja_repetido(self):
        r = self.client.post(reverse("cuadro_cuentas:hojas"), {"accion": "crear", "nueva-nombre": "Bancos",
                                                                "nueva-color": Hoja.Color.AZUL})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["nueva"].errors)

    def test_pagina_de_hojas(self):
        r = self.client.get(reverse("cuadro_cuentas:hojas"))
        self.assertContains(r, "Bancos")
        self.assertContains(r, "1 asiento sin hoja")


# ===================== Extractos =====================

class ExtractoTests(Base):
    def test_extracto_de_una_subcuenta_con_saldo_acumulado(self):
        e = extracto(self.ejercicio, "57200001")
        self.assertFalse(e.varias)
        self.assertEqual([l["saldo"] for l in e.lineas], [D("1100.00"), D("990.00")])
        self.assertEqual((e.total_debe, e.total_haber, e.saldo_final), (D("1100.00"), D("110.00"), D("990.00")))

    def test_extracto_de_una_cuenta_agrupa_sus_subcuentas(self):
        e = extracto(self.ejercicio, "400")
        self.assertTrue(e.varias)
        self.assertEqual(len(e.lineas), 3)
        self.assertEqual(e.saldo_final, D("-1234.56"))   # acreedor: se debe a Bebidas

    def test_saldo_inicial_con_fecha_desde(self):
        e = extracto(self.ejercicio, "57200001", desde=date(2026, 5, 3))
        self.assertEqual(e.saldo_inicial, D("1100.00"))
        self.assertEqual(len(e.lineas), 1)
        self.assertEqual(e.saldo_final, D("990.00"))

    def test_fecha_hasta(self):
        e = extracto(self.ejercicio, "57200001", hasta=date(2026, 5, 2))
        self.assertEqual(e.saldo_final, D("1100.00"))

    def test_borradores_y_solo_contabilizados(self):
        crear_asiento(date(2026, 6, 1), "Borrador", [
            Linea("60000001", debe=D("5")), Linea("57200001", haber=D("5"))], contabilizar=False)
        self.assertEqual(extracto(self.ejercicio, "57200001").saldo_final, D("985.00"))
        self.assertEqual(extracto(self.ejercicio, "57200001", solo_contabilizados=True).saldo_final, D("990.00"))

    def test_el_cierre_se_excluye_salvo_que_se_pida(self):
        crear_asiento(date(2026, 12, 31), "Cierre", [
            Linea("40000002", debe=D("1234.56")), Linea("57200001", haber=D("990")),
            Linea("60000001", haber=D("244.56"))], tipo=Asiento.Tipo.CIERRE)
        self.assertEqual(extracto(self.ejercicio, "57200001").saldo_final, D("990.00"))
        self.assertEqual(extracto(self.ejercicio, "57200001", incluir_cierre=True).saldo_final, D("0.00"))

    def test_sumas_y_saldos(self):
        filas, totales = sumas_y_saldos(self.ejercicio)
        por_cuenta = {f["codigo"]: f for f in filas}
        self.assertEqual(por_cuenta["57200001"]["saldo"], D("990.00"))
        self.assertEqual(por_cuenta["43000001"]["saldo"], D("0.00"))
        self.assertEqual(totales["debe"], totales["haber"])
        self.assertEqual(totales["deudores"], totales["acreedores"])

    def test_sumas_y_saldos_filtradas_por_prefijo(self):
        filas, _ = sumas_y_saldos(self.ejercicio, "40")
        self.assertEqual([f["codigo"] for f in filas], ["40000001", "40000002"])

    def test_pagina_de_extractos(self):
        url = reverse("cuadro_cuentas:extractos")
        r = self.client.get(url, {"ejercicio": 2026})
        self.assertContains(r, "Saldo de cada cuenta")
        self.assertContains(r, "Banco Prueba")
        r = self.client.get(url, {"ejercicio": 2026, "cuenta": "57200001"})
        self.assertContains(r, "Saldo final")
        self.assertContains(r, "990,00")
        self.assertContains(r, reverse("cuadro_cuentas:asiento", args=[self.cobro.id]))   # abre el asiento


# ===================== Buscador =====================

class BuscadorTests(Base):
    def buscar(self, **criterios):
        form = BuscadorForm(criterios)
        self.assertTrue(form.is_valid(), form.errors)
        return list(buscar_apuntes(form.cleaned_data))

    def cuentas(self, apuntes):
        return sorted(a.cuenta.codigo for a in apuntes)

    def test_parse_importe(self):
        self.assertEqual(parse_importe("1.234,56"), D("1234.56"))
        self.assertEqual(parse_importe("1234.56 €"), D("1234.56"))
        self.assertIsNone(parse_importe(""))
        with self.assertRaises(ValueError):
            parse_importe("abc")

    def test_por_importe_exacto_en_formato_espanol(self):
        self.assertEqual(self.cuentas(self.buscar(importe="1.234,56")), ["40000002", "60000001"])

    def test_por_importe_y_lado(self):
        self.assertEqual(self.cuentas(self.buscar(importe="110", lado="haber")), ["40000001", "57200001"])
        self.assertEqual(self.cuentas(self.buscar(importe="110", lado="debe")), ["40000001"])

    def test_importe_aproximado(self):
        self.assertEqual(self.buscar(importe="1230"), [])
        self.assertEqual(len(self.buscar(importe="1230", aproximado="on")), 2)

    def test_rango_de_importes(self):
        resultado = self.buscar(importe_min="100", importe_max="110")
        self.assertTrue(all(D("100") <= a.debe + a.haber <= D("110") for a in resultado))
        self.assertEqual(len(resultado), 5)

    def test_por_texto_en_concepto_documento_o_cuenta(self):
        self.assertEqual(len(self.buscar(texto="B-77")), 2)          # nº de factura
        self.assertEqual(len(self.buscar(texto="agencia")), 5)       # concepto y nombre de cuenta
        self.assertEqual(self.cuentas(self.buscar(texto="frutas prueba")), ["40000001", "40000001"])

    def test_por_cuenta_codigo_o_nombre(self):
        self.assertEqual(len(self.buscar(cuenta="400")), 3)
        self.assertEqual(len(self.buscar(cuenta="Banco")), 2)

    def test_por_fechas_hoja_y_estado(self):
        self.assertEqual(len(self.buscar(desde="2026-05-01")), 4)
        self.assertEqual(len(self.buscar(hoja=str(self.bancos.id))), 4)
        self.assertEqual(len(self.buscar(hoja="sin")), 3)
        self.assertEqual(len(self.buscar(estado="borrador")), 0)

    def test_criterios_combinados(self):
        self.assertEqual(self.cuentas(self.buscar(cuenta="572", lado="haber", importe="110")), ["57200001"])

    def test_errores_del_formulario(self):
        self.assertFalse(BuscadorForm({"importe": "doce"}).is_valid())
        self.assertFalse(BuscadorForm({"importe_min": "200", "importe_max": "100"}).is_valid())

    def test_pagina_del_buscador(self):
        url = reverse("cuadro_cuentas:buscador")
        r = self.client.get(url)
        self.assertNotContains(r, "encontrado")
        r = self.client.get(url, {"importe": "1.234,56"})
        self.assertContains(r, "2 apuntes encontrados")
        self.assertContains(r, reverse("cuadro_cuentas:asiento", args=[self.compra2.id]))
