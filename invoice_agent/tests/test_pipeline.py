"""Profesor (LLM grande) → etiquetas JSON → dataset: invoice_agent_pipeline y sus piezas."""

import io
import json
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import override_settings

from invoice_agent.models import Borrador, Documento
from invoice_agent.providers import get_teacher_provider, use_provider
from invoice_agent.providers.fake import FakeProvider
from invoice_agent.schemas import DecisionIn, DraftPatch
from invoice_agent.services import review, worker
from invoice_agent.services.dataset import export_dataset
from invoice_agent.services.ingestion import ingest_upload
from invoice_agent.services.labels import export_labels
from invoice_agent.tests.helpers import (
    INVOICE_LINES,
    FakeOcr,
    IsolatedTestCase,
    field,
    make_pdf,
    model_json,
    model_output,
    upload,
)

TESSERACT = "invoice_agent.services.preparation.TesseractEngine"


class TeacherProviderTests(IsolatedTestCase):
    base = {"PROVIDER": "openai", "MODEL": "factura-qwen", "BASE_URL": "http://localhost:8000/v1", "API_KEY": "local"}

    def test_without_teacher_uses_the_normal_provider(self):
        with override_settings(INVOICE_AGENT=self.base):
            self.assertEqual(get_teacher_provider().model, "factura-qwen")

    def test_teacher_settings_and_keys_are_not_sent_to_another_server(self):
        settings = {**self.base, "TEACHER_MODEL": "grande", "TEACHER_BASE_URL": "https://ejemplo.invalid/v1"}
        with override_settings(INVOICE_AGENT=settings):
            teacher = get_teacher_provider()
        self.assertEqual(teacher.model, "grande")
        self.assertEqual(str(teacher.client.base_url).rstrip("/"), "https://ejemplo.invalid/v1")
        self.assertNotEqual(teacher.client.api_key, "local")
        with override_settings(INVOICE_AGENT={**settings, "TEACHER_API_KEY": "clave-profesor"}):
            self.assertEqual(get_teacher_provider().client.api_key, "clave-profesor")


class PipelineTestCase(IsolatedTestCase):
    def extracted(self, name, output):
        """Factura etiquetada por el «profesor» (queda pendiente de aprobación o necesita revisión)."""
        doc, _ = ingest_upload(upload(make_pdf(self.work / name, lines=INVOICE_LINES + [name])), self.empresa.pk, self.ctx())
        worker.run_claimed(worker.claim_next("w"), provider=FakeProvider([output]), ocr_engine=FakeOcr(),
                           sleep=lambda s: None)
        return Borrador.objects.select_related("documento").get(documento=doc)


