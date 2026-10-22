"""Modo visión, importación de carpetas y exportación del dataset de entrenamiento."""

import io
import json
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.test import override_settings

from invoice_agent.models import Borrador, Documento
from invoice_agent.prompts import DOC_START
from invoice_agent.providers.fake import FakeProvider
from invoice_agent.schemas import DecisionIn, DraftPatch, ExtractionOutput
from invoice_agent.services import review, worker
from invoice_agent.services.dataset import (
    build_target,
    export_dataset,
    find_evidence,
    number_text,
    search_candidates,
    split_for,
)
from invoice_agent.services.ingestion import ingest_upload
from invoice_agent.services.preparation import PreparationError, prepare
from invoice_agent.tests.helpers import (
    INVOICE_LINES,
    FakeOcr,
    IsolatedTestCase,
    field,
    make_pdf,
    make_png,
    make_scanned_pdf,
    model_json,
    upload,
)


class VisionModeTests(IsolatedTestCase):
    def test_always_attaches_every_page_including_native_text(self):
        result = prepare(make_pdf(self.work / "f.pdf", pages=2), "pdf", FakeOcr(), True, "always")
        self.assertEqual([page for page, _ in result.images], [1, 2])
        self.assertIn("F-2026/0042", result.pages[0].text)  # el texto nativo se sigue enviando
        good_scan = prepare(make_scanned_pdf(self.work / "s.pdf"), "pdf", FakeOcr(), True, "always")
        self.assertEqual(len(good_scan.images), 1)

    def test_auto_and_never(self):
        auto = prepare(make_pdf(self.work / "f.pdf"), "pdf", FakeOcr(), True, "auto")
        self.assertEqual(auto.images, [])
        poor = FakeOcr(text="??", confidence=20)
        never = prepare(make_scanned_pdf(self.work / "s.pdf"), "pdf", poor, True, "never")
        self.assertEqual(never.images, [])

    def test_always_without_vision_support_sends_no_images(self):
        result = prepare(make_pdf(self.work / "f.pdf"), "pdf", FakeOcr(), False, "always")
        self.assertEqual(result.images, [])

    def test_invalid_mode(self):
        with self.assertRaises(PreparationError):
            prepare(make_pdf(self.work / "f.pdf"), "pdf", FakeOcr(), True, "a veces")


class ImportCommandTests(IsolatedTestCase):
    def test_imports_folder_idempotently(self):
        folder = self.work / "lote"
        (folder / "sub").mkdir(parents=True)
        make_pdf(folder / "a.pdf", lines=INVOICE_LINES + ["a"])
        make_png(folder / "sub" / "b.png")
        (folder / "notas.txt").write_text("no es una factura")
        out = io.StringIO()
        call_command("invoice_agent_import", str(folder), empresa=self.empresa.nif, recursive=True, stdout=out)
        self.assertIn("2 en cola", out.getvalue())
        call_command("invoice_agent_import", str(folder), empresa=self.empresa.nif, recursive=True, stdout=out)
        self.assertEqual(Documento.objects.count(), 2)
        self.assertIn("2 ya estaban", out.getvalue())


class LabelImportTests(IsolatedTestCase):
    def test_import_with_labels_goes_straight_to_review(self):
        folder, labels = self.work / "pdf", self.work / "json"
        folder.mkdir()
        labels.mkdir()
        make_pdf(folder / "f1.pdf", lines=INVOICE_LINES + ["1"])
        make_pdf(folder / "f2.pdf", lines=INVOICE_LINES + ["2"])
        make_pdf(folder / "f3.pdf", lines=INVOICE_LINES + ["3"])
        (labels / "f1.json").write_text(model_json(customer_tax_id=field(self.empresa.nif)), encoding="utf-8")
        (labels / "f2.json").write_text("{no es json", encoding="utf-8")
        out = io.StringIO()
        from unittest import mock

        with mock.patch("invoice_agent.services.preparation.TesseractEngine", FakeOcr):
            call_command("invoice_agent_import", str(folder), empresa=self.empresa.nif, labels=str(labels), stdout=out)
        states = {d.original_filename: d.estado for d in Documento.objects.all()}
        self.assertEqual(states["f1.pdf"], "pending_approval")
        self.assertEqual(states["f2.pdf"], "queued")  # etiqueta mala: reintento programado; nunca entra un dato inventado
        self.assertEqual(states["f3.pdf"], "queued")  # sin etiqueta: espera al worker
        self.assertEqual(Borrador.objects.get(documento__original_filename="f1.pdf").total_bruto, Decimal("121.00"))
        self.assertIn("2 con etiqueta", out.getvalue())


class HelperTests(IsolatedTestCase):
    def test_number_text(self):
        self.assertEqual(number_text(Decimal("10.0000")), "10")
        self.assertEqual(number_text(Decimal("8.000000")), "8")
        self.assertEqual(number_text(Decimal("2.50")), "2.5")
        self.assertEqual(number_text(Decimal("100")), "100")
        self.assertEqual(number_text(Decimal("121"), 2), "121.00")
        self.assertIsNone(number_text(None))

    def test_evidence_search_finds_spanish_formats(self):
        pages = [(1, "cabecera"), (2, "Base 1.234,56\nTOTAL  1.493,82 €")]
        page, text = find_evidence(search_candidates("total_bruto", Decimal("1493.82")), pages)
        self.assertEqual((page, text), (2, "TOTAL 1.493,82 €"))
        from datetime import date

        page, _ = find_evidence(search_candidates("fecha_factura", date(2026, 9, 15)), [(1, "Fecha: 15/09/2026")])
        self.assertEqual(page, 1)
        self.assertEqual(find_evidence(search_candidates("numero_factura", "ZZ-9"), pages), (None, None))

    def test_split_is_deterministic_and_respects_fractions(self):
        keys = [f"k{i}" for i in range(2000)]
        splits = [split_for(k, 0.1, 0.15) for k in keys]
        self.assertEqual(splits, [split_for(k, 0.1, 0.15) for k in keys])
        self.assertAlmostEqual(splits.count("test") / 2000, 0.15, delta=0.03)
        self.assertAlmostEqual(splits.count("val") / 2000, 0.10, delta=0.03)


class ExportTests(IsolatedTestCase):
    def approved(self, name, output=None, correct=None):
        path = make_pdf(self.work / name, lines=INVOICE_LINES + [name])
        doc, _ = ingest_upload(upload(path), self.empresa.pk, self.ctx())
        claimed = worker.claim_next("w")
        worker.run_claimed(claimed, provider=FakeProvider([output or model_json()]), ocr_engine=FakeOcr(),
                           sleep=lambda s: None)
        borrador = Borrador.objects.get(documento=doc)
        if correct:
            review.apply_patch(self.ctx(), borrador, DraftPatch(**correct))
        borrador.refresh_from_db()
        warnings = [i["code"] for i in borrador.incidencias if i["severity"] == "warning" and i["blocking"]]
        if warnings:  # p. ej. «misma fecha y total que otra factura» en facturas de prueba casi idénticas
            review.apply_patch(self.ctx(), borrador, DraftPatch(aceptar_avisos=warnings))
        review.approve(self.ctx(), Borrador.objects.get(pk=borrador.pk), DecisionIn())
        return Borrador.objects.get(pk=borrador.pk)

    def test_target_uses_reviewed_values_not_model_output(self):
        borrador = self.approved("a.pdf", model_json(gross_total=field("150.00")), {"total_bruto": "121.00"})
        target = build_target(borrador, [(1, "\n".join(INVOICE_LINES))])
        ExtractionOutput.model_validate(target)
        self.assertEqual(target["gross_total"]["value"], "121.00")
        self.assertEqual(target["gross_total"]["evidence"]["page"], 1)  # corregido: evidencia buscada en el texto
        self.assertIn("121,00", target["gross_total"]["evidence"]["text"])
        self.assertEqual(target["due_date"]["status"], "absent")
        self.assertEqual(target["line_items"][0]["quantity"], "10")
        self.assertEqual(target["tax_breakdown"][0]["rate"], "21")

    def test_export_writes_jsonl_images_and_test_cases(self):
        for index in range(12):
            self.approved(f"f{index}.pdf", model_json(invoice_number=field(f"F-{index}")))
        out = self.work / "dataset_x"
        report = export_dataset(out, val=0.2, test=0.3, ocr_engine=FakeOcr())
        self.assertEqual(sum(report.counts.values()), 12)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["prompt_version"], "factura-ia/1.0")
        self.assertEqual(manifest["counts"], report.counts)

        train = [json.loads(line) for line in (out / "train.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(train), report.counts["train"])
        record = train[0]
        roles = [m["role"] for m in record["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant"])
        user = record["messages"][1]["content"]
        self.assertIn(DOC_START, user[0]["text"])
        image = next(c for c in user if c["type"] == "image")["image"]
        self.assertTrue((out / image).is_file())
        answer = json.loads(record["messages"][2]["content"][0]["text"])
        ExtractionOutput.model_validate(answer)

        test_cases = list((out / "test").glob("*.json"))
        self.assertEqual(len(test_cases), report.counts["test"])
        if test_cases:
            truth = json.loads(test_cases[0].read_text(encoding="utf-8"))
            self.assertEqual(truth["empresa"]["nif"], self.empresa.nif)
            self.assertTrue(test_cases[0].with_suffix(".pdf").is_file())

    def test_only_approved_are_exported_and_folder_is_protected(self):
        self.approved("a.pdf")
        path = make_pdf(self.work / "pend.pdf", lines=INVOICE_LINES + ["pendiente"])
        ingest_upload(upload(path), self.empresa.pk, self.ctx())  # en cola, sin aprobar
        out = self.work / "dataset_y"
        report = export_dataset(out, val=0, test=0, ocr_engine=FakeOcr())
        self.assertEqual(report.counts, {"train": 1, "val": 0, "test": 0})
        with self.assertRaises(FileExistsError):
            export_dataset(out, ocr_engine=FakeOcr())

    def test_exported_prompt_matches_production_request(self):
        """El modelo afinado debe ver en producción exactamente el mismo texto que al entrenar."""
        self.approved("a.pdf")
        out = self.work / "dataset_z"
        export_dataset(out, val=0, test=0, ocr_engine=FakeOcr())
        record = json.loads((out / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
        provider = FakeProvider([model_json()])
        from invoice_agent.services.extraction import run_extraction

        borrador = Borrador.objects.get()
        source = Path(__import__("invoice_agent.services.storage", fromlist=["x"]).path_for(borrador.documento.storage_key))
        prepared = prepare(source, "pdf", FakeOcr(), True, "always")
        run_extraction(prepared, empresa_nombre=self.empresa.nombre, empresa_nif=self.empresa.nif, provider=provider,
                       sleep=lambda s: None)
        sent = provider.requests[0]
        self.assertEqual(record["messages"][0]["content"][0]["text"], sent.system)
        self.assertEqual(record["messages"][1]["content"][0]["text"], sent.user_text)
        self.assertEqual(len([c for c in record["messages"][1]["content"] if c["type"] == "image"]), len(sent.images))

    @override_settings()
    def test_command(self):
        self.approved("a.pdf")
        out = io.StringIO()
        from unittest import mock

        with mock.patch("invoice_agent.services.dataset.prepare",
                        side_effect=lambda *a, **k: prepare(*a[:2], FakeOcr(), True, "always")):
            call_command("invoice_agent_export_dataset", str(self.work / "dataset_cmd"), val=0, test=0, stdout=out)
        self.assertIn("train 1", out.getvalue())
        self.assertIn("Menos de 50", out.getvalue())
