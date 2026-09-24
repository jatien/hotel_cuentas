"""
Table-aware extraction for the invoice totals block (BASE IMP. / %IVA / TOTAL).

Only applies to digital PDFs - pdfplumber reads the PDF's real text
positions to reconstruct table structure, which only exists for an
actual digital PDF, not a scanned image. Scanned PDFs/images still rely
on OCR text + regex (services/extraction.py), which currently can't
recover table structure - that's a known limitation, not solved here.

Strategy note: many suppliers (including this one) draw table separators
using plain text dashes rather than real PDF vector lines. pdfplumber's
default "lines" table-detection strategy looks for drawn lines and finds
nothing in that case, so we use the "text" strategy instead, which
clusters cells by their aligned x/y text positions - much more reliable
for this kind of invoice layout.
"""

from pathlib import Path

import pdfplumber

from invoice_data.services.extraction import parse_amount

# Keyword fragments used to recognize the totals header row, matched against
# each cell with spaces stripped - handles suppliers who print headers with
# letter-spacing (e.g. "T O T A L" instead of "TOTAL").
BASE_IMPONIBLE_KEYWORDS = ("BASEIMP",)
IVA_KEYWORDS = ("IVA",)

TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
}


def _compact(cell) -> str:
    """Uppercases and strips all whitespace from a cell, so 'T O T A L' and 'TOTAL' compare equal."""
    return (cell or "").strip().upper().replace(" ", "")


def find_totals_row(tables: list[list[list[str]]]) -> dict:
    """
    Scans every table pdfplumber found on a page for the header row that
    contains BASE IMP., %IVA and TOTAL together, then reads the values
    from the row directly below it, mapped by column position. Returns
    a dict with whatever fields were confidently found; the rest stay None.
    """
    result = {"base_imponible": None, "iva_porcentaje": None, "total": None}

    for table in tables:
        for row_index, row in enumerate(table[:-1]):
            compact_cells = [_compact(cell) for cell in row]
            has_base = any(any(k in cell for k in BASE_IMPONIBLE_KEYWORDS) for cell in compact_cells)
            has_iva = any(any(k in cell for k in IVA_KEYWORDS) for cell in compact_cells)
            has_total = any(cell == "TOTAL" for cell in compact_cells)

            if not (has_base and has_iva and has_total):
                continue  # not the totals header row, keep looking

            value_row = table[row_index + 1]
            for col_index, header_cell in enumerate(compact_cells):
                if col_index >= len(value_row):
                    continue
                value = parse_amount(value_row[col_index])
                if value is None:
                    continue
                if any(k in header_cell for k in BASE_IMPONIBLE_KEYWORDS) and result["base_imponible"] is None:
                    result["base_imponible"] = value
                elif any(k in header_cell for k in IVA_KEYWORDS) and result["iva_porcentaje"] is None:
                    result["iva_porcentaje"] = value
                elif header_cell == "TOTAL" and result["total"] is None:
                    result["total"] = value
            return result  # found and mapped the row, no need to check further tables

    return result


def extract_totals_from_pdf(path: Path) -> dict:
    """
    Opens a digital PDF with pdfplumber and attempts to locate and parse
    its totals table. Returns None for any field it couldn't confidently
    find - extraction.py falls back to its regex result for those.
    """
    result = {"base_imponible": None, "iva_porcentaje": None, "total": None}

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables(table_settings=TABLE_SETTINGS)
            if not tables:
                continue
            page_result = find_totals_row(tables)
            for key, value in page_result.items():
                if value is not None:
                    result[key] = value

    return result