import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from copilot_pms.models import EstadoLlamada
from copilot_pms.tools.base import Contexto, ejecutar
from inicio.modulos import MODULOS, por_clave
from inicio.views import flechas_rueda, lineas_rueda


class RegistroModulosTests(TestCase):
    def test_cuatro_ramas_en_orden(self):
        self.assertEqual([m.clave for m in MODULOS], ["reservas", "facturas", "contabilidad", "analisis"])

    def test_reservas_aun_no_disponible(self):
        self.assertFalse(por_clave("reservas").disponible)
        self.assertTrue(all(s.url is None for s in por_clave("reservas").secciones))

    def test_secciones_programadas_resuelven_url(self):
        for clave in ("facturas", "contabilidad", "analisis"):
            m = por_clave(clave)
            self.assertTrue(m.disponible, clave)
            for s in m.secciones:
                self.assertTrue(s.url and s.url.startswith("/"), f"{clave}: {s.nombre}")


class GeometriaTests(TestCase):
    def test_dos_arcos_por_tramo(self):
        lineas = lineas_rueda(4)
        self.assertEqual(len(lineas), 4)
        self.assertTrue(all(len(t) == 2 and t[0].startswith("M ") for t in lineas))

    def test_una_flecha_por_tramo(self):
        self.assertEqual(len(flechas_rueda(4)), 4)


class PaginaInicioTests(TestCase):
    def test_rueda(self):
        r = self.client.get(reverse("home"))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for m in MODULOS:
            self.assertIn(f'data-clave="{m.clave}"', html)
        self.assertIn("modulo-futuro", html)                     # reservas: próximamente
        self.assertIn(reverse("copilot_pms:preguntar"), html)     # chat del centro
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn('id="modulos-datos"', html)
        self.assertEqual(html.count('class="rueda-lineas"'), 1)

    def test_vista_clasica(self):
        self.assertEqual(self.client.get(reverse("home_clasico")).status_code, 200)


class HerramientaCopilotTests(TestCase):
    def run_tool(self, **args):
        return ejecutar("modulos_pms", json.dumps(args), Contexto(hoy=date(2026, 10, 23), actor_label="test"))

    def test_todas_las_ramas(self):
        r = self.run_tool()
        self.assertEqual(r.estado, EstadoLlamada.OK)
        self.assertEqual(len(r.datos["ramas"]), len(MODULOS))
        self.assertTrue(any("reservas" in a.lower() for a in r.avisos))

    def test_una_rama_con_enlaces(self):
        r = self.run_tool(modulo="analisis")
        rama, = r.datos["ramas"]
        cajas = next(s for s in rama["secciones"] if s["nombre"] == "Cajas")
        self.assertEqual(cajas["enlace_Cajas"], reverse("cajas:resumen"))

    def test_rama_inexistente_no_valida(self):
        self.assertNotEqual(self.run_tool(modulo="bar").estado, EstadoLlamada.OK)
