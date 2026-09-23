"""
Step 4b of the invoice OCR pipeline: line-item extraction.

Generalized approach: rather than one fixed regex per supplier's exact
column order, this looks for a leading product code, a free-text
description, and then a trailing run of 3 or 4 whitespace-separated
numeric tokens - and infers what those tokens mean from how many there
are (3 => cantidad/precio/importe; 4 => IVA/cantidad/precio/importe).
This single pattern already correctly handles both real invoices seen
so far (APAGAFOC - no per-line IVA; FRUTAS CASTANO - per-line IVA),
without needing a per-supplier entry for either.

SUPPLIER_OVERRIDES exists as an escape hatch for a supplier whose layout
doesn't fit this shape at all (e.g. numbers not trailing, or a completely
different column order) - empty by default, add a supplier-specific regex
here only when the generic pattern genuinely can't parse a real invoice.

Known limitation: relies on the description never containing a
standalone whitespace-separated numeric token of its own (e.g. "12V" or
"TA03" are fine since the digits are attached to letters; a description
containing a bare number surrounded by spaces would confuse the trailing-
number count). Verify results in the admin for any new supplier.
"""

import re

from invoice_data.models import InvoiceDocument, InvoiceLineItem
from invoice_data.services.extraction import parse_amount

GENERIC_LINE_PATTERN = re.compile(
    r"^(?P<codigo>\d{6,12})\s+(?P<descripcion>.+?)\s+"
    r"(?P<numbers>(?:[\d.,]+\s+){2,3}[\d.,]+)\s*$",
    re.MULTILINE,
)

# Per-supplier override patterns, tried before the generic one. Empty for
# now - add an entry (keyword found in the invoice text -> compiled regex
# with the same named groups as GENERIC_LINE_PATTERN, or "numbers") only
# when a real supplier's layout doesn't fit the generic shape.
SUPPLIER_OVERRIDES: dict[str, re.Pattern] = {}


def _interpret_numbers(raw_numbers: str) -> dict:
    """
    Splits the captured trailing-numbers blob and assigns fields by count:
    3 tokens = cantidad, precio, importe (no per-line IVA on this invoice).
    4 tokens = iva, cantidad, precio, importe.
    Anything else is treated as unparseable for this line.
    """
    tokens = raw_numbers.split()
    if len(tokens) == 4:
        iva, cantidad, precio, importe = tokens
        return {"iva": iva, "cantidad": cantidad, "precio": precio, "importe": importe}
    if len(tokens) == 3:
        cantidad, precio, importe = tokens
        return {"iva": None, "cantidad": cantidad, "precio": precio, "importe": importe}
    return {}


def get_line_pattern_for_text(text: str) -> re.Pattern:
    """Returns a supplier-specific override pattern if one matches the text's keyword, else the generic pattern."""
    for keyword, pattern in SUPPLIER_OVERRIDES.items():
        if keyword in text:
            return pattern
    return GENERIC_LINE_PATTERN


def extract_line_items(document: InvoiceDocument) -> list[InvoiceLineItem]:
    """
    Parses every line item from one document's text and creates
    InvoiceLineItem rows. Returns an empty list if nothing matched at all.
    Safe to re-run - existing line items for this document are replaced first.
    """
    text = document.get_extracted_text()
    pattern = get_line_pattern_for_text(text)

    document.line_items.all().delete()

    created = []
    for match in pattern.finditer(text):
        fields = _interpret_numbers(match.group("numbers"))
        if not fields:
            continue  # unexpected number of trailing tokens, skip rather than guess

        item = InvoiceLineItem.objects.create(
            document=document,
            codigo_articulo=match.group("codigo"),
            descripcion=match.group("descripcion").strip(),
            iva_porcentaje=parse_amount(fields["iva"]),
            cantidad=parse_amount(fields["cantidad"]),
            precio_unitario=parse_amount(fields["precio"]),
            importe=parse_amount(fields["importe"]),
        )
        created.append(item)
    return created