"""Tests of full-page OCR (scans and photos). Skipped when Tesseract is not available on the machine."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from django.conf import settings

from invoice_data.models import IngestionStatus
from invoice_data.services.ingestion import ingest_file
from invoice_data.services.ocr import run_ocr
from invoice_data.tests.base import MediaIsolatedTestCase
from invoice_data.tests.fixtures import make_image, make_invoice_pdf


def tesseract_available() -> bool:
    """True if Tesseract can be found via PATH, settings.TESSERACT_CMD or the TESSERACT_CMD variable."""
    if shutil.which("tesseract"):
        return True
    configured = getattr(settings, "TESSERACT_CMD", None) or os.environ.get("TESSERACT_CMD")
    return bool(configured) and Path(configured).exists()


@unittest.skipUnless(tesseract_available(), "Tesseract no está instalado: test omitido.")
class OCRTests(MediaIsolatedTestCase):
    """Runs the real Tesseract on generated images."""

    def setUp(self):
        """A private scratch folder for the source files."""
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Remove the scratch folder."""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ocr_extracts_readable_text_from_an_image(self):
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.png", "FACTURA PRUEBA"), departamento="lavanderia")
        text = run_ocr(document)
        self.assertIn("FACTURA", text.upper())
        document.refresh_from_db()
        self.assertEqual(document.status, IngestionStatus.OCR_DONE)
        self.assertIn("FACTURA", document.ocr_text.upper())

    def test_a_digital_document_skips_full_page_ocr(self):
        document, _ = ingest_file(make_invoice_pdf(self.tmp_dir / "d.pdf"), departamento="lavanderia")
        self.assertEqual(run_ocr(document), document.raw_text_layer)
        self.assertEqual(document.ocr_text, "")
