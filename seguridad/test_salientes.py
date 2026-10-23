import time

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from seguridad.checks import modelos_ia
from seguridad.webhooks import FirmaInvalida, firma_webhook, firmar, verificar_firma


def _ids(**ajustes):
    base = {"INVOICE_AGENT": {"PROVIDER": "fake"}, "COPILOT_PMS": {"PROVIDER": "fake"}, "OUTBOUND_VPN_NETWORKS": []}
    base.update(ajustes)
    with override_settings(**base):
        return sorted(a.id for a in modelos_ia(None))


class ModelosIATests(TestCase):
    def copilot(self, base_url, api_key="clave", **extra):
        return _ids(COPILOT_PMS={"PROVIDER": "openai", "BASE_URL": base_url, "API_KEY": api_key}, **extra)

    def test_proveedor_fake_no_avisa(self):
        self.assertEqual(_ids(), [])

    def test_nube_avisa_de_que_los_datos_salen(self):
        self.assertEqual(self.copilot(""), ["seguridad.W010"])

    def test_https_externo_correcto(self):
        self.assertEqual(self.copilot("https://modelos.example.com/v1"), [])

    def test_http_externo_es_error(self):
        self.assertEqual(self.copilot("http://8.8.8.8:8000/v1"), ["seguridad.E010"])
        self.assertEqual(self.copilot("http://modelos.example.com/v1"), ["seguridad.E010"])

    def test_http_en_la_lan_avisa(self):
        self.assertEqual(self.copilot("http://192.168.1.50:8000/v1"), ["seguridad.W011"])
        self.assertEqual(self.copilot("http://gpu.hotel.lan:8000/v1"), ["seguridad.W011"])

    def test_http_por_vpn_correcto(self):
        self.assertEqual(self.copilot("http://100.101.5.7:8000/v1"), [])  # Tailscale
        self.assertEqual(self.copilot("http://10.8.0.2:8000/v1", OUTBOUND_VPN_NETWORKS=["10.8.0.0/24"]), [])

    def test_servidor_sin_clave(self):
        self.assertEqual(self.copilot("http://100.101.5.7:11434/v1", api_key=""), ["seguridad.W012"])
        self.assertEqual(self.copilot("http://127.0.0.1:11434/v1", api_key=""), [])  # mismo equipo


SECRETO = "secreto-compartido"


class FirmaTests(TestCase):
    def test_firma_valida(self):
        t = str(int(time.time()))
        verificar_firma(SECRETO, b'{"a":1}', firmar(SECRETO, t, b'{"a":1}'), t)
        verificar_firma(SECRETO, b'{"a":1}', "sha256=" + firmar(SECRETO, t, b'{"a":1}'), t)

    def test_cuerpo_alterado(self):
        t = str(int(time.time()))
        with self.assertRaisesMessage(FirmaInvalida, "firma incorrecta"):
            verificar_firma(SECRETO, b'{"a":2}', firmar(SECRETO, t, b'{"a":1}'), t)

    def test_repeticion_antigua(self):
        t = str(int(time.time()) - 3600)
        with self.assertRaisesMessage(FirmaInvalida, "fuera de plazo"):
            verificar_firma(SECRETO, b"x", firmar(SECRETO, t, b"x"), t)

    def test_sin_secreto_o_cabeceras(self):
        with self.assertRaises(FirmaInvalida):
            verificar_firma("", b"x", "f", "1")
        with self.assertRaises(FirmaInvalida):
            verificar_firma(SECRETO, b"x", "", "")


@override_settings(PRUEBA_WEBHOOK_SECRET=SECRETO)
class DecoradorTests(TestCase):
    def setUp(self):
        @firma_webhook(secreto="PRUEBA_WEBHOOK_SECRET")
        def vista(request):
            return HttpResponse("ok")

        self.vista = vista

    def _post(self, cuerpo=b'{"reserva":1}', firma=None, t=None, wid=None):
        t = t or str(int(time.time()))
        cabeceras = {"HTTP_X_TIMESTAMP": t, "HTTP_X_SIGNATURE": firma or firmar(SECRETO, t, cuerpo)}
        if wid:
            cabeceras["HTTP_X_WEBHOOK_ID"] = wid
        return self.vista(RequestFactory().post("/w", cuerpo, content_type="application/json", **cabeceras))

    def test_acepta_firmado(self):
        self.assertEqual(self._post().status_code, 200)

    def test_rechaza_sin_firma_valida(self):
        with self.assertLogs("seguridad.webhooks", level="WARNING"):
            self.assertEqual(self._post(firma="0" * 64).status_code, 401)

    def test_rechaza_repetido_por_id(self):
        self.assertEqual(self._post(wid="evt-1").status_code, 200)
        with self.assertLogs("seguridad.webhooks", level="WARNING"):
            self.assertEqual(self._post(wid="evt-1").status_code, 409)

    def test_solo_post(self):
        self.assertEqual(self.vista(RequestFactory().get("/w")).status_code, 405)

    def test_no_necesita_csrf(self):
        self.assertTrue(getattr(self.vista, "csrf_exempt", False))
