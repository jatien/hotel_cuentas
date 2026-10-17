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
