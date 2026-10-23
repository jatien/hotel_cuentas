import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from copilot_pms.models import EstadoLlamada
from copilot_pms.tools.base import Contexto, ejecutar
from inicio.modulos import MODULOS, por_clave
from inicio.views import ELIPSE, posicion


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
    def test_ovalo_alargado_con_el_lado_izquierdo_mas_alto(self):
        derecha, izquierda = posicion(90), posicion(270)
        self.assertLess(izquierda[1], derecha[1])                        # el lado izquierdo queda más alto
        self.assertGreater(abs(derecha[0]), 2 * abs(posicion(0)[1]))    # bastante más ancho que alto

    def test_imagenes_dentro_del_lienzo(self):
        for angulo in range(0, 360, 15):
            x, y = posicion(angulo)
            self.assertLess(abs(x) + ELIPSE["img"] / 2, ELIPSE["ancho"] / 2)
            self.assertLess(abs(y) + ELIPSE["img"] / 2, ELIPSE["alto"] / 2)


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
        self.assertNotIn("rueda-flechas", html)               # las flechas del boceto eran solo guía
        self.assertNotIn('class="pista"', html)               # sin texto bajo la rueda

    def test_iconos_en_linea_con_colores_del_tema(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertEqual(html.count('class="icono"'), len(MODULOS))
        self.assertIn('data-tema="abrat"', html)

    def test_rama_abierta(self):
        r = self.client.get(reverse("home_modulo", args=["contabilidad"]) + "?seccion=1")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('data-clave="contabilidad"', html)
        self.assertIn(f'src="{reverse("cuadro_cuentas:plan_cuentas")}"', html)   # sección pedida en el marco
        self.assertIn('class="app-volver"', html)
        self.assertNotIn('id="app" hidden', html)

    def test_contabilidad_abre_el_diario_y_agrupa_su_menu(self):
        html = self.client.get(reverse("home_modulo", args=["contabilidad"])).content.decode()
        self.assertIn(f'src="{reverse("cuadro_cuentas:diario")}"', html)
        for grupo in ("Configuración", "Registro", "Consultas", "Balances"):
            self.assertIn(f'<li class="app-grupo">{grupo}</li>', html)

    def test_rama_sin_pantallas(self):
        html = self.client.get(reverse("home_modulo", args=["reservas"])).content.decode()
        self.assertIn("Todavía en preparación", html)
        self.assertNotIn('<div class="app-vacio" id="app-vacio" hidden', html)

    def test_rama_inexistente(self):
        self.assertEqual(self.client.get("/m/bar/").status_code, 404)

    def test_las_pantallas_se_pueden_enmarcar(self):
        r = self.client.get(reverse("cajas:resumen"))
        self.assertEqual(r.get("X-Frame-Options"), "SAMEORIGIN")

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
        self.assertEqual(rama["enlace_Análisis"], "/m/analisis/")

    def test_rama_inexistente_no_valida(self):
        self.assertNotEqual(self.run_tool(modulo="bar").estado, EstadoLlamada.OK)
