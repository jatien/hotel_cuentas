import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from copilot_pms.llm.fake import FakeChat, llamar
from copilot_pms.models import Conversacion
from copilot_pms.tests.utils import ContabilidadBase

FAKE = override_settings(COPILOT_PMS={"PROVIDER": "fake", "BACKOFF_SECONDS": 0})


def proveedor(*respuestas):
    return mock.patch("copilot_pms.agent.get_provider", return_value=FakeChat(list(respuestas)))


@FAKE
class VistasTests(ContabilidadBase):
    def test_inicio(self):
        r = self.client.get(reverse("copilot_pms:index"))
        self.assertContains(r, "PMS Copilot")
        self.assertContains(r, "Aún no conectado")

    def test_preguntar_desde_el_formulario(self):
        with proveedor(llamar("saldo_cuenta", '{"cuenta": "572"}'), "Saldo deudor de 1.500,00 €."):
            r = self.client.post(reverse("copilot_pms:preguntar"), {"pregunta": "¿Saldo del banco?"})
        conv = Conversacion.objects.get()
        self.assertRedirects(r, reverse("copilot_pms:conversacion", args=[conv.public_id]) + "#ultimo",
                             fetch_redirect_response=False)
        pagina = self.client.get(reverse("copilot_pms:conversacion", args=[conv.public_id]))
        self.assertContains(pagina, "Saldo deudor de 1.500,00 €.")
        self.assertContains(pagina, "Datos consultados (1)")
        self.assertContains(pagina, "/contabilidad/extractos/?")

    def test_api(self):
        with proveedor(llamar("saldo_cuenta", '{"cuenta": "572"}'), "Listo."):
            r = self.client.post(reverse("copilot_pms:api_preguntar"), json.dumps({"pregunta": "Saldo 572"}),
                                 content_type="application/json")
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(datos["respuesta"], "Listo.")
        self.assertEqual(datos["consultas"][0]["herramienta"], "saldo_cuenta")
        self.assertEqual(datos["consultas"][0]["estado"], "ok")
        self.assertTrue(datos["consultas"][0]["enlaces"])

        with proveedor("Sigue."):
            r = self.client.post(reverse("copilot_pms:api_preguntar"),
                                 json.dumps({"pregunta": "¿Y?", "conversacion": datos["conversacion"]}),
                                 content_type="application/json")
        self.assertEqual(r.json()["conversacion"], datos["conversacion"])

    def test_api_errores(self):
        url = reverse("copilot_pms:api_preguntar")
        self.assertEqual(self.client.post(url, "no", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 400)
        r = self.client.post(url, json.dumps({"pregunta": "x", "conversacion": "no-es-uuid"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 404)

    def test_api_herramientas(self):
        datos = self.client.get(reverse("copilot_pms:api_herramientas")).json()
        self.assertIn("saldo_cuenta", [h["name"] for h in datos["herramientas"]])
        self.assertTrue(datos["pendientes"])


@override_settings(COPILOT_PMS={"PROVIDER": "fake", "REQUIRE_LOGIN": True})
class LoginTests(ContabilidadBase):
    def test_sin_sesion(self):
        self.assertEqual(self.client.get(reverse("copilot_pms:index")).status_code, 302)
        r = self.client.post(reverse("copilot_pms:api_preguntar"), "{}", content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_conversaciones_ajenas_no_se_ven(self):
        ana = User.objects.create_user("ana", password="x")
        luis = User.objects.create_user("luis", password="x")
        self.client.force_login(ana)
        with proveedor("Hola."):
            self.client.post(reverse("copilot_pms:preguntar"), {"pregunta": "hola"})
        conv = Conversacion.objects.get()
        self.assertEqual(conv.usuario, ana)
        self.client.force_login(luis)
        self.assertEqual(self.client.get(reverse("copilot_pms:conversacion", args=[conv.public_id])).status_code, 404)
