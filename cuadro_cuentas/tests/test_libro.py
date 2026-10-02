import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from cuadro_cuentas.models import Asiento
from cuadro_cuentas.services import Linea, crear_asiento

from .utils import D, crear_plan_minimo, crear_subcuenta_directa, ejercicio


class LibroDiarioApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        crear_subcuenta_directa("60000001", "Compras cocina")
        crear_subcuenta_directa("47200001", "IVA soportado 10%")
        crear_subcuenta_directa("40000001", "Proveedor prueba")
        crear_subcuenta_directa("57200001", "Banco prueba")

    # ---- ayudas ----
    def url(self, nombre, *args):
        return reverse(f"cuadro_cuentas:{nombre}", args=args)

    def post(self, url, datos):
        return self.client.post(url, json.dumps(datos), content_type="application/json")

    def patch(self, url, datos):
        return self.client.patch(url, json.dumps(datos), content_type="application/json")

    def nuevo_asiento(self, fecha="2026-03-10"):
        return self.post(self.url("api_crear_asiento"), {"fecha": fecha, "concepto": "Compra prueba"}).json()

    def linea(self, asiento_id, **datos):
        return self.post(self.url("api_crear_apunte", asiento_id), datos)

    # ---- tests ----
    def test_la_pagina_del_libro_carga(self):
        crear_asiento(date(2026, 3, 1), "Compra", [
            Linea("60000001", debe=D("100")), Linea("40000001", haber=D("100"))])
        r = self.client.get(self.url("libro_diario"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["asientos"]), 1)

    def test_crear_asiento_y_lineas_con_contrapartida_automatica(self):
        a = self.nuevo_asiento()
        self.assertEqual((a["numero"], a["estado"]), (1, "borrador"))
        self.linea(a["id"], cuenta="60000001", debe="100,00")
        d = self.linea(a["id"], cuenta="40000001", haber="100").json()
        cp = {x["cuenta"]: x["contrapartida"] for x in d["apuntes"]}
        self.assertEqual(cp, {"60000001": "40000001", "40000001": "60000001"})
        self.assertEqual((d["debe"], d["haber"]), ("100.00", "100.00"))

    def test_importes_en_formato_espanol(self):
        a = self.nuevo_asiento()
        d = self.linea(a["id"], cuenta="60000001", debe="1.234,56").json()
        self.assertEqual(d["apuntes"][0]["debe"], "1234.56")

    def test_linea_en_cuenta_no_imputable_se_rechaza(self):
        a = self.nuevo_asiento()
        r = self.linea(a["id"], cuenta="600", debe="10")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no admite apuntes", r.json()["error"])

    def test_cuenta_inexistente_se_rechaza(self):
        a = self.nuevo_asiento()
        r = self.linea(a["id"], cuenta="60099999", debe="10")
        self.assertEqual(r.status_code, 400)

    def test_contabilizar_y_volver_a_borrador_al_descuadrar(self):
        a = self.nuevo_asiento()
        self.linea(a["id"], cuenta="60000001", debe="100")
        d = self.linea(a["id"], cuenta="40000001", haber="100").json()
        r = self.post(self.url("api_estado", a["id"]), {"estado": "contabilizado"})
        self.assertEqual(r.json()["estado"], "contabilizado")
        proveedor = next(x for x in d["apuntes"] if x["cuenta"] == "40000001")
        d = self.patch(self.url("api_apunte", proveedor["id"]), {"haber": "99"}).json()
        self.assertEqual(d["estado"], "borrador")

    def test_no_se_contabiliza_descuadrado(self):
        a = self.nuevo_asiento()
        self.linea(a["id"], cuenta="60000001", debe="100")
        self.linea(a["id"], cuenta="40000001", haber="90")
        r = self.post(self.url("api_estado", a["id"]), {"estado": "contabilizado"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Asiento.objects.get(pk=a["id"]).estado, "borrador")

    def test_escribir_en_el_debe_vacia_el_haber(self):
        a = self.nuevo_asiento()
        d = self.linea(a["id"], cuenta="40000001", haber="50").json()
        d = self.patch(self.url("api_apunte", d["apuntes"][0]["id"]), {"debe": "50"}).json()
        self.assertEqual((d["apuntes"][0]["debe"], d["apuntes"][0]["haber"]), ("50.00", "0.00"))

    def test_reordenar_lineas_del_mismo_lado(self):
        a = self.nuevo_asiento()
        self.linea(a["id"], cuenta="60000001", debe="100")
        self.linea(a["id"], cuenta="47200001", debe="10")
        d = self.linea(a["id"], cuenta="40000001", haber="110").json()
        compra, iva, proveedor = [x["id"] for x in d["apuntes"]]
        d = self.post(self.url("api_ordenar", a["id"]), {"ids": [iva, compra, proveedor]}).json()
        self.assertEqual([x["id"] for x in d["apuntes"]], [iva, compra, proveedor])

    def test_las_lineas_del_debe_van_antes_que_las_del_haber(self):
        a = self.nuevo_asiento()
        self.linea(a["id"], cuenta="40000001", haber="110")   # se escribe primero el Haber
        self.linea(a["id"], cuenta="60000001", debe="100")
        d = self.linea(a["id"], cuenta="47200001", debe="10").json()
        self.assertEqual([x["cuenta"] for x in d["apuntes"]], ["60000001", "47200001", "40000001"])
        self.assertEqual(d["clase"], "Compuesto")
    def test_reordenar_con_ids_ajenos_falla(self):
        a = self.nuevo_asiento()
        self.linea(a["id"], cuenta="60000001", debe="100")
        r = self.post(self.url("api_ordenar", a["id"]), {"ids": [999999]})
        self.assertEqual(r.status_code, 400)

    def test_no_se_borra_un_asiento_contabilizado(self):
        asiento = crear_asiento(date(2026, 3, 1), "Compra", [
            Linea("60000001", debe=D("100")), Linea("40000001", haber=D("100"))])
        r = self.client.delete(self.url("api_asiento", asiento.id))
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Asiento.objects.filter(pk=asiento.id).exists())

    def test_cambiar_fecha_a_otro_ejercicio_falla(self):
        a = self.nuevo_asiento()
        r = self.patch(self.url("api_asiento", a["id"]), {"fecha": "2027-01-02"})
        self.assertEqual(r.status_code, 400)

    def test_ejercicio_cerrado_no_admite_cambios(self):
        ejercicio(2025, cerrado=True)
        asiento = Asiento.objects.create(fecha=date(2025, 6, 1), concepto="Viejo")
        r = self.linea(asiento.id, cuenta="60000001", debe="10")
        self.assertEqual(r.status_code, 400)
        self.assertIn("cerrado", r.json()["error"])

    def test_resumen_con_saldos_acumulados(self):
        crear_asiento(date(2026, 3, 1), "Compra 1", [
            Linea("60000001", debe=D("100")), Linea("40000001", haber=D("100"))])
        segundo = crear_asiento(date(2026, 3, 5), "Compra 2", [
            Linea("60000001", debe=D("50")), Linea("40000001", haber=D("50"))])
        crear_asiento(date(2026, 3, 9), "Pago", [
            Linea("40000001", debe=D("150")), Linea("57200001", haber=D("150"))])
        d = self.client.get(self.url("api_resumen", segundo.id)).json()
        prov = next(c for c in d["cuentas"] if c["codigo"] == "40000001")
        self.assertEqual(prov["mov_haber"], "50.00")
        self.assertEqual(prov["saldo_tras"], "-150.00")       # tras el 2.º asiento
        self.assertEqual(prov["saldo_ejercicio"], "0.00")     # tras el pago