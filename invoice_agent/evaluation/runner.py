"""
Ejecución de la evaluación sobre una carpeta de casos.

Cada caso son dos ficheros con el mismo nombre:
    <caso>.pdf | .png | .jpg   la factura (anonimizada)
    <caso>.json                la verdad revisada a mano:
        {
          "fields": {"supplier_tax_id": "...", "invoice_number": "...", "invoice_date": "AAAA-MM-DD",
                     "gross_total": "121.00", "due_date": null, ...},   # solo los campos verificados
          "lines": 2,                                                      # opcional
          "taxes": [{"kind": "vat", "rate": "21", "base": "100.00", "amount": "21.00"}],  # opcional
          "reconciles": true,             # false si la factura original NO cuadra a propósito
          "duplicate_group": "g1"         # opcional: casos que son la misma factura
        }
    o bien el JSON completo de la factura (esquema factura-ia/1, el de las etiquetas de Google Drive):
    se convierte solo a la verdad de arriba. Así basta con copiar pares PDF + JSON a una carpeta.

No escribe nada en la base de datos: prepara, llama al modelo y valida en memoria.
"""

import json
import time
from decimal import Decimal
from pathlib import Path

from invoice_agent.evaluation.metrics import CaseResult, summarize
from invoice_agent.providers.base import ExtractionProvider, ModelRequest, ModelResponse
from invoice_agent.schemas import HEADER_FIELDS
from invoice_agent.services.extraction import ExtractionFailed, run_extraction
from invoice_agent.services.normalize import from_model_output
from invoice_agent.services.preparation import PreparationError, prepare
from invoice_agent.services.validation import ValidationContext, validate

DOCUMENT_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")


def truth_from_label(label: dict) -> dict:
    """JSON de factura (esquema factura-ia/1) → verdad de evaluación (solo lo que el documento muestra)."""
    from invoice_agent.schemas import HEADER_FIELDS

    fields = {}
    for name in HEADER_FIELDS:
        item = label.get(name) or {}
        fields[name] = item.get("value") if item.get("status") == "present" else None
    return {
        "fields": fields,
        "lines": len(label.get("line_items") or []),
        "taxes": [{k: t.get(k) for k in ("kind", "rate", "base", "amount")} for t in label.get("tax_breakdown") or []],
        "reconciles": True,
    }


def load_cases(folder: Path) -> list[tuple[str, Path, dict]]:
    cases = []
    for truth_path in sorted(folder.glob("*.json")):
        document = next((truth_path.with_suffix(s) for s in DOCUMENT_SUFFIXES if truth_path.with_suffix(s).exists()), None)
        if document is None:
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        if "fields" not in truth and "document_type" in truth:
            truth = truth_from_label(truth)
        truth.setdefault("documento", document.name)
        cases.append((truth_path.stem, document, truth))
    return cases


class OracleProvider(ExtractionProvider):
    """
    Devuelve la verdad como si fuera el modelo. Sirve para comprobar que la
    tubería de evaluación funciona (debe dar 100 % en los campos cubiertos);
    no mide ningún modelo.
    """

    name = "oracle"
    model = "oracle"
    supports_vision = True  # no envía nada; así sirve también en el modo solo imagen

    def __init__(self):
        self.truth: dict = {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        fields = self.truth.get("fields", {})

        def f(name):
            value = fields.get(name)
            return {
                "status": "present" if value not in (None, "") else "absent",
                "value": value,
                "confidence": "high",
                "evidence": {"page": 1 if value else None, "text": str(value) if value else None},
            }

        output = {name: f(name) for name in HEADER_FIELDS}
        output.update(
            document_type="invoice",
            line_items=[
                {"description": f"línea {i + 1}", "quantity": None, "unit_price": None, "discount": None, "tax_rate": None,
                 "line_subtotal": None, "line_total": None, "unreadable_fields": [], "page": 1, "source_text": None}
                for i in range(self.truth.get("lines") or 0)
            ],
            tax_breakdown=[{**t, "page": 1, "source_text": None} for t in self.truth.get("taxes", [])],
            notes=[],
        )
        return ModelResponse(content=json.dumps(output), model="oracle")


def _tax_tuple(kind, rate, base, amount) -> tuple:
    def q(value):
        return str(Decimal(str(value)).quantize(Decimal("0.01"))) if value not in (None, "") else None

    return (kind, q(rate), q(base), q(amount))


def evaluate_folder(
    folder: Path, provider: ExtractionProvider, ocr_engine=None, own_nif: str = "", vision_mode: str | None = None
) -> dict:
    results = []
    for name, document, truth in load_cases(Path(folder)):
        if isinstance(provider, OracleProvider):
            provider.truth = truth
        started = time.monotonic()
        tipo = "pdf" if document.suffix.lower() == ".pdf" else "image"
        try:
            prepared = prepare(document, tipo, ocr_engine, provider.supports_vision, vision_mode)
            empresa = truth.get("empresa") or {}  # la exportación del dataset guarda la empresa receptora
            extraction = run_extraction(
                prepared,
                empresa_nombre=empresa.get("nombre", ""),
                empresa_nif=empresa.get("nif", own_nif),
                provider=provider,
            )
        except (PreparationError, ExtractionFailed) as error:
            results.append(
                CaseResult(name, {}, truth.get("fields", {}), [], 0, truth.get("lines"), [], None,
                           latency_ms=int((time.monotonic() - started) * 1000), error=f"{error.code}: {error.message}",
                           title=truth.get("documento"))
            )
            continue
        data = from_model_output(extraction.output)
        issues = [
            i.to_dict()
            for i in validate(data, ValidationContext(empresa_nif=(truth.get("empresa") or {}).get("nif", own_nif)))
        ]
        stats = extraction.stats
        results.append(
            CaseResult(
                name=name,
                predicted={n: fv.value if fv.usable else None for n, fv in data.fields.items()},
                expected=truth.get("fields", {}),
                issues=issues,
                lines_predicted=len(data.lines),
                lines_expected=truth.get("lines"),
                taxes_predicted=[_tax_tuple(t.kind, t.rate, t.base, t.amount) for t in data.taxes],
                taxes_expected=[_tax_tuple(t["kind"], t.get("rate"), t.get("base"), t.get("amount")) for t in truth["taxes"]]
                if "taxes" in truth
                else None,
                expect_reconciles=truth.get("reconciles", True),
                duplicate_group=truth.get("duplicate_group"),
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=stats.input_tokens,
                output_tokens=stats.output_tokens,
                cost=stats.cost(),
                title=truth.get("documento"),
            )
        )
    report = summarize(results)
    return {
        "provider": provider.name,
        "model": provider.model,
        "documents": report.documents,
        "failures": report.failures,
        "fields": report.fields,
        "documents_metrics": report.documents_metrics,
        "duplicates": report.duplicates,
        "cost_latency": report.cost_latency,
        "per_document": report.per_document,
    }
