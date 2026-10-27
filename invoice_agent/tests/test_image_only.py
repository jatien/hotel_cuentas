"""Modo solo imagen (VISION_MODE=image_only, por defecto): el modelo de visión lee las páginas sin OCR ni texto."""

import io
import json
from unittest import mock

from django.core.management import call_command
from django.test import override_settings

from invoice_agent.models import Borrador, Documento
from invoice_agent.prompts import PROMPT_VERSION_IMAGE
from invoice_agent.providers.fake import FakeProvider
from invoice_agent.services import worker
from invoice_agent.services.dataset import export_dataset
from invoice_agent.services.extraction import ExtractionFailed, build_request, run_extraction
from invoice_agent.services.ingestion import ingest_upload
from invoice_agent.services.preparation import PreparationError, prepare
from invoice_agent.tests.helpers import (
    INVOICE_LINES,
    IsolatedTestCase,
    field,
    make_pdf,
    make_png,
    make_scanned_pdf,
    model_json,
    upload,
)


class NoOcr:
    """Si el modo solo imagen llamara a Tesseract, el test fallaría aquí."""

    def read(self, image):
        raise AssertionError("El modo solo imagen no debe usar OCR")

    rotation = read


class ImageOnlyTestCase(IsolatedTestCase):
    agent_settings = {"VISION_MODE": "image_only"}


class PrepareImageOnlyTests(ImageOnlyTestCase):
    def test_scanned_pdf_without_ocr(self):
        prepared = prepare(make_scanned_pdf(self.work / "s.pdf", pages=2), "pdf", NoOcr(), True)
        self.assertTrue(prepared.image_only)
        self.assertEqual([page for page, _ in prepared.images], [1, 2])
        self.assertEqual(prepared.method, "vision")

    def test_digital_pdf_keeps_native_text_but_does_not_send_it(self):
        prepared = prepare(make_pdf(self.work / "f.pdf"), "pdf", NoOcr(), True)
        self.assertIn("F-2026/0042", prepared.pages[0].text)  # solo para buscar evidencias
        request = build_request(prepared, "Hotel Prueba S.A.", self.empresa.nif, with_images=True)
        self.assertNotIn("F-2026/0042", request.user_text)
        self.assertNotIn("Toallas", request.user_text)
        self.assertIn("1 página(s)", request.user_text)
        self.assertEqual(len(request.images), 1)

    def test_images(self):
        prepared = prepare(make_png(self.work / "foto.png"), "image", NoOcr(), True)
        self.assertEqual(len(prepared.images), 1)

    def test_text_only_model_is_refused_clearly(self):
        with self.assertRaises(PreparationError) as caught:
            prepare(make_pdf(self.work / "f.pdf"), "pdf", NoOcr(), False)
        self.assertEqual(caught.exception.code, "VISION_REQUIRED")
        prepared = prepare(make_pdf(self.work / "g.pdf"), "pdf", NoOcr(), True)
        with self.assertRaises(ExtractionFailed):
            run_extraction(prepared, empresa_nombre="x", empresa_nif="", provider=FakeProvider([], supports_vision=False))

    def test_extraction_records_the_image_prompt_version(self):
        prepared = prepare(make_pdf(self.work / "f.pdf"), "pdf", NoOcr(), True)
        result = run_extraction(prepared, empresa_nombre="Hotel", empresa_nif=self.empresa.nif,
                                provider=FakeProvider([model_json()]), sleep=lambda s: None)
        self.assertEqual(result.prompt_version, PROMPT_VERSION_IMAGE)


class ImageOnlyPipelineTests(ImageOnlyTestCase):
    def test_scanned_invoice_with_json_loads_without_tesseract(self):
        """El caso de FA-58/FA-59: escaneo + JSON, sin Tesseract instalado."""
        drive, labels = self.work / "drive", self.work / "json"
        drive.mkdir()
        labels.mkdir()
        make_scanned_pdf(drive / "FA-58.pdf")
        (labels / "FA-58.json").write_text(model_json(invoice_number=field("FA-58")), encoding="utf-8")
        with mock.patch("invoice_agent.services.preparation.TesseractEngine", NoOcr):
            call_command("invoice_agent_pipeline", str(drive), empresa=self.empresa.nif, labels=str(labels),
                         no_extract=True, stdout=io.StringIO())
        self.assertEqual(Documento.objects.get().estado, "pending_approval")

    def test_dataset_has_images_and_no_page_text(self):
        doc, _ = ingest_upload(upload(make_pdf(self.work / "a.pdf", lines=INVOICE_LINES + ["a"], pages=2)),
                               self.empresa.pk, self.ctx())
        worker.run_claimed(worker.claim_next("w"), provider=FakeProvider([model_json()]), ocr_engine=NoOcr(),
                           sleep=lambda s: None)
        self.assertEqual(Borrador.objects.get(documento=doc).documento.estado, "pending_approval")
        out = self.work / "dataset"
        export_dataset(out, val=0, test=0, ocr_engine=NoOcr(), include_pending=True)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["vision_mode"], "image_only")
        self.assertEqual(manifest["prompt_version"], PROMPT_VERSION_IMAGE)
        record = json.loads((out / "train.jsonl").read_text(encoding="utf-8"))
        user = record["messages"][1]["content"]
        # mismo orden que en producción: instrucción, y «Imagen de la página N:» + imagen por página
        self.assertEqual([part["type"] for part in user], ["text", "text", "image", "text", "image"])
        texts = " ".join(part["text"] for part in user if part["type"] == "text")
        self.assertNotIn("Toallas", texts)  # nada del contenido de la factura va como texto
        answer = json.loads(record["messages"][2]["content"][0]["text"])
        self.assertEqual(answer["gross_total"]["value"], "121.00")


class DefaultModeTests(IsolatedTestCase):
    def test_project_default_is_image_only(self):
        from invoice_agent.conf import DEFAULTS

        self.assertEqual(DEFAULTS["VISION_MODE"], "image_only")
        with override_settings(INVOICE_AGENT={}):
            prepared = prepare(make_scanned_pdf(self.work / "s.pdf"), "pdf", NoOcr(), True)
        self.assertTrue(prepared.image_only)
