"""
Utilidades de test: facturas ficticias (ningún dato real), salidas del
modelo de ejemplo, un OCR falso y una clase base que aísla los ficheros.
"""

import copy
import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from invoice_agent.models import Empresa
from invoice_agent.services.access import AccessContext

# NIF/CIF ficticios con dígito de control válido.
SUPPLIER_NIF = "B12345674"
EMPRESA_NIF = "A58818501"
OTRA_EMPRESA_NIF = "B65410011"

INVOICE_LINES = [
    "SUMINISTROS DEMO, S.L.   CIF: B12345674",
    "Calle Ficticia 1, 07000 Palma",
    "FACTURA Nº: F-2026/0042     Fecha: 15/09/2026",
    "Cliente: HOTEL PRUEBA S.A.   NIF: A58818501",
    "DESCRIPCION                 CANT   PRECIO    IMPORTE",
    "Toallas de baño             10     8,00      80,00",
    "Detergente industrial        2    10,00      20,00",
    "Base 100,00   IVA 21% 21,00   TOTAL 121,00",
]


def field(value=None, status="present", confidence="high", page=1, text=None):
    if value is None and status == "present":
        status = "absent"
    return {
        "status": status,
        "value": value,
        "confidence": confidence,
        "evidence": {"page": page if status != "absent" else None, "text": text if text is not None else value},
    }


BASE_OUTPUT = {
    "document_type": "invoice",
    "supplier_name": field("Suministros Demo, S.L."),
    "supplier_tax_id": field(SUPPLIER_NIF),
    "supplier_address": field("Calle Ficticia 1, 07000 Palma"),
    "supplier_country": field("ES", confidence="medium"),
    "customer_tax_id": field(EMPRESA_NIF),
    "invoice_number": field("F-2026/0042"),
    "invoice_date": field("2026-09-15", text="15/09/2026"),
    "due_date": field(None),
    "currency": field("EUR", text="€"),
    "purchase_order": field(None),
    "net_subtotal": field("100.00"),
    "discount_total": field(None),
    "charges_total": field(None),
    "tax_total": field("21.00"),
    "withholding_total": field(None),
    "gross_total": field("121.00"),
    "amount_due": field(None),
    "line_items": [
        {"description": "Toallas de baño", "quantity": "10", "unit_price": "8.00", "discount": None,
         "tax_rate": "21", "line_subtotal": "80.00", "line_total": None, "unreadable_fields": [],
         "page": 1, "source_text": "Toallas de baño 10 8,00 80,00"},
        {"description": "Detergente industrial", "quantity": "2", "unit_price": "10.00", "discount": None,
         "tax_rate": "21", "line_subtotal": "20.00", "line_total": None, "unreadable_fields": [],
         "page": 1, "source_text": "Detergente industrial 2 10,00 20,00"},
    ],
    "tax_breakdown": [
        {"kind": "vat", "rate": "21", "base": "100.00", "amount": "21.00", "page": 1, "source_text": "IVA 21% 21,00"},
    ],
    "notes": [],
}


def model_output(**overrides) -> dict:
    data = copy.deepcopy(BASE_OUTPUT)
    for key, value in overrides.items():
        data[key] = value
    return data


def model_json(**overrides) -> str:
    return json.dumps(model_output(**overrides))


def make_pdf(path: Path, lines=None, pages: int = 1) -> Path:
    pdf = canvas.Canvas(str(path), invariant=1)
    for page in range(pages):
        y = 800
        for line in lines or INVOICE_LINES:
            pdf.drawString(50, y, line)
            y -= 18
        if pages > 1:
            pdf.drawString(50, 60, f"Página {page + 1} de {pages}")
        pdf.showPage()
    pdf.save()
    return path


def make_scanned_pdf(path: Path, pages: int = 1) -> Path:
    images = [Image.new("RGB", (1240, 1754), "white") for _ in range(pages)]
    for image in images:
        ImageDraw.Draw(image).text((60, 60), "imagen sin capa de texto", fill="black")
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])
    return path


def make_png(path: Path, size=(1200, 1600)) -> Path:
    Image.new("RGB", size, "white").save(path, "PNG")
    return path


def upload(path: Path, content_type: str | None = None, name: str | None = None) -> SimpleUploadedFile:
    data = Path(path).read_bytes()
    guessed = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}.get(Path(path).suffix)
    return SimpleUploadedFile(name or Path(path).name, data, content_type=content_type or guessed)


class FakeOcr:
    """OCR falso: devuelve un texto y una confianza fijos y, opcionalmente, una rotación."""

    def __init__(self, text="\n".join(INVOICE_LINES), confidence=92.0, rotation=0):
        self.text, self.confidence, self.rotation_value = text, confidence, rotation
        self.calls = 0

    def read(self, image):
        self.calls += 1
        return self.text, self.confidence

    def rotation(self, image):
        return self.rotation_value


class IsolatedTestCase(TestCase):
    """Almacenamiento privado en una carpeta temporal y proveedor falso por defecto."""

    agent_settings: dict = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.mkdtemp(prefix="invoice_agent_test_")
        settings = {
            "PROVIDER": "fake",
            "PRIVATE_STORAGE_ROOT": cls._tmp,
            "BACKOFF_BASE_SECONDS": 0,
            "JOB_RETRY_DELAY_SECONDS": 0,
            "VISION_MODE": "auto",  # los tests existentes cubren el camino con OCR; image_only tiene los suyos
            "REQUIRE_LOGIN": False,
            **cls.agent_settings,
        }
        cls._override = override_settings(INVOICE_AGENT=settings)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.work = Path(tempfile.mkdtemp(prefix="invoice_agent_files_"))
        self.addCleanup(shutil.rmtree, self.work, True)
        Empresa.objects.all().delete()
        self.empresa = Empresa.objects.create(nif=EMPRESA_NIF, nombre="Hotel Prueba S.A.")
        self.otra = Empresa.objects.create(nif=OTRA_EMPRESA_NIF, nombre="Otra Empresa Ficticia S.L.")

    def ctx(self, *empresas, can_approve=True, user=None) -> AccessContext:
        empresas = empresas or (self.empresa, self.otra)
        return AccessContext(
            empresa_ids=frozenset(e.pk for e in empresas), user=user, actor_label="test", can_approve=can_approve
        )


def D(text: str) -> Decimal:
    return Decimal(text)
