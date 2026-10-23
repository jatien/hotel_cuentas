import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

BASE_DIR = Path(settings.BASE_DIR)


class CabecerasTests(TestCase):
    def test_csp_en_modo_informe_con_nonce(self):
        respuesta = self.client.get(reverse("cuadro_cuentas:plan_cuentas"))
        politica = respuesta.headers.get("Content-Security-Policy-Report-Only", "")
        self.assertIn("default-src 'self'", politica)
        self.assertIn("object-src 'none'", politica)
        self.assertIn("report-uri /seguridad/csp-report/", politica)
        nonce = politica.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'nonce="{nonce}"', respuesta.content.decode())
        self.assertNotIn("Content-Security-Policy", respuesta.headers)

    def test_cabeceras_basicas(self):
        respuesta = self.client.get(reverse("seguridad:health"))
        self.assertEqual(respuesta.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(respuesta.headers["Referrer-Policy"], "same-origin")
        self.assertEqual(respuesta.headers["X-Frame-Options"], "SAMEORIGIN")

    def test_plantillas_sin_javascript_en_linea(self):
        """Ningún onclick/onchange/onsubmit…: la CSP en modo obligatorio los bloquearía."""
        import re

        patron = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)
        infractores = []
        for plantilla in BASE_DIR.glob("**/templates/**/*.html"):
            if "venv" in plantilla.parts:
                continue
            for n, linea in enumerate(plantilla.read_text(encoding="utf-8").splitlines(), 1):
                if patron.search(linea):
                    infractores.append(f"{plantilla.relative_to(BASE_DIR)}:{n}")
        self.assertEqual(infractores, [], "Usa data-autosubmit / data-confirm (seguridad/acciones.js)")

    def test_scripts_en_linea_llevan_nonce(self):
        import re

        sin_nonce = []
        for plantilla in BASE_DIR.glob("**/templates/**/*.html"):
            if "venv" in plantilla.parts:
                continue
            for etiqueta in re.findall(r"<script\b[^>]*>", plantilla.read_text(encoding="utf-8")):
                if "src=" not in etiqueta and "nonce=" not in etiqueta and "application/json" not in etiqueta:
                    sin_nonce.append(f"{plantilla.relative_to(BASE_DIR)}: {etiqueta}")
        self.assertEqual(sin_nonce, [])


@override_settings(SECURE_CSP={"default-src": ["'self'"]}, SECURE_CSP_REPORT_ONLY={})
class CspObligatoriaTests(TestCase):
    def test_cabecera_obligatoria(self):
        respuesta = self.client.get(reverse("seguridad:health"))
        self.assertIn("Content-Security-Policy", respuesta.headers)


class CspReportTests(TestCase):
    url = "/seguridad/csp-report/"

    def test_registra_informe_report_uri(self):
        cuerpo = {"csp-report": {"document-uri": "https://pms.hotel.lan/cajas/", "violated-directive": "script-src",
                                 "blocked-uri": "inline", "line-number": 12}}
        with self.assertLogs("seguridad.csp", level="WARNING") as logs:
            r = self.client.post(self.url, json.dumps(cuerpo), content_type="application/csp-report")
        self.assertEqual(r.status_code, 204)
        self.assertIn("script-src", logs.output[0])

    def test_registra_informe_reporting_api(self):
        cuerpo = [{"type": "csp-violation", "body": {"documentURL": "https://x/", "effectiveDirective": "img-src"}}]
        with self.assertLogs("seguridad.csp", level="WARNING") as logs:
            r = self.client.post(self.url, json.dumps(cuerpo), content_type="application/reports+json")
        self.assertEqual(r.status_code, 204)
        self.assertIn("img-src", logs.output[0])

    def test_no_necesita_csrf(self):
        from django.test import Client

        c = Client(enforce_csrf_checks=True)
        r = c.post(self.url, json.dumps({"csp-report": {}}), content_type="application/csp-report")
        self.assertEqual(r.status_code, 204)

    def test_rechaza_json_invalido_y_vacio(self):
        self.assertEqual(self.client.post(self.url, "no-json", content_type="application/csp-report").status_code, 400)
        self.assertEqual(self.client.post(self.url, "", content_type="application/csp-report").status_code, 400)

    def test_rechaza_cuerpos_grandes(self):
        grande = json.dumps({"csp-report": {"x": "a" * 20000}})
        self.assertEqual(self.client.post(self.url, grande, content_type="application/csp-report").status_code, 413)

    def test_solo_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class HealthTests(TestCase):
    def test_ok(self):
        r = self.client.get("/seguridad/health/")
        self.assertEqual((r.status_code, r.content), (200, b"ok"))
        self.assertIn("no-cache", r.headers["Cache-Control"])


class AjustesProduccionTests(TestCase):
    """Arranca un proceso aparte con DJANGO_ENV=prod: los ajustes inseguros deben impedir el arranque."""

    def _cargar(self, **entorno):
        env = {k: v for k, v in os.environ.items() if k not in {"SECRET_KEY", "ALLOWED_HOSTS", "DJANGO_SETTINGS_MODULE"}}
        # Vacíos explícitos: así no cuenta lo que haya en el .env del PC
        env.update({"DJANGO_ENV": "prod", "DB_PASSWORD": "x", "SECRET_KEY": "", "ALLOWED_HOSTS": "", **entorno})
        return subprocess.run(
            [sys.executable, "-c", "import django; django.setup(); from django.conf import settings; "
                                   "print(settings.DEBUG, settings.SESSION_COOKIE_SECURE, settings.SECURE_SSL_REDIRECT)"],
            cwd=BASE_DIR, env={**env, "DJANGO_SETTINGS_MODULE": "mysite.settings"},
            capture_output=True, text=True, timeout=60,
        )

    def test_prod_valido(self):
        r = self._cargar(SECRET_KEY="k" * 64, ALLOWED_HOSTS="pms.hotel.lan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False True True")

    def test_prod_sin_secret_key(self):
        r = self._cargar(ALLOWED_HOSTS="pms.hotel.lan")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SECRET_KEY", r.stderr)

    def test_prod_rechaza_clave_insegura(self):
        r = self._cargar(SECRET_KEY="django-insecure-" + "x" * 60, ALLOWED_HOSTS="pms.hotel.lan")
        self.assertIn("SECRET_KEY de producción insegura", r.stderr)

    def test_prod_rechaza_comodin_en_hosts(self):
        r = self._cargar(SECRET_KEY="k" * 64, ALLOWED_HOSTS="*")
        self.assertIn("ALLOWED_HOSTS", r.stderr)
