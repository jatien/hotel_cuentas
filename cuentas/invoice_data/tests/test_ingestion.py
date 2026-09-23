import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from invoice_data.models import InvoiceDocument, SourceType, TipoEntidad
from invoice_data.services.ingestion import ingest_file
from invoice_data.tests.base import MediaIsolatedTestCase


def make_digital_pdf(path: Path, text: str = "FACTURA DE PRUEBA NUMERO 123"):
    """A real digitally-authored PDF with a genuine, selectable text layer."""
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, text)
    c.save()


def make_scanned_pdf(path: Path):
    """A PDF containing only an embedded image - simulates a scan, no text layer."""
    img = Image.new("RGB", (600, 800), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), "texto dentro de una imagen, no seleccionable", fill="black")
    img.save(path, "PDF")


def make_test_image(path: Path):
    img = Image.new("RGB", (400, 300), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "FACTURA FOTO PRUEBA", fill="black")
    img.save(path, "JPEG")


class IngestionTests(MediaIsolatedTestCase):
    """
    Uses MediaIsolatedTestCase (not plain TestCase) because ingest_file()
    writes real files via doc.file.save() - Django's TestCase only rolls
    back the database between tests, not file storage, so without this
    every run would leave test files behind in the real media folder.
    """

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_digital_pdf_is_classified_correctly(self):
        pdf_path = self.tmp_dir / "digital.pdf"
        make_digital_pdf(pdf_path)

        doc, created = ingest_file(pdf_path, departamento="recepcion")

        self.assertTrue(created)
        self.assertEqual(doc.source_type, SourceType.DIGITAL_PDF)
        self.assertFalse(doc.needs_ocr)
        self.assertIn("FACTURA", doc.raw_text_layer)

    def test_scanned_pdf_is_classified_correctly(self):
        pdf_path = self.tmp_dir / "scanned.pdf"
        make_scanned_pdf(pdf_path)

        doc, created = ingest_file(pdf_path, departamento="cocina")

        self.assertTrue(created)
        self.assertEqual(doc.source_type, SourceType.SCANNED_PDF)
        self.assertTrue(doc.needs_ocr)
        self.assertEqual(doc.raw_text_layer, "")

    def test_image_is_classified_correctly(self):
        img_path = self.tmp_dir / "foto.jpg"
        make_test_image(img_path)

        doc, created = ingest_file(img_path, departamento="mantenimiento")

        self.assertTrue(created)
        self.assertEqual(doc.source_type, SourceType.IMAGE)
        self.assertTrue(doc.needs_ocr)

    def test_duplicate_file_is_not_reimported(self):
        pdf_path = self.tmp_dir / "digital.pdf"
        make_digital_pdf(pdf_path)

        doc1, created1 = ingest_file(pdf_path, departamento="recepcion")
        doc2, created2 = ingest_file(pdf_path, departamento="recepcion")

        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(doc1.pk, doc2.pk)
        self.assertEqual(InvoiceDocument.objects.count(), 1)

    def test_unsupported_extension_is_flagged_as_error(self):
        bad_path = self.tmp_dir / "nota.txt"
        bad_path.write_text("esto no es una factura")

        doc, created = ingest_file(bad_path, departamento="comedor")

        self.assertTrue(created)
        self.assertEqual(doc.status, "error")

    def test_acreedor_has_no_departamento(self):
        """
        Regression test for the departamento-fallback bug: passing an
        explicit empty string must NOT be overridden by the parent folder
        name - that's exactly what an acreedor needs.
        """
        pdf_path = self.tmp_dir / "acreedor.pdf"
        make_digital_pdf(pdf_path)

        doc, created = ingest_file(pdf_path, departamento="", tipo_entidad=TipoEntidad.ACREEDOR)

        self.assertTrue(created)
        self.assertEqual(doc.tipo_entidad, TipoEntidad.ACREEDOR)
        self.assertEqual(doc.departamento, "")

    def test_proveedor_is_default_tipo_entidad(self):
        """Backward compatibility: not passing tipo_entidad at all still defaults to proveedor."""
        pdf_path = self.tmp_dir / "proveedor.pdf"
        make_digital_pdf(pdf_path)

        doc, _ = ingest_file(pdf_path, departamento="cocina")

        self.assertEqual(doc.tipo_entidad, TipoEntidad.PROVEEDOR)