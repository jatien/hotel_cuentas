import shutil
import tempfile

from django.test import TestCase, override_settings

from copilot_pms.entrenamiento.hotel_ficticio import GeneradorHotel, cif_ficticio
from cuadro_cuentas.nif import validar_nif


class HotelFicticioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tmp = tempfile.mkdtemp()
        with override_settings(INVOICE_AGENT={"PRIVATE_STORAGE_ROOT": cls.tmp}):
            cls.hotel = GeneradorHotel(semilla=7).crear()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def test_nif_ficticios_validos(self):
        for n in (9900001, 9910003, 1234567):
            self.assertEqual(validar_nif(cif_ficticio(n)), cif_ficticio(n))

    def test_contabilidad_cuadra(self):
        from cuadro_cuentas.consultas import sumas_y_saldos
        from cuadro_cuentas.models import Asiento, Ejercicio

        for ej in Ejercicio.objects.all():
            _, totales = sumas_y_saldos(ej)
            self.assertEqual(totales["debe"], totales["haber"], ej)
        self.assertTrue(Asiento.objects.filter(estado=Asiento.Estado.BORRADOR).exists())

    def test_hay_datos_en_todas_las_fuentes(self):
        from analisis.models import Reserva
        from cajas.models import CajaDia
        from invoice_agent.models import Borrador
        from nominas.models import NominaMensual

        self.assertTrue(Reserva.objects.exists() and CajaDia.objects.exists() and NominaMensual.objects.exists())
        self.assertEqual(Borrador.objects.count(), len(self.hotel.facturas))

    def test_escenarios(self):
        import collections
        import json

        from copilot_pms.entrenamiento.escenarios import Catalogo, Generador

        with override_settings(INVOICE_AGENT={"PRIVATE_STORAGE_ROOT": self.tmp}):
            gen = Generador(Catalogo.desde_bd(self.hotel.hoy), semilla=3)
            ejemplos = gen.generar(150)
        self.assertEqual(len(ejemplos), 150)
        tipos = collections.Counter(e["meta"]["escenario"] for e in ejemplos)
        self.assertGreaterEqual(len(tipos), 18, tipos)
        for e in ejemplos:
            roles = [m["role"] for m in e["messages"]]
            self.assertEqual(roles[0], "system")
            self.assertEqual(roles[-1], "assistant")
            self.assertTrue(e["messages"][-1]["content"])
            for m in e["messages"]:
                for tc in m.get("tool_calls", []):
                    self.assertIsInstance(tc["function"]["arguments"], dict)
        json.dumps(ejemplos, ensure_ascii=False, default=str)  # serializable tal cual

    def test_dataset_exportado_y_evaluacion(self):
        import json
        import tempfile
        from pathlib import Path

        from copilot_pms.entrenamiento.escenarios import Catalogo, Generador
        from copilot_pms.entrenamiento.evaluacion import cifras_inventadas, evaluar
        from copilot_pms.entrenamiento.exportar import escribir
        from copilot_pms.llm.base import ChatResponse, ToolCall

        with override_settings(INVOICE_AGENT={"PRIVATE_STORAGE_ROOT": self.tmp}):
            ejemplos = Generador(Catalogo.desde_bd(self.hotel.hoy), semilla=5).generar(120)
        # Las respuestas de referencia no pueden tener cifras que no salgan de las herramientas
        for e in ejemplos:
            resultados = [m["content"] for m in e["messages"] if m["role"] == "tool"]
            final = e["messages"][-1]["content"]
            self.assertEqual(cifras_inventadas(final, resultados), [], (e["meta"], final))

        carpeta = Path(tempfile.mkdtemp())
        manifest = escribir(ejemplos, carpeta, semilla=1, hoy=self.hotel.hoy)
        self.assertEqual(sum(manifest["ejemplos"].values()), 120)
        test = [json.loads(l) for l in (carpeta / "train.jsonl").read_text().splitlines()][:20]

        class Oraculo:
            """Responde exactamente lo esperado: la evaluación debe dar 1.0 en todo."""
            model = "oraculo"

            def __init__(self, ejemplos):
                self.cola = [m for e in ejemplos for m in e["messages"] if m["role"] == "assistant" and m.get("entrenar", True)]

            def chat(self, messages, tools):
                m = self.cola.pop(0)
                return ChatResponse(content=m.get("content") or "", tool_calls=[
                    ToolCall(tc["id"], tc["function"]["name"], json.dumps(tc["function"]["arguments"]))
                    for tc in m.get("tool_calls", [])])

        informe = evaluar(Oraculo(test), test, str(self.hotel.hoy))
        t = informe["total"]
        self.assertEqual((t["herramienta_correcta"], t["argumentos_correctos"]), (1.0, 1.0), informe["fallos"][:3])
        self.assertIn(t["no_llama_cuando_no_toca"], (1.0, None))
        self.assertIn(t["respuestas_con_cifras_inventadas"], (0.0, None))
