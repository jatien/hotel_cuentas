"""
Tests of image cleanup before OCR (deskew, denoise, contrast). Image
tests here use real OpenCV (no Tesseract needed), so they always run.
"""

import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from invoice_data.models import IngestionStatus
from invoice_data.services.ingestion import ingest_file
from invoice_data.services.preprocessing import clean_page_image, preprocess_document
from invoice_data.tests.base import MediaIsolatedTestCase
from invoice_data.tests.fixtures import make_image, make_invoice_pdf


def make_tilted_page(size=(400, 300), angle=8) -> Image.Image:
    """A white page with a black rectangle drawn at a known tilt, for testing deskew."""
    from PIL import ImageDraw

    image = Image.new("L", size, color=255)
    draw = ImageDraw.Draw(image)
    draw.rectangle([80, 80, 320, 220], outline=0, width=6)
    return image.rotate(angle, fillcolor=255, expand=False)


class CleanPageImageTests(MediaIsolatedTestCase):
    """clean_page_image() itself - no database needed, pure image processing."""

    def test_output_is_grayscale_same_size(self):
        image = Image.new("RGB", (200, 150), color=(200, 190, 180))
        cleaned = clean_page_image(image)
        self.assertEqual(cleaned.size, (200, 150))
        self.assertEqual(cleaned.mode, "L")

    def test_a_blank_page_does_not_crash(self):
        """Deskew's angle detection must handle a page with no content gracefully."""
        image = Image.new("RGB", (300, 200), color=(255, 255, 255))
        cleaned = clean_page_image(image)
        self.assertEqual(cleaned.size, (300, 200))

    def test_visibly_tilted_content_is_straightened(self):
        """
        Compares row-sums before/after: a tilted rectangle's edges smear
        across many rows; a straightened one concentrates into fewer,
        sharper rows. Not a pixel-perfect angle check, just confirms
        deskew measurably improves horizontal alignment.
        """
        tilted = make_tilted_page(angle=8)
        cleaned = clean_page_image(tilted)
        cleaned_array = np.array(cleaned)

        def edge_row_spread(img_array):
            dark_rows = np.where(img_array.mean(axis=1) < 250)[0]
            return dark_rows.max() - dark_rows.min() if dark_rows.size else 0

        tilted_spread = edge_row_spread(np.array(tilted.convert("L")))
        cleaned_spread = edge_row_spread(cleaned_array)
        self.assertLess(cleaned_spread, tilted_spread)

    def test_low_contrast_page_is_enhanced(self):
        """A page with muted (faded-looking) contrast should end up with a wider intensity range."""
        faint = Image.new("L", (200, 200), color=200)
        from PIL import ImageDraw

        ImageDraw.Draw(faint).rectangle([50, 50, 150, 150], fill=170)  # subtle, low-contrast shape
        cleaned = clean_page_image(faint.convert("RGB"))
        self.assertGreater(np.array(cleaned).std(), np.array(faint).std())


class PreprocessDocumentTests(MediaIsolatedTestCase):
    """preprocess_document(): the database/file side - saving InvoicePage rows."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_creates_one_invoicepage_per_page_and_marks_status(self):
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.png"), departamento="cocina")
        pages = preprocess_document(document)
        self.assertEqual(len(pages), 1)
        document.refresh_from_db()
        self.assertEqual(document.status, IngestionStatus.PREPROCESSED)
        self.assertEqual(document.pages.count(), 1)
        self.assertTrue(document.pages.first().processed_image.name)

    def test_a_digital_document_is_skipped_entirely(self):
        document, _ = ingest_file(make_invoice_pdf(self.tmp_dir / "d.pdf"), departamento="cocina")
        self.assertEqual(preprocess_document(document), [])
        self.assertEqual(document.pages.count(), 0)

    def test_second_call_is_a_no_op_without_force(self):
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.png"), departamento="cocina")
        first = preprocess_document(document)
        second = preprocess_document(document)
        self.assertEqual([p.pk for p in first], [p.pk for p in second])  # same rows, not recreated

    def test_force_replaces_existing_pages(self):
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.png"), departamento="cocina")
        first = preprocess_document(document)
        second = preprocess_document(document, force=True)
        self.assertNotEqual(first[0].pk, second[0].pk)  # old row gone, a new one created
        self.assertEqual(document.pages.count(), 1)  # not accumulating duplicates