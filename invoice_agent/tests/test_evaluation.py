"""Evaluación: la tubería con el oráculo da 100 % y las métricas se calculan bien."""

import io
import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from invoice_agent.evaluation.metrics import CaseResult, summarize
from invoice_agent.evaluation.runner import OracleProvider, evaluate_folder
from invoice_agent.providers.fake import FakeProvider
from invoice_agent.tests.helpers import FakeOcr, model_json


class EvaluationTests(SimpleTestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="invoice_agent_eval_"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        call_command("invoice_agent_evaluate", build_demo=str(self.folder), stdout=io.StringIO())

    def test_oracle_scores_perfectly(self):
        report = evaluate_folder(self.folder, OracleProvider(), ocr_engine=FakeOcr())
        self.assertEqual(report["documents"], 5)
        self.assertEqual(report["failures"], 0)
        for name, metrics in report["fields"].items():
            self.assertEqual(metrics["exact_match"], 1.0, name)
        self.assertEqual(report["documents_metrics"]["reconciliation_accuracy"], 1.0)
        self.assertEqual(report["documents_metrics"]["taxes_exact_match"], 1.0)
        self.assertEqual(report["duplicates"], {"pairs_expected": 1, "pairs_detected": 1, "precision": 1.0, "recall": 1.0})

    @override_settings(INVOICE_AGENT={"PROVIDER_MAX_RETRIES": 0, "MALFORMED_OUTPUT_RETRIES": 0,
                                      "PRICE_INPUT_PER_MTOK": "2", "PRICE_OUTPUT_PER_MTOK": "8"})
    def test_model_errors_and_costs_are_measured(self):
        # El mismo JSON para los 5 casos: acierta el 01 y el 04 (duplicados) y falla el resto.
        provider = FakeProvider([model_json()] * 4 + ["no json"])
        report = evaluate_folder(self.folder, provider, ocr_engine=FakeOcr())
        self.assertEqual(report["failures"], 1)
        self.assertLess(report["fields"]["invoice_number"]["exact_match"], 1.0)
        self.assertEqual(report["cost_latency"]["input_tokens"], 4000)
        self.assertEqual(Decimal(report["cost_latency"]["cost_total"]), Decimal("0.024"))  # 4×(1000×2 + 500×8)/1e6

    def test_precision_and_recall_definitions(self):
        cases = [
            CaseResult("a", {"invoice_number": "1"}, {"invoice_number": "1"}, [], 0, None, [], None),
            CaseResult("b", {"invoice_number": "9"}, {"invoice_number": "2"}, [], 0, None, [], None),
            CaseResult("c", {"invoice_number": None}, {"invoice_number": "3"}, [], 0, None, [], None),
            CaseResult("d", {"invoice_number": None}, {"invoice_number": None}, [], 0, None, [], None),
        ]
        metrics = summarize(cases).fields["invoice_number"]
        self.assertEqual(metrics, {"exact_match": 0.5, "precision": 0.5, "recall": round(1 / 3, 4), "n": 4})

    def test_dataset_is_fictitious_and_complete(self):
        for truth in self.folder.glob("*.json"):
            data = json.loads(truth.read_text(encoding="utf-8"))
            self.assertIn("fields", data)
            self.assertTrue((self.folder / f"{truth.stem}.pdf").exists())


class DriveLabelCasesTests(SimpleTestCase):
    """Una carpeta con pares PDF + JSON de Google Drive (esquema factura-ia/1) se evalúa tal cual."""

    def test_drive_label_is_converted_and_oracle_scores_perfectly(self):
        from invoice_agent.tests.helpers import field, make_pdf, model_json

        folder = Path(tempfile.mkdtemp(prefix="invoice_agent_eval_drive_"))
        self.addCleanup(shutil.rmtree, folder, True)
        make_pdf(folder / "FA-1.pdf")
        (folder / "FA-1.json").write_text(model_json(due_date=field(None)), encoding="utf-8")
        report = evaluate_folder(folder, OracleProvider(), ocr_engine=FakeOcr())
        self.assertEqual((report["documents"], report["failures"]), (1, 0))
        self.assertEqual(report["fields"]["gross_total"]["exact_match"], 1.0)
        self.assertEqual(report["fields"]["supplier_tax_id"]["exact_match"], 1.0)


class CompareReportsTests(SimpleTestCase):
    """Base frente a afinado, los dos contra el JSON de referencia; solo informes, nada se guarda."""

    def setUp(self):
        from invoice_agent.tests.helpers import field, make_pdf, model_json

        self.folder = Path(tempfile.mkdtemp(prefix="invoice_agent_cmp_"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        for name in ("FA-1", "FA-2"):
            make_pdf(self.folder / f"{name}.pdf")
            (self.folder / f"{name}.json").write_text(model_json(due_date=field(None)), encoding="utf-8")
        self.field, self.model_json = field, model_json

    def run_model(self, answers):
        from invoice_agent.providers.fake import FakeProvider

        return evaluate_folder(self.folder, FakeProvider(answers, model="factura-qwen"), ocr_engine=FakeOcr())

    def test_diffs_fixed_regressed_and_html(self):
        from invoice_agent.evaluation.compare import compare, render_html, render_text

        right, f = self.model_json(due_date=self.field(None)), self.field
        # base: FA-1 con el total mal · afinado: FA-1 bien, FA-2 con el NIF mal
        base = self.run_model([self.model_json(due_date=f(None), gross_total=f("999.00")), right])
        trained = self.run_model([right, self.model_json(due_date=f(None), supplier_tax_id=f("B00000000"))])
        doc = next(d for d in base["per_document"] if d["name"] == "FA-1")
        self.assertEqual(doc["title"], "FA-1.pdf")
        self.assertEqual(doc["diffs"]["gross_total"], {"expected": "121.00", "predicted": "999.00"})

        c = compare(base, trained)
        self.assertEqual(c["documents"], 2)
        self.assertEqual([d["title"] for d in c["fixed"]], ["FA-1.pdf"])
        self.assertEqual([(d["title"], d["fields"]) for d in c["regressed"]], [("FA-2.pdf", ["supplier_tax_id"])])
        self.assertEqual(c["regressed"][0]["diffs"]["supplier_tax_id"]["predicted"], "B00000000")
        gross = next(r for r in c["fields"] if r["field"] == "gross_total")
        self.assertEqual((gross["base"], gross["trained"], gross["delta"]), (50.0, 100.0, 50.0))
        self.assertIn("Total con IVA", render_text(c))
        page = render_html(c)
        self.assertIn("B00000000", page)
        self.assertIn("Nada de este informe se ha guardado", page)

    def test_command_writes_reports(self):
        import json as _json

        right = self.model_json(due_date=self.field(None))
        for name in ("base", "afinado"):
            (self.folder / f"{name}.json").write_text(_json.dumps(self.run_model([right, right])), encoding="utf-8")
        out = io.StringIO()
        call_command("invoice_agent_compare", str(self.folder / "base.json"), str(self.folder / "afinado.json"),
                     html=str(self.folder / "informe.html"), json=str(self.folder / "c.json"), stdout=out)
        self.assertIn("Facturas de prueba: 2", out.getvalue())
        self.assertTrue((self.folder / "informe.html").is_file())
        self.assertEqual(_json.loads((self.folder / "c.json").read_text())["perfect"]["trained"], 2)
