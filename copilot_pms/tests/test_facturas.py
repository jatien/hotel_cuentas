import json
import shutil
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase, override_settings

from copilot_pms.models import EstadoLlamada
from copilot_pms.tests.utils import ctx
from copilot_pms.tools.base import ejecutar
from invoice_agent.models import AccionRevision, Borrador, Documento, Empresa, EventoRevision


class FacturasTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.mkdtemp()
        cls.ajustes = override_settings(INVOICE_AGENT={"PRIVATE_STORAGE_ROOT": cls.tmp})
        cls.ajustes.enable()

    @classmethod
    def tearDownClass(cls):
        cls.ajustes.disable()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.hotel = Empresa.objects.create(nif="A58818501", nombre="Hotel Prueba S.A.")
        self.otra = Empresa.objects.create(nif="B65410011", nombre="Otra Empresa S.L.")
        self.doc = self._factura(self.hotel, "F-2026/0042", Decimal("121.00"))
        self.ajena = self._factura(self.otra, "X-1", Decimal("50.00"))
        self.ctx = ctx(empresas_facturas=frozenset({self.hotel.pk}))

    def _factura(self, empresa, numero, total, con_fichero=True):
        clave = f"{empresa.pk}/2026/09/{uuid.uuid4().hex}.pdf"
        if con_fichero:
            ruta = Path(self.tmp) / clave
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(b"%PDF-1.4 ficticio")
        doc = Documento.objects.create(empresa=empresa, storage_key=clave, original_filename=f"{numero}.pdf",
                                       sha256=uuid.uuid4().hex * 2, mime_type="application/pdf",
                                       size_bytes=20, tipo_fuente="pdf", estado="approved")
        Borrador.objects.create(documento=doc, empresa=empresa, proveedor_nombre="Suministros Demo, S.L.",
                                proveedor_nif="B12345674", numero_factura=numero,
                                fecha_factura=date(2026, 9, 15), total_bruto=total, moneda="EUR")
        return doc

    def run_tool(self, nombre, **args):
        return ejecutar(nombre, json.dumps(args), self.ctx)

    def test_busca_solo_en_las_empresas_del_contexto(self):
        r = self.run_tool("buscar_facturas", proveedor="suministros", origen="ia")
        self.assertEqual(r.estado, EstadoLlamada.OK)
        refs = [f["referencia"] for f in r.datos["facturas"]]
        self.assertEqual(refs, [f"ia:{self.doc.public_id}"])
        self.assertEqual(r.datos["facturas"][0]["total"], "121.00")
        self.assertEqual(r.datos["facturas"][0]["entidad"]["nif"], "A58818501")

    def test_busqueda_sin_criterios_pide_aclaracion(self):
        r = self.run_tool("buscar_facturas")
        self.assertEqual(r.estado, EstadoLlamada.ACLARAR)

    def test_busqueda_por_total_y_numero(self):
        self.assertEqual(self.run_tool("buscar_facturas", total="121.00", origen="ia").datos["facturas"][0]["numero"],
                         "F-2026/0042")
        self.assertEqual(self.run_tool("buscar_facturas", numero="nada", origen="ia").estado, EstadoLlamada.VACIO)

    def test_enlace_firmado_auditado_y_descargable(self):
        r = self.run_tool("documento_factura", referencia=f"ia:{self.doc.public_id}")
        self.assertEqual(r.estado, EstadoLlamada.OK)
        self.assertEqual(r.datos["numero"], "F-2026/0042")
        self.assertTrue(r.datos["enlace_documento"].startswith("/facturas-ia/original/"))
        self.assertTrue(EventoRevision.objects.filter(documento=self.doc, accion=AccionRevision.FILE_ACCESS,
                                                      actor_label__startswith="copilot").exists())
        respuesta = self.client.get(r.datos["enlace_documento"])
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/pdf")

    def test_documento_de_otra_empresa_no_se_revela(self):
        r = self.run_tool("documento_factura", referencia=f"ia:{self.ajena.public_id}")
        self.assertEqual(r.estado, EstadoLlamada.NO_DISPONIBLE)
        self.assertEqual(r.codigo, "DOCUMENTO_NO_ENCONTRADO")
        self.assertNotIn("enlace_documento", r.datos)

    def test_original_borrado(self):
        doc = self._factura(self.hotel, "F-2", Decimal("10.00"), con_fichero=False)
        r = self.run_tool("documento_factura", referencia=f"ia:{doc.public_id}")
        self.assertEqual(r.codigo, "ORIGINAL_NO_DISPONIBLE")

    def test_referencias_inventadas(self):
        for ref in ("ia:00000000-0000-0000-0000-000000000000", "ia:--------------------------------", "ocr:999"):
            r = self.run_tool("documento_factura", referencia=ref)
            self.assertEqual(r.estado, EstadoLlamada.NO_DISPONIBLE, ref)
        self.assertEqual(self.run_tool("documento_factura", referencia="https://x").estado, EstadoLlamada.INVALIDO)

    def test_estado_entrenamiento_cuenta_solo_las_empresas_del_contexto(self):
        self._factura(self.hotel, "P-1", Decimal("10.00"))
        Documento.objects.filter(borrador__numero_factura="P-1").update(estado="pending_approval")
        self._factura(self.hotel, "R-1", Decimal("11.00"))
        Documento.objects.filter(borrador__numero_factura="R-1").update(estado="needs_review")
        r = self.run_tool("estado_entrenamiento_facturas")
        self.assertEqual(r.estado, EstadoLlamada.OK)
        self.assertEqual(r.datos["listas_para_entrenar"], 2)  # la de la otra empresa no cuenta
        self.assertEqual(r.datos["aprobadas_por_una_persona"], 1)
        self.assertEqual(r.datos["necesitan_revision"], 1)
        self.assertTrue(r.avisos)
