import json
from decimal import Decimal

from django.urls import reverse

from cajas.models import CajaDia, CajaMes
from copilot_pms.agent import preguntar
from copilot_pms.llm.fake import FakeChat, llamar
from copilot_pms.models import EstadoLlamada
from copilot_pms.tests.utils import ContabilidadBase, ctx
from copilot_pms.tools.base import disponibles, ejecutar


def grafico(contexto=None, **args):
    return ejecutar("grafico", json.dumps(args), contexto or ctx())


class GraficosTests(ContabilidadBase):
    def setUp(self):
        for anio, bar in ((2026, "100"), (2025, "80")):
            julio = CajaMes.objects.create(anio=anio, mes=7)
            CajaDia.objects.create(caja_mes=julio, dia=1, bar=Decimal(bar), comedor=Decimal("50"))
            CajaDia.objects.create(caja_mes=CajaMes.objects.create(anio=anio, mes=8), dia=1, bar=Decimal("10"))

    def test_plantilla_de_cajas(self):
        r = grafico(plantilla="ventas_cajas_mensuales", anio=2026)
        self.assertEqual(r.estado, EstadoLlamada.OK)
        g = r.datos["grafico"]
        self.assertEqual(g["tipo"], "barras_apiladas")
        self.assertEqual([s["nombre"] for s in g["series"]], ["Bar", "Comedor", "Chill out"])
        self.assertEqual(g["etiquetas"][6], "Jul")
        self.assertEqual(g["series"][0]["valores"][6], "100.00")
        self.assertIn("Ecotasa", r.datos["series_disponibles"])
        self.assertEqual(r.datos["parametros"], {"plantilla": "ventas_cajas_mensuales", "anio": 2026})

    def test_edicion_series_tipo_y_comparacion(self):
        r = grafico(plantilla="ventas_cajas_mensuales", anio=2026, series=["Bar"], tipo="lineas",
                    comparar_con=[2025], desde_mes=7, hasta_mes=8, titulo="Bar")
        g = r.datos["grafico"]
        self.assertEqual((g["tipo"], g["titulo"], g["etiquetas"]), ("lineas", "Bar", ["Jul", "Ago"]))
        self.assertEqual([(s["nombre"], s["valores"]) for s in g["series"]],
                         [("Bar 2026", ["100.00", "10.00"]), ("Bar 2025", ["80.00", "10.00"])])

    def test_ano_en_curso_se_corta_en_el_ultimo_mes_con_datos(self):
        g = grafico(plantilla="ventas_cajas_mensuales", anio=2026).datos["grafico"]
        self.assertEqual(g["etiquetas"][-1], "Ago")

    def test_parametros_incompatibles_piden_aclaracion(self):
        casos = [
            {"plantilla": "ventas_cajas_mensuales", "anio": 2026, "tipo": "tarta"},
            {"plantilla": "ventas_cajas_mensuales", "anio": 2026, "series": ["Piscina"]},
            {"plantilla": "saldo_cuenta_mensual", "anio": 2026},
        ]
        for args in casos:
            r = grafico(**args)
            self.assertEqual(r.estado, EstadoLlamada.ACLARAR, args)
        r = grafico(plantilla="ventas_cajas_mensuales", anio=2026, series=["Piscina"])
        self.assertIn("Disponibles: Bar", r.mensaje)
        self.assertEqual(grafico(plantilla="inventada").estado, EstadoLlamada.INVALIDO)

    def test_sin_datos(self):
        r = grafico(plantilla="ventas_cajas_mensuales", anio=2019)
        self.assertEqual((r.estado, r.codigo), (EstadoLlamada.NO_DISPONIBLE, "SIN_DATOS"))

    def test_saldo_de_cuenta_por_mes(self):
        g = grafico(plantilla="saldo_cuenta_mensual", anio=2026, cuenta="572").datos["grafico"]
        valores = g["series"][0]["valores"]
        self.assertTrue(g["series"][0]["nombre"].startswith("572"))
        self.assertEqual((valores[0], valores[2], valores[5]), ("1000.00", "1500.00", "1500.00"))  # borrador fuera

    def test_resultado_contable_mensual(self):
        g = grafico(plantilla="resultado_contable_mensual", anio=2026, tipo="lineas").datos["grafico"]
        self.assertEqual(g["series"][0]["valores"][2], "500.00")

    def test_analisis_sin_datos(self):
        self.assertEqual(grafico(plantilla="beneficio_mensual", anio=2019).estado, EstadoLlamada.NO_DISPONIBLE)
        r = ejecutar("resumen_beneficio", '{"anio": 2019}', ctx())
        self.assertEqual(r.estado, EstadoLlamada.NO_DISPONIBLE)

    def test_analisis_usa_las_cajas(self):
        # Sin reservas ni compras, el análisis toma bar y comedor de las cajas (sin IVA: / 1,10)
        g = grafico(plantilla="ingresos_mensuales", anio=2026, series=["Bar", "Comedor"]).datos["grafico"]
        self.assertEqual(g["series"][0]["valores"][6], "90.91")
        r = ejecutar("resumen_beneficio", '{"anio": 2026, "mes": 7}', ctx())
        self.assertEqual(r.estado, EstadoLlamada.OK)
        self.assertEqual(r.datos["ingresos"]["bar"], "90.91")
        self.assertTrue(r.avisos)

    def test_permisos_por_plantilla(self):
        from django.contrib.auth.models import Permission, User

        user = User.objects.create_user("bar")
        user.user_permissions.add(Permission.objects.get(codename="view_cajames"))
        user = User.objects.get(pk=user.pk)
        contexto = ctx(user=user, login_requerido=True)
        self.assertIn("grafico", [h.nombre for h in disponibles(contexto)])
        self.assertEqual(grafico(contexto, plantilla="ventas_cajas_mensuales", anio=2026).estado, EstadoLlamada.OK)
        self.assertEqual(grafico(contexto, plantilla="saldo_cuenta_mensual", anio=2026, cuenta="572").estado,
                         EstadoLlamada.DENEGADO)

    def test_el_historial_lleva_los_parametros_para_editar(self):
        args = '{"plantilla": "ventas_cajas_mensuales", "anio": 2026}'
        primero = preguntar("Gráfico de cajas", ctx(), proveedor=FakeChat([llamar("grafico", args), "Ahí lo tienes."]))
        proveedor = FakeChat(["Hecho."])
        preguntar("Ahora en líneas", ctx(), primero.conversacion, proveedor=proveedor)
        anterior = proveedor.llamadas[0]["messages"][2]
        self.assertEqual(anterior["role"], "assistant")
        self.assertIn("[Gráfico mostrado: grafico {", anterior["content"])
        self.assertIn('"plantilla": "ventas_cajas_mensuales"', anterior["content"])

    def test_pantallas(self):
        with self.settings(COPILOT_PMS={"PROVIDER": "fake"}):
            from unittest import mock

            with mock.patch("copilot_pms.agent.get_provider", return_value=FakeChat(
                    [llamar("grafico", '{"plantilla": "ventas_cajas_mensuales", "anio": 2026}'), "Listo."])):
                self.client.post(reverse("copilot_pms:preguntar"), {"pregunta": "gráfico"})
            from copilot_pms.models import Conversacion

            conv = Conversacion.objects.get()
            pagina = self.client.get(reverse("copilot_pms:conversacion", args=[conv.public_id]))
            self.assertContains(pagina, 'data-grafico="grafico-')
            self.assertContains(pagina, "chart.umd.js")

        galeria = self.client.get(reverse("copilot_pms:galeria"), {"plantilla": "ventas_cajas_mensuales",
                                                                    "anio": 2026, "comparar_con": "2025"})
        self.assertContains(galeria, "Bar 2025")
        self.assertContains(galeria, 'data-grafico="grafico-galeria"')
        error = self.client.get(reverse("copilot_pms:galeria"), {"plantilla": "ventas_cajas_mensuales",
                                                                  "anio": 2026, "tipo": "tarta"})
        self.assertContains(error, "Una tarta solo tiene sentido")
