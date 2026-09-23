"""
Step 4 of the invoice OCR pipeline: field extraction.

Two-tier approach:
1. invoice2data template match (services/invoice2data_extraction.py) - tried
   first. Precise when it matches, but only covers suppliers we've written
   a YAML template for.
2. Generic regex + pdfplumber fallback - runs whenever no template matched,
   so every document still gets a best-effort result instead of nothing.
   Always marked NEEDS_REVIEW and extraction_method="regex_fallback", since
   it's a supplier we haven't onboarded a template for yet.

A third tier (AI fallback via invoice2data's ai_fallback option) is planned
but not wired in yet - see the note in invoice2data_extraction.py.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from invoice_data.models import ExtractedInvoiceData, ExtractionStatus, InvoiceDocument, SourceType
from invoice_data.services.invoice2data_extraction import extract_with_invoice2data

NUMERO_FACTURA_PATTERNS = [
    re.compile(r"N[UÚ]MERO[:\s]*([A-Z0-9][A-Z0-9/\-]{2,20})", re.IGNORECASE),
    re.compile(r"FACTURA\s*N[ºO°][:\s]*([A-Z0-9][A-Z0-9/\-]{2,20})", re.IGNORECASE),
]
FECHA_RE = re.compile(r"FECHA[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", re.IGNORECASE)
CIF_RE = re.compile(r"\b([A-Z]\d{7}[A-Z0-9])\b")


def parse_amount(raw) -> Decimal | None:
    """
    Parses a monetary amount that might use Spanish formatting
    (1.234,56) or plain dot-decimal formatting (852.51). Accepts either
    a string or a number (invoice2data may already return a float).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))
    cleaned = str(raw).strip()
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(raw: str | None) -> date | None:
    """Parses a dd/mm/yyyy-style date string (with / or - separators, 2 or 4 digit year)."""
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _find_numero_factura(text: str) -> str | None:
    """Tries each numero_factura pattern in order, returns the first match found."""
    for pattern in NUMERO_FACTURA_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def _extract_fields_from_text(text: str) -> dict:
    """Regex-only header extraction (invoice number, date, CIF), used by the fallback tier."""
    fecha_match = FECHA_RE.search(text)
    cif_match = CIF_RE.search(text)

    return {
        "numero_factura": _find_numero_factura(text),
        "fecha_factura": parse_date(fecha_match.group(1) if fecha_match else None),
        "proveedor_cif": cif_match.group(1) if cif_match else None,
        "base_imponible": None,
        "iva_porcentaje": None,
        "total": None,
    }


def _fallback_extraction(document: InvoiceDocument) -> dict:
    """
    Tier 2: generic regex over the document's extracted text, plus
    pdfplumber table extraction for the totals block on digital PDFs.
    This is the safety net for any supplier without a template yet.
    """
    text = document.get_extracted_text()
    fields = _extract_fields_from_text(text)

    if document.source_type == SourceType.DIGITAL_PDF:
        from invoice_data.services.table_extraction import extract_totals_from_pdf

        try:
            table_totals = extract_totals_from_pdf(Path(document.file.path))
            for key, value in table_totals.items():
                if value is not None:
                    fields[key] = value
        except Exception:
            pass  # best-effort; header fields above still stand

    return fields


def _map_invoice2data_result(result: dict) -> dict:
    """Maps invoice2data's field names onto our ExtractedInvoiceData field names."""
    date_value = result.get("date")
    return {
        "numero_factura": result.get("invoice_number"),
        "fecha_factura": date_value.date() if hasattr(date_value, "date") else None,
        "proveedor_cif": None,  # not modeled in templates yet - add per-template if needed
        "base_imponible": parse_amount(result.get("amount_untaxed")),
        "iva_porcentaje": parse_amount(result.get("vat_rate")),
        "total": parse_amount(result.get("amount")),
    }


def extract_document(document: InvoiceDocument) -> ExtractedInvoiceData:
    """
    Runs field extraction for one InvoiceDocument, trying invoice2data's
    template match first and falling back to generic regex/table extraction
    if no template matched. Safe to re-run - updates the existing
    ExtractedInvoiceData row instead of creating duplicates.
    """
    i2d_result = extract_with_invoice2data(document)

    if i2d_result:
        fields = _map_invoice2data_result(i2d_result)
        extraction_method = "invoice2data_template"
    else:
        fields = _fallback_extraction(document)
        extraction_method = "regex_fallback"

    is_confident = bool(fields["numero_factura"]) and fields["total"] is not None

    extracted, _ = ExtractedInvoiceData.objects.update_or_create(
        document=document,
        defaults={
            **fields,
            "extraction_method": extraction_method,
            "status": ExtractionStatus.EXTRACTED if is_confident else ExtractionStatus.NEEDS_REVIEW,
        },
    )
    return extracted