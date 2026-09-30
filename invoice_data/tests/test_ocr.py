"""Tests of full-page OCR (scans and photos). Skipped when Tesseract is not available on the machine."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from invoice_data.models import IngestionStatus
from invoice_data.services.ingestion import ingest_file
from invoice_data.services.ocr import OcrWord, low_confidence_words, ocr_words, run_ocr, words_to_text
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


class WordsToTextTests(SimpleTestCase):
    """
    Reading-order text reconstruction from positioned words - pure logic,
    no Tesseract needed, so these always run (unlike OCRTests above).
    """

    def word(self, text, x, y, height=20, confidence=90, page=0):
        """Shorthand for building one OcrWord in these tests."""
        return OcrWord(page, text, x, y, width=len(text) * 10, height=height, confidence=confidence)

    def test_two_lines_are_grouped_and_ordered_correctly(self):
        words = [
            self.word("Hola", x=10, y=100),
            self.word("mundo", x=60, y=103),  # same line: only 3px lower
            self.word("Segunda", x=10, y=140),
            self.word("linea", x=80, y=138),  # same line as 'Segunda', but a lower y - naturally
        ]  # occurring OCR stagger: must still land on the same output line, in x order
        self.assertEqual(words_to_text(words), "Hola mundo\nSegunda linea")

    def test_three_consecutive_lines_regression(self):
        """
        Regression: a line-grouping variable wasn't resetting between
        groups, so every line after the first was compared against the
        FIRST line's position instead of its own - silently merging or
        splitting lines wrong from the third line onward.
        """
        words = [
            self.word("Uno", x=10, y=100),
            self.word("Dos", x=10, y=140),
            self.word("Tres", x=10, y=180),
        ]
        self.assertEqual(words_to_text(words), "Uno\nDos\nTres")

    def test_grouping_threshold_is_relative_to_word_height(self):
        """A gap that is small for tall text can still be a real new line for short text, and vice versa."""
        small_text = [self.word("A", x=10, y=100, height=10), self.word("B", x=10, y=107, height=10)]
        self.assertEqual(words_to_text(small_text), "A\nB")  # 7px gap, but only 10px tall: a real new line

        big_text = [self.word("A", x=10, y=100, height=60), self.word("B", x=10, y=115, height=60)]
        self.assertEqual(words_to_text(big_text), "A B")  # 15px gap, but 60px tall: still the same line

    def test_pages_are_joined_with_a_blank_line_and_kept_separate(self):
        words = [self.word("Pagina uno", x=10, y=100, page=0), self.word("Pagina dos", x=10, y=100, page=1)]
        self.assertEqual(words_to_text(words), "Pagina uno\n\nPagina dos")

    def test_empty_input_gives_empty_text(self):
        self.assertEqual(words_to_text([]), "")


class LowConfidenceWordsTests(SimpleTestCase):
    """Flagging words Tesseract itself was unsure about."""

    def test_only_words_below_the_threshold_are_returned(self):
        words = [
            OcrWord(0, "seguro", 0, 0, 10, 10, confidence=95),
            OcrWord(0, "dudoso", 0, 0, 10, 10, confidence=30),
        ]
        self.assertEqual([w.text for w in low_confidence_words(words, threshold=50)], ["dudoso"])

    def test_default_threshold_is_used_when_not_specified(self):
        words = [OcrWord(0, "x", 0, 0, 10, 10, confidence=10)]
        self.assertEqual(len(low_confidence_words(words)), 1)


@unittest.skipUnless(tesseract_available(), "Tesseract no está instalado: test omitido.")
class OcrWordsTests(MediaIsolatedTestCase):
    """ocr_words() against real Tesseract: position and confidence actually come back populated."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_words_carry_real_position_and_confidence(self):
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.png", "FACTURA PRUEBA"), departamento="lavanderia")
        words = ocr_words(document)
        self.assertTrue(words)
        self.assertTrue(any("FACTURA" in w.text.upper() for w in words))
        for w in words:
            self.assertGreaterEqual(w.x, 0)
            self.assertGreaterEqual(w.y, 0)
            self.assertGreater(w.height, 0)
            self.assertGreaterEqual(w.confidence, 0)

    def test_a_digital_document_returns_no_ocr_words(self):
        """No OCR runs on a digital PDF, so there is nothing positioned to return."""
        document, _ = ingest_file(make_invoice_pdf(self.tmp_dir / "d.pdf"), departamento="lavanderia")
        self.assertEqual(ocr_words(document), [])

    def test_run_ocr_text_is_consistent_with_ocr_words(self):
        """run_ocr()'s stored text is words_to_text() of exactly what ocr_words() finds - same source, two views."""
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.png", "FACTURA PRUEBA"), departamento="lavanderia")
        text = run_ocr(document)
        words = ocr_words(document)
        self.assertEqual(text, words_to_text(words))