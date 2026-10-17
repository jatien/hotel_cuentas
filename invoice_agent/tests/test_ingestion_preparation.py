"""Etapas A (subida segura) y B (preparación: texto nativo, OCR, visión, giro, multipágina)."""

import shutil
from unittest import skipUnless

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from invoice_agent.models import (
    AccionRevision,
    Documento,
    EstadoDocumento,
    EventoRevision,
    Trabajo,
)
from invoice_agent.services.errors import ServiceError
from invoice_agent.services.ingestion import ingest_upload
from invoice_agent.services.preparation import PreparationError, prepare
from invoice_agent.services.storage import path_for
from invoice_agent.tests.helpers import (
    FakeOcr,
    IsolatedTestCase,
    make_pdf,
    make_png,
    make_scanned_pdf,
    upload,
)

TESSERACT = shutil.which("tesseract")


def raw_malware_scanner(path):
    raise RuntimeError("EICAR")


class IngestionTests(IsolatedTestCase):
    def test_valid_pdf_creates_document_job_and_event_atomically(self):
        doc, created = ingest_upload(upload(make_pdf(self.work / "f.pdf")), self.empresa.pk, self.ctx())
        self.assertTrue(created)
        self.assertEqual(doc.estado, EstadoDocumento.QUEUED)
        self.assertEqual(doc.page_count, 1)
        self.assertEqual(len(doc.sha256), 64)
        self.assertTrue(path_for(doc.storage_key).is_file())
        self.assertNotIn("f.pdf", doc.storage_key)  # la ruta interna no usa el nombre original
        self.assertEqual(Trabajo.objects.filter(documento=doc).count(), 1)
        self.assertTrue(EventoRevision.objects.filter(documento=doc, accion=AccionRevision.UPLOADED).exists())

    def test_same_file_same_company_is_idempotent(self):
        path = make_pdf(self.work / "f.pdf")
        first, _ = ingest_upload(upload(path), self.empresa.pk, self.ctx())
        second, created = ingest_upload(upload(path, name="otra-copia.pdf"), self.empresa.pk, self.ctx())
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Trabajo.objects.count(), 1)

    def test_same_file_other_company_is_a_separate_document(self):
        path = make_pdf(self.work / "f.pdf")
        ingest_upload(upload(path), self.empresa.pk, self.ctx())
        _, created = ingest_upload(upload(path), self.otra.pk, self.ctx())
        self.assertTrue(created)

    def test_signature_must_match_extension(self):
        fake_pdf = SimpleUploadedFile("factura.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")
        with self.assertRaises(ServiceError) as raised:
            ingest_upload(fake_pdf, self.empresa.pk, self.ctx())
        self.assertEqual(raised.exception.code, "SIGNATURE_MISMATCH")
        png_as_pdf = upload(make_png(self.work / "x.png"), name="x.pdf", content_type="application/pdf")
        with self.assertRaises(ServiceError):
            ingest_upload(png_as_pdf, self.empresa.pk, self.ctx())

    def test_declared_mime_must_match(self):
        with self.assertRaises(ServiceError) as raised:
            ingest_upload(upload(make_pdf(self.work / "f.pdf"), content_type="text/html"), self.empresa.pk, self.ctx())
        self.assertEqual(raised.exception.code, "MIME_MISMATCH")

    def test_unsupported_extension(self):
        with self.assertRaises(ServiceError) as raised:
            ingest_upload(SimpleUploadedFile("a.exe", b"MZ\x90\x00", "application/octet-stream"), self.empresa.pk, self.ctx())
        self.assertEqual(raised.exception.status, 415)

    @override_settings()
    def test_oversized_upload(self):
        from django.conf import settings

        settings.INVOICE_AGENT = {**settings.INVOICE_AGENT, "MAX_UPLOAD_BYTES": 100}
        with self.assertRaises(ServiceError) as raised:
            ingest_upload(upload(make_pdf(self.work / "f.pdf")), self.empresa.pk, self.ctx())
        self.assertEqual(raised.exception.status, 413)
        self.assertEqual(Documento.objects.count(), 0)

    def test_corrupt_pdf(self):
        broken = SimpleUploadedFile("roto.pdf", b"%PDF-1.4\n esto no es un pdf", "application/pdf")
        with self.assertRaises(ServiceError) as raised:
            ingest_upload(broken, self.empresa.pk, self.ctx())
        self.assertEqual(raised.exception.code, "PDF_UNREADABLE")

    def test_too_many_pages(self):
        from django.conf import settings

        with override_settings(INVOICE_AGENT={**settings.INVOICE_AGENT, "MAX_PAGES": 2}):
            with self.assertRaises(ServiceError) as raised:
                ingest_upload(upload(make_pdf(self.work / "f.pdf", pages=3)), self.empresa.pk, self.ctx())
        self.assertEqual(raised.exception.code, "TOO_MANY_PAGES")

    def test_malware_hook_rejects(self):
        from django.conf import settings

        scanner = f"{__name__}.raw_malware_scanner"
        with override_settings(INVOICE_AGENT={**settings.INVOICE_AGENT, "MALWARE_SCANNER": scanner}):
            with self.assertRaises(ServiceError) as raised:
                ingest_upload(upload(make_pdf(self.work / "f.pdf")), self.empresa.pk, self.ctx())
        self.assertEqual(raised.exception.code, "MALWARE_DETECTED")
        self.assertEqual(Documento.objects.count(), 0)

    def test_company_not_in_context_is_not_found(self):
        with self.assertRaises(ServiceError) as raised:
            ingest_upload(upload(make_pdf(self.work / "f.pdf")), self.otra.pk, self.ctx(self.empresa))
        self.assertEqual(raised.exception.status, 404)


class PreparationTests(IsolatedTestCase):
    def test_digital_pdf_uses_native_text(self):
        engine = FakeOcr()
        result = prepare(make_pdf(self.work / "f.pdf"), "pdf", engine)
        self.assertEqual(result.method, "native")
        self.assertIn("F-2026/0042", result.pages[0].text)
        self.assertEqual(engine.calls, 0)

    def test_scanned_multipage_pdf_uses_ocr_and_keeps_page_numbers(self):
        result = prepare(make_scanned_pdf(self.work / "s.pdf", pages=2), "pdf", FakeOcr())
        self.assertEqual(result.method, "ocr")
        self.assertEqual([p.number for p in result.pages], [1, 2])
        self.assertEqual(result.images, [])

    def test_poor_ocr_attaches_downscaled_image_only_when_vision_is_supported(self):
        engine = FakeOcr(text="?? ilegible", confidence=31.0)
        result = prepare(make_scanned_pdf(self.work / "s.pdf"), "pdf", engine, vision_supported=True)
        self.assertEqual(result.method, "vision")
        page, jpeg = result.images[0]
        self.assertEqual(page, 1)
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(jpeg)) as image:
            self.assertLessEqual(max(image.size), 1600)
        self.assertTrue(result.pages[0].problems)
        without = prepare(make_scanned_pdf(self.work / "s2.pdf"), "pdf", engine, vision_supported=False)
        self.assertEqual(without.images, [])
        self.assertEqual(without.method, "ocr")

    def test_rotated_page_is_reported(self):
        result = prepare(make_png(self.work / "r.png"), "image", FakeOcr(rotation=90))
        self.assertEqual(result.pages[0].rotation, 90)
        self.assertTrue(any("girada" in p for p in result.pages[0].problems))
        self.assertEqual(result.page_problems()[0]["page"], 1)

    def test_metadata_has_no_document_text(self):
        result = prepare(make_pdf(self.work / "f.pdf"), "pdf", FakeOcr())
        self.assertNotIn("F-2026/0042", str(result.metadata()))

    def test_missing_original(self):
        with self.assertRaises(PreparationError) as raised:
            prepare(self.work / "no-existe.pdf", "pdf", FakeOcr())
        self.assertFalse(raised.exception.retryable)

    def test_ocr_engine_failure_is_retryable(self):
        class Broken(FakeOcr):
            def read(self, image):
                raise RuntimeError("tesseract no instalado")

        with self.assertRaises(PreparationError) as raised:
            prepare(make_scanned_pdf(self.work / "s.pdf"), "pdf", Broken())
        self.assertTrue(raised.exception.retryable)

    @skipUnless(TESSERACT, "Tesseract no instalado")
    def test_real_tesseract_on_scanned_page(self):
        from invoice_agent.services.preparation import TesseractEngine

        with override_settings(TESSERACT_CMD=TESSERACT, INVOICE_OCR_LANG="eng"):
            result = prepare(make_scanned_pdf(self.work / "s.pdf"), "pdf", TesseractEngine())
        self.assertEqual(result.pages[0].method, "ocr")
