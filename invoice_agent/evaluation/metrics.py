"""
Métricas de evaluación contra una verdad de referencia revisada a mano.

No se usa un modelo como juez: todas las comparaciones son deterministas
(importes como Decimal, fechas, NIF y números de factura normalizados).

Por campo:
    exact_match  aciertos (incluido "ambos vacíos") / documentos
    precision    valores correctos / valores que dio el sistema
    recall       valores correctos / valores que hay en la verdad
Por documento:
    reconciliation_accuracy  la validación dice "cuadra" exactamente cuando la verdad dice que cuadra
    review_rate              documentos que acaban necesitando revisión humana
    lines_count_accuracy, taxes_exact_match
Duplicados: precisión y exhaustividad sobre PAREJAS de documentos con la misma clave de negocio.
Coste y latencia: media, p95 y totales.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import combinations

from invoice_agent.schemas import AMOUNT_FIELDS, DATE_FIELDS, HEADER_FIELDS
from invoice_agent.services.normalize import (
    normalize_invoice_number,
    normalize_tax_id,
    parse_date,
)

RECONCILIATION_CODES = {"TOTAL_MISMATCH", "TAX_ARITHMETIC", "TAX_TOTAL_MISMATCH", "WITHHOLDING_MISMATCH"}


def comparable(name: str, value):
    if value in (None, ""):
        return None
    if name in AMOUNT_FIELDS:
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None
    if name in DATE_FIELDS:
        return value if isinstance(value, date) else parse_date(value)
    if name in ("supplier_tax_id", "customer_tax_id"):
        return normalize_tax_id(value) or None
    if name == "invoice_number":
        return normalize_invoice_number(value) or None
    return " ".join(str(value).upper().split())


@dataclass
class CaseResult:
    name: str
    predicted: dict  # campo -> valor
    expected: dict
    issues: list[dict]
    lines_predicted: int
    lines_expected: int | None
    taxes_predicted: list[tuple]
    taxes_expected: list[tuple] | None
    expect_reconciles: bool = True
    duplicate_group: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)
    error: str | None = None
    title: str | None = None  # nombre legible (fichero original), si la verdad lo trae


def _text(value) -> str | None:
    return None if value in (None, "") else str(value)


@dataclass
class Report:
    documents: int = 0
    failures: int = 0
    fields: dict = field(default_factory=dict)
    documents_metrics: dict = field(default_factory=dict)
    duplicates: dict = field(default_factory=dict)
    cost_latency: dict = field(default_factory=dict)
    per_document: list = field(default_factory=list)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def summarize(results: list[CaseResult]) -> Report:
    report = Report(documents=len(results), failures=sum(1 for r in results if r.error))
    ok = [r for r in results if not r.error]

    for name in HEADER_FIELDS:
        tp = fp = fn = exact = considered = 0
        for r in ok:
            if name not in r.expected:
                continue  # la verdad no cubre este campo
            considered += 1
            truth = comparable(name, r.expected.get(name))
            pred = comparable(name, r.predicted.get(name))
            if pred == truth:
                exact += 1
            if pred is not None and pred == truth:
                tp += 1
            elif pred is not None:
                fp += 1
                if truth is not None:
                    fn += 1
            elif truth is not None:
                fn += 1
        if considered:
            report.fields[name] = {
                "exact_match": _ratio(exact, considered),
                "precision": _ratio(tp, tp + fp),
                "recall": _ratio(tp, tp + fn),
                "n": considered,
            }

    reconciled_right = review = lines_ok = lines_n = taxes_ok = taxes_n = 0
    for r in ok:
        reconciles = not any(i["code"] in RECONCILIATION_CODES for i in r.issues)
        reconciled_right += reconciles == r.expect_reconciles
        review += any(i["blocking"] for i in r.issues)
        if r.lines_expected is not None:
            lines_n += 1
            lines_ok += r.lines_predicted == r.lines_expected
        if r.taxes_expected is not None:
            taxes_n += 1
            taxes_ok += sorted(r.taxes_predicted) == sorted(r.taxes_expected)
    report.documents_metrics = {
        "reconciliation_accuracy": _ratio(reconciled_right, len(ok)),
        "review_rate": _ratio(review, len(ok)),
        "lines_count_accuracy": _ratio(lines_ok, lines_n),
        "taxes_exact_match": _ratio(taxes_ok, taxes_n),
    }

    truth_pairs, predicted_pairs = set(), set()
    for a, b in combinations(ok, 2):
        if a.duplicate_group and a.duplicate_group == b.duplicate_group:
            truth_pairs.add((a.name, b.name))
        key_a = (comparable("supplier_tax_id", a.predicted.get("supplier_tax_id")), comparable("invoice_number", a.predicted.get("invoice_number")))
        key_b = (comparable("supplier_tax_id", b.predicted.get("supplier_tax_id")), comparable("invoice_number", b.predicted.get("invoice_number")))
        if None not in key_a and key_a == key_b:
            predicted_pairs.add((a.name, b.name))
    hits = len(truth_pairs & predicted_pairs)
    report.duplicates = {
        "pairs_expected": len(truth_pairs),
        "pairs_detected": len(predicted_pairs),
        "precision": _ratio(hits, len(predicted_pairs)),
        "recall": _ratio(hits, len(truth_pairs)),
    }

    latencies = [r.latency_ms for r in results]
    total_cost = sum((r.cost for r in results), Decimal(0))
    report.cost_latency = {
        "latency_ms_mean": int(sum(latencies) / len(latencies)) if latencies else 0,
        "latency_ms_p95": _p95(latencies),
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "cost_total": str(total_cost),
        "cost_per_document": str((total_cost / len(results)).quantize(Decimal("0.000001"))) if results else "0",
    }
    report.per_document = []
    for r in results:
        wrong = sorted(
            n for n in HEADER_FIELDS if n in r.expected and comparable(n, r.expected.get(n)) != comparable(n, r.predicted.get(n))
        )
        report.per_document.append({
            "name": r.name,
            "title": r.title or r.name,
            "error": r.error,
            "blocking": sorted({i["code"] for i in r.issues if i["blocking"]}),
            "wrong_fields": wrong,
            # valor de la verdad (p. ej. el JSON del modelo grande) frente al del modelo evaluado
            "diffs": {} if r.error else {
                n: {"expected": _text(r.expected.get(n)), "predicted": _text(r.predicted.get(n))} for n in wrong
            },
            "lines": {"expected": r.lines_expected, "predicted": None if r.error else r.lines_predicted},
        })
    return report