class IncludePendingTests(PipelineTestCase):
    def test_pending_only_with_flag_and_never_needs_review(self):
        good = self.extracted("buena.pdf", model_json(invoice_number=field("F-1")))
        bad = self.extracted("mala.pdf", model_json(invoice_number=field("F-2"), gross_total=field("999.00")))
        self.assertEqual(good.documento.estado, "pending_approval")
        self.assertEqual(bad.documento.estado, "needs_review")

        default = export_dataset(self.work / "d1", val=0, test=0, ocr_engine=FakeOcr())
        self.assertEqual(default.counts["train"], 0)  # sin la opción, solo aprobadas

        report = export_dataset(self.work / "d2", val=0, test=0, ocr_engine=FakeOcr(), include_pending=True)
        self.assertEqual(report.counts["train"], 1)
        self.assertEqual(report.label_sources["teacher"], 1)
        record = json.loads((self.work / "d2" / "train.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["label_source"], "teacher")
        manifest = json.loads((self.work / "d2" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["label_sources"], {"teacher": 1})


class ExportLabelsTests(PipelineTestCase):
    def test_writes_teacher_output_and_keeps_existing_files(self):
        borrador = self.extracted("FA-1.pdf", model_json(invoice_number=field("FA-1")))
        folder = self.work / "json"
        report = export_labels(folder)
        self.assertEqual(report.written, ["FA-1.json"])
        written = json.loads((folder / "FA-1.json").read_text(encoding="utf-8"))
        self.assertEqual(written, model_output(invoice_number=field("FA-1")))
        self.assertEqual(written, borrador.extraccion.salida_modelo)

        (folder / "FA-1.json").write_text('{"original": "del usuario"}', encoding="utf-8")
        again = export_labels(folder)
        self.assertEqual((again.written, again.kept), ([], 1))
        self.assertIn("del usuario", (folder / "FA-1.json").read_text(encoding="utf-8"))

    def test_human_correction_replaces_the_label(self):
        borrador = self.extracted("FA-2.pdf", model_json(invoice_number=field("FA-2")))
        folder = self.work / "json"
        export_labels(folder)
        review.apply_patch(self.ctx(), borrador, DraftPatch(numero_factura="FA-2B"))
        review.approve(self.ctx(), Borrador.objects.get(pk=borrador.pk), DecisionIn())
        report = export_labels(folder)
        self.assertEqual(report.replaced, ["FA-2.json"])
        label = json.loads((folder / "FA-2.json").read_text(encoding="utf-8"))
        self.assertEqual(label["invoice_number"]["value"], "FA-2B")
        self.assertEqual(export_labels(folder).kept, 1)  # sin cambios: no se reescribe

    def test_needs_review_is_not_written(self):
        self.extracted("FA-3.pdf", model_json(invoice_number=field("FA-3"), gross_total=field("999.00")))
        self.assertEqual(export_labels(self.work / "json").written, [])


class PipelineCommandTests(PipelineTestCase):
    def test_full_cycle_is_idempotent(self):
        drive, labels = self.work / "drive", self.work / "drive" / "Invoices_json"
        (drive / "FA-2020").mkdir(parents=True)
        labels.mkdir()
        make_pdf(drive / "con_json.pdf", lines=INVOICE_LINES + ["1"])
        make_pdf(drive / "FA-2020" / "sin_json.pdf", lines=INVOICE_LINES + ["2"])
        (labels / "con_json.json").write_text(
            model_json(invoice_number=field("A-1"), customer_tax_id=field(self.empresa.nif)), encoding="utf-8")
        # otra fecha: con la misma fecha y total sería un «posible duplicado» y pasaría a revisión
        teacher = FakeProvider([model_json(invoice_number=field("B-2"), invoice_date=field("2026-09-20"))])
        out = io.StringIO()
        with mock.patch(TESSERACT, FakeOcr), use_provider(teacher):
            call_command("invoice_agent_pipeline", str(drive), empresa=self.empresa.nif, labels=str(labels),
                         dataset=str(self.work / "dataset"), val=0, test=0, stdout=out)
        self.assertEqual(len(teacher.requests), 1)  # solo la que no tenía JSON
        self.assertTrue((labels / "sin_json.json").is_file())
        self.assertIn("train 2", out.getvalue())
        self.assertEqual(Borrador.objects.get(numero_factura="B-2").total_bruto, Decimal("121.00"))

        out = io.StringIO()
        with mock.patch(TESSERACT, FakeOcr), use_provider(FakeProvider([])):
            call_command("invoice_agent_pipeline", str(drive), empresa=self.empresa.nif, labels=str(labels),
                         dataset=str(self.work / "dataset"), val=0, test=0, stdout=out)
        self.assertIn("0 facturas enviadas al profesor", out.getvalue())
        self.assertEqual(Documento.objects.count(), 2)

    def test_max_extract_and_no_extract(self):
        drive = self.work / "drive"
        drive.mkdir()
        for index in range(3):
            make_pdf(drive / f"f{index}.pdf", lines=INVOICE_LINES + [str(index)])
        teacher = FakeProvider([model_json(invoice_number=field(f"N-{i}")) for i in range(3)])
        with mock.patch(TESSERACT, FakeOcr), use_provider(teacher):
            call_command("invoice_agent_pipeline", str(drive), empresa=self.empresa.nif,
                         labels=str(self.work / "json"), no_extract=True, stdout=io.StringIO())
            self.assertEqual(len(teacher.requests), 0)
            call_command("invoice_agent_pipeline", str(drive), empresa=self.empresa.nif,
                         labels=str(self.work / "json"), max_extract=2, stdout=io.StringIO())
        self.assertEqual(len(teacher.requests), 2)
        self.assertEqual(Documento.objects.filter(estado="queued").count(), 1)


class MissingTesseractTests(PipelineTestCase):
    def test_missing_tesseract_is_retryable_not_unreadable(self):
        from pytesseract import TesseractNotFoundError

        from invoice_agent.services.preparation import PreparationError, prepare
        from invoice_agent.tests.helpers import make_scanned_pdf

        class NoTesseract(FakeOcr):
            def read(self, image):
                raise TesseractNotFoundError()

        with self.assertRaises(PreparationError) as caught:
            prepare(make_scanned_pdf(self.work / "escaneo.pdf"), "pdf", NoTesseract(), False, "auto")
        self.assertEqual(caught.exception.code, "OCR_NOT_INSTALLED")
        self.assertTrue(caught.exception.retryable)

    def test_retry_failed_reloads_with_the_label(self):
        drive, labels = self.work / "drive", self.work / "json"
        drive.mkdir()
        labels.mkdir()
        make_pdf(drive / "FA-58.pdf", lines=INVOICE_LINES + ["58"])
        (labels / "FA-58.json").write_text(model_json(invoice_number=field("FA-58")), encoding="utf-8")
        args = dict(empresa=self.empresa.nif, labels=str(labels), no_extract=True, stdout=io.StringIO())

        from invoice_agent.services.preparation import PreparationError

        # Lo que pasaba antes con Tesseract ausente: error definitivo en la primera ejecución
        broken = mock.Mock(side_effect=PreparationError("DOCUMENT_UNREADABLE", "No se puede leer", False))
        with mock.patch(TESSERACT, FakeOcr), mock.patch("invoice_agent.services.worker.prepare", broken):
            call_command("invoice_agent_pipeline", str(drive), **args)
        self.assertEqual(Documento.objects.get().estado, "failed")

        with mock.patch(TESSERACT, FakeOcr):
            call_command("invoice_agent_pipeline", str(drive), **args)  # sin --retry-failed: sigue en error
            self.assertEqual(Documento.objects.get().estado, "failed")
            out = io.StringIO()
            call_command("invoice_agent_pipeline", str(drive), retry_failed=True, **{**args, "stdout": out})
        self.assertEqual(Documento.objects.get().estado, "pending_approval")
        self.assertIn("1 facturas con error vuelven a la cola", out.getvalue())
        self.assertEqual(Borrador.objects.get().numero_factura, "FA-58")
