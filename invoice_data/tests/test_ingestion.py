"""Tests of step 1: classifying and registering files."""

import shutil
import tempfile
from pathlib import Path

from invoice_data.models import InvoiceDocument, IngestionStatus, SourceType, TipoEntidad
from invoice_data.services.ingestion import ingest_file
from invoice_data.tests.base import MediaIsolatedTestCase
from invoice_data.tests.fixtures import make_image, make_invoice_pdf, make_scanned_pdf


class IngestionTests(MediaIsolatedTestCase):
    """Uses generated fixtures in a temp folder; file writes go to the isolated MEDIA_ROOT."""

    def setUp(self):
        """A private scratch folder for the source files."""
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Remove the scratch folder."""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_digital_pdf_is_classified_correctly(self):
        document, created = ingest_file(make_invoice_pdf(self.tmp_dir / "digital.pdf"), departamento="recepcion")
        self.assertTrue(created)
        self.assertEqual(document.source_type, SourceType.DIGITAL_PDF)
        self.assertFalse(document.needs_ocr)
        self.assertIn("FACTURA", document.raw_text_layer)

    def test_scanned_pdf_is_classified_correctly(self):
        document, _ = ingest_file(make_scanned_pdf(self.tmp_dir / "scan.pdf"), departamento="cocina")
        self.assertEqual(document.source_type, SourceType.SCANNED_PDF)
        self.assertTrue(document.needs_ocr)
        self.assertEqual(document.status, IngestionStatus.NEEDS_OCR)

    def test_image_is_classified_correctly(self):
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.jpg"), departamento="mantenimiento")
        self.assertEqual(document.source_type, SourceType.IMAGE)
        self.assertTrue(document.needs_ocr)

    def test_duplicate_file_is_not_reimported(self):
        path = make_invoice_pdf(self.tmp_dir / "digital.pdf")
        first, created_first = ingest_file(path, departamento="recepcion")
        second, created_second = ingest_file(path, departamento="recepcion")
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(InvoiceDocument.objects.count(), 1)

    def test_unsupported_extension_is_stored_as_error(self):
        bad = self.tmp_dir / "nota.txt"
        bad.write_text("esto no es una factura")
        document, created = ingest_file(bad, departamento="comedor")
        self.assertTrue(created)
        self.assertEqual(document.status, IngestionStatus.ERROR)

    def test_corrupt_pdf_is_stored_as_error_instead_of_crashing(self):
        """A file that claims to be a PDF but is not must not abort an import."""
        bad = self.tmp_dir / "rota.pdf"
        bad.write_text("no soy un pdf")
        document, created = ingest_file(bad, departamento="comedor")
        self.assertTrue(created)
        self.assertEqual(document.status, IngestionStatus.ERROR)
        self.assertTrue(document.error_message)

    def test_creditor_keeps_an_empty_departamento(self):
        """An explicit empty string must NOT fall back to the folder name."""
        document, _ = ingest_file(
            make_invoice_pdf(self.tmp_dir / "acreedor.pdf"), departamento="", tipo_entidad=TipoEntidad.ACREEDOR
        )
        self.assertEqual(document.tipo_entidad, TipoEntidad.ACREEDOR)
        self.assertEqual(document.departamento, "")

    def test_supplier_is_the_default_type(self):
        document, _ = ingest_file(make_invoice_pdf(self.tmp_dir / "p.pdf"), departamento="cocina")
        self.assertEqual(document.tipo_entidad, TipoEntidad.PROVEEDOR)

    def test_departamento_falls_back_to_the_parent_folder(self):
        folder = self.tmp_dir / "bar"
        folder.mkdir()
        document, _ = ingest_file(make_invoice_pdf(folder / "f.pdf"))
        self.assertEqual(document.departamento, "bar")
