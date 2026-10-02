from datetime import date

from django.test import TestCase
from django.urls import reverse

from cuadro_cuentas.models import CuentaContable
from cuadro_cuentas.services import Linea, crear_asiento

from .utils import CIF_ACREEDOR, CIF_PROVEEDOR, D, crear_plan_minimo, crear_subcuenta_directa

URL = reverse("cuadro_cuentas:plan_cuentas")


class PlanCuentasPaginaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()

    def test_la_pagina_carga_con_arbol_y_tipos(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="c400"')
        self.assertContains(r, 'id="datos-tipos"')

    def test_el_tipo_de_la_url_queda_seleccionado(self):
        r = self.client.get(URL + "?tipo=banco")
        self.assertRegex(r.content.decode(), r'value="banco"\s+checked')

    def test_los_tipos_solo_ofrecen_cuentas_que_existen(self):
        tipos = {t["clave"]: t for t in self.client.get(URL).context["tipos"]}
        self.assertEqual([b["codigo"] for b in tipos["proveedor"]["bases"]], ["400"])  # 403-405 no están en el plan mínimo
        self.assertIn("76200", [b["codigo"] for b in tipos["otra"]["bases"]])
        self.assertNotIn("40", [b["codigo"] for b in tipos["otra"]["bases"]])


class AltaDesdeFormularioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()

    def alta(self, **datos):
        return self.client.post(URL, datos)

    def test_alta_de_proveedor(self):
        r = self.alta(tipo="proveedor", base="400", nombre="Frutas Prueba SL", nif=CIF_PROVEEDOR)
        self.assertRedirects(r, URL + "?tipo=proveedor#c40000001", fetch_redirect_response=False)
        sub = CuentaContable.objects.get(codigo="40000001")
        self.assertEqual((sub.nombre, sub.nif), ("Frutas Prueba SL", CIF_PROVEEDOR))

    def test_proveedor_sin_nif_no_se_crea(self):
        r = self.alta(tipo="proveedor", base="400", nombre="Sin NIF")
        self.assertEqual(r.status_code, 200)
        self.assertFormError(r.context["form"], "nif", "El NIF/CIF es obligatorio para «Proveedor».")
        self.assertFalse(CuentaContable.objects.filter(codigo="40000001").exists())

    def test_nif_incorrecto_se_muestra_en_el_campo(self):
        r = self.alta(tipo="proveedor", base="400", nombre="Mal", nif="A12345675")
        self.assertIn("nif", r.context["form"].errors)

    def test_cuenta_que_no_corresponde_al_tipo(self):
        r = self.alta(tipo="proveedor", base="410", nombre="Mal", nif=CIF_PROVEEDOR)
        self.assertIn("base", r.context["form"].errors)

    def test_banco_no_pide_nif(self):
        self.alta(tipo="banco", base="572", nombre="Banco Prueba ****0001")
        self.assertTrue(CuentaContable.objects.filter(codigo="57200001").exists())

    def test_otra_cuenta_admite_bases_de_5_digitos(self):
        self.alta(tipo="otra", base="76200", nombre="Préstamo a filial")
        self.assertTrue(CuentaContable.objects.filter(codigo="76200001").exists())

    def test_error_del_servicio_se_muestra_como_error_general(self):
        self.alta(tipo="acreedor", base="410", nombre="Primero", nif=CIF_ACREEDOR)
        r = self.alta(tipo="acreedor", base="410", nombre="Repetido", nif=CIF_ACREEDOR)
        self.assertIn("Ya existe", " ".join(r.context["form"].non_field_errors()))


class ApiSiguienteCodigoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()

    def test_devuelve_codigo_resumen_y_ubicacion(self):
        d = self.client.get(reverse("cuadro_cuentas:api_siguiente_codigo"), {"base": "400"}).json()
        self.assertEqual(d["codigo"], "40000001")
        self.assertIn("suministradores", d["resumen"])
        self.assertEqual(d["ubicacion"], "Figurará en el pasivo corriente del balance.")

    def test_base_no_valida(self):
        r = self.client.get(reverse("cuadro_cuentas:api_siguiente_codigo"), {"base": "40"})
        self.assertEqual(r.status_code, 400)


class ApiFichaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        crear_subcuenta_directa("60000001", "Compras cocina")
        crear_subcuenta_directa("40000001", "Proveedor prueba", nif=CIF_PROVEEDOR)
        crear_asiento(date(2026, 3, 1), "Compra", [
            Linea("60000001", debe=D("100")), Linea("40000001", haber=D("100"))])

    def ficha(self, codigo):
        return self.client.get(reverse("cuadro_cuentas:api_ficha_cuenta", args=[codigo]))

    def test_ficha_de_subcuenta_hereda_definicion_y_ubicacion(self):
        d = self.ficha("40000001").json()
        self.assertEqual([a["codigo"] for a in d["ruta"]], ["4", "40", "400"])
        self.assertEqual(d["definicion_de"], "400")
        self.assertEqual(d["ubicacion"], "Figurará en el pasivo corriente del balance.")
        self.assertEqual((d["haber"], d["saldo"]), ("100.00", "-100.00"))
        self.assertFalse(d["admite_subcuentas"])

    def test_ficha_de_cuenta_suma_sus_subcuentas(self):
        d = self.ficha("6").json()
        self.assertEqual((d["debe"], d["saldo"]), ("100.00", "100.00"))
        self.assertFalse(d["imputable"])

    def test_ficha_lista_las_cuentas_que_contiene(self):
        d = self.ficha("400").json()
        self.assertEqual([h["codigo"] for h in d["hijas"]], ["4000", "40000001"])
        self.assertTrue(d["admite_subcuentas"])

    def test_cuenta_inexistente(self):
        self.assertEqual(self.ficha("9999").status_code, 404)