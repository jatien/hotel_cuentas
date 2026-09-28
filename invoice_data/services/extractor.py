r"""
Invoice extraction engine (template-free) for the invoice_data app.

Reads a PDF's text layer with pdfium, recognises product lines by their
ARITHMETIC (units x price = total), finds IVA rates and totals by
searching for numbers that satisfy the invoice's own arithmetic, and
flags anything that does not reconcile instead of trusting it. Images
embedded in otherwise digital PDFs (header blocks, descriptions) are
read with OCR only when the first pass is incomplete.

Nothing here is specific to any supplier. The only configuration is the
hotel's own identity (so the customer is never mistaken for the
supplier), read from Django settings at call time:

    INVOICE_OWN_NIF    = "B12345678"          # the hotel's tax id
    INVOICE_OWN_NAMES  = ("MI HOTEL",)        # fragments of its legal name
    TESSERACT_CMD      = r"C:\Program Files\Tesseract-OCR\tesseract.exe"   # optional
    INVOICE_OCR_LANG   = "spa"                # optional
"""

import os
import re
import sys
from collections import Counter
from datetime import datetime
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations_with_replacement, product
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pypdfium2 as pdfium
from django.conf import settings
import pypdfium2.raw as pdfium_raw

# The hotel's own tax id: any other CIF/NIF found in the text is the supplier's.
# Move this to Django settings when integrating.
VERSION = "2026-09-28-g"  # stored with every extraction so results can be traced to the code that made them
KERN_GAP_RATIO = 0.45  # a synthetic space between glyphs closer than this x glyph height is a kerning artefact


def own_nif() -> str:
    """The hotel's own tax id (settings.INVOICE_OWN_NIF), normalised; '' when not configured."""
    return re.sub(r"[\s-]", "", str(getattr(settings, "INVOICE_OWN_NIF", "") or "")).upper()


def own_names() -> tuple[str, ...]:
    """Fragments of the hotel's own legal name (settings.INVOICE_OWN_NAMES); () when not configured."""
    return tuple(getattr(settings, "INVOICE_OWN_NAMES", ()) or ())


def ocr_language() -> str:
    """Tesseract language for embedded-image OCR (settings.INVOICE_OCR_LANG, default Spanish)."""
    return getattr(settings, "INVOICE_OCR_LANG", "spa") or "spa"


# No rate list gates extraction: rates are read from the invoice (IVA column)
# or inferred from its own arithmetic. MAX_RATE only rejects absurd values;
# USUAL_RATES is a soft hint that adds a "check it" note, never a rejection.
MAX_RATE = Decimal("30")
RATE_STEP = Decimal("0.5")
MAX_PARTS = 3  # how many different rates one invoice may mix when inferring
USUAL_RATES = {Decimal(r) for r in ("0", "2", "4", "5", "7", "7.5", "10", "21")}
CENT = Decimal("0.01")
LINE_TOLERANCE = Decimal("0.011")  # qty x price may differ from amount by rounding

NUMERIC_TOKEN = re.compile(r"^\d[\d.,]*$")
MONEY_TOKEN = re.compile(r"(?<![\d.,])\d+[.,]\d{2}(?!\d)")
DATE = r"\d{1,2}/\d{1,2}/\d{2,4}"
CIF_RE = re.compile(r"\b([A-HJ-NP-SUVW])[- ]?(\d{7}[0-9A-J])\b")
# Personal tax ids (freelancers): 8 digits + letter (NIF) or X/Y/Z + 7 digits + letter (NIE).
PERSONAL_ID_RE = re.compile(r"\b(\d{8}[A-Z]|[XYZ]\d{7}[A-Z])\b")
# Words that may precede a company name on the same line but are not part of it.
NAME_STOPWORDS = {
    "FACTURA", "ALBARAN", "PRESUPUESTO", "PEDIDO", "CLIENTE", "PROVEEDOR", "DATOS",
    "EMISOR", "RECEPTOR", "NIF", "CIF", "RAZON", "SOCIAL", "DUPLICADO", "COPIA", "ORIGINAL",
}
ALBARAN_RE = re.compile(
    rf"ALBAR[ÁA]N\s*:?\s*'?([A-Z0-9][A-Z0-9/\-. ]*?)'?\s*"
    rf"(?:DE\s+FECHA|FECHA\s+ALBAR[ÁA]N\s*:?|FECHA\s*:?)?\s+({DATE})",
    re.IGNORECASE,
)
TOTAL_ALBARAN_RE = re.compile(r"TOTAL\s+ALBAR[ÁA]N\s*:\s*([\d.,]+)", re.IGNORECASE)

# Header labels, matched against a whole cell (not free text), so only a
# cell that IS the label counts. 'Fecha albaran:' / 'Vencimientos' never match.
NUMBER_LABEL_RE = re.compile(
    r"^(?:(?:N[ÚU]M(?:ERO)?|N[ºO°])\.?\s*(?:DE\s+)?)?FACTURA(?:\s*(?:N[ÚU]M(?:ERO)?|N[ºO°])\.?)?\s*:?$"
    r"|^N[ÚU]MERO\s*:?$|^N[ºO°]\.?\s*:?$|^INVOICE(?:\s*N[OU]\.?)?\s*:?$",
    re.IGNORECASE,
)
DATE_LABEL_RE = re.compile(
    r"^FECHA(?:\s+(?:DE\s+)?(?:FACTURA|EMISI[ÓO]N|EXPEDICI[ÓO]N))?\s*:?$|^DATE\s*:?$", re.IGNORECASE
)
MAX_DROP = 32  # points: how far below its label a value may sit
ROW_TOLERANCE = 4  # points: vertical slack to count as the same row
MAX_GAP_RIGHT = 120  # points: how far to the right of its label a value may sit

# OCR of images embedded in otherwise digital PDFs (header blocks, descriptions, footers).
OCR_SCALE = 300 / 72  # render at ~300 DPI
OCR_MIN_WIDTH = 60  # points: smaller images are icons/QR codes, not text blocks
OCR_MIN_HEIGHT = 12
OCR_MIN_CONFIDENCE = 55  # mean word confidence below this is treated as noise (logos, decoration)

# Company name = 1-6 capitalised words (optionally joined by 'de', 'del', 'y'...)
# on ONE line, followed by a legal-form suffix. Digits and dots break the chain,
# so 'H.AB7139 ACME PINTURAS S.L.' yields 'ACME PINTURAS'.
NAME_WORD = r"[A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚÑÜáéíóúñü&'’\-]*\d{0,2}(?![A-Za-z0-9ÁÉÍÓÚÑÜáéíóúñü&'’\-])"
NAME_CONNECTOR = r"(?:DE|DEL|LA|LAS|LOS|EL|Y|E|I|de|del|la|las|los|el|y|e|i)"
LEGAL_SUFFIX = (
    r"(?:S\.\s?L\.\s?U\.|S\.\s?L\.|SLU|SL|S\.\s?A\.\s?U\.|S\.\s?A\.|SAU|SA"
    r"|S\.\s?COOP\.?|S\.\s?C\.|C\.\s?B\.|CB|SC)"
)
COMPANY_RE = re.compile(
    rf"(?P<name>{NAME_WORD}(?:[ \t]+(?:{NAME_CONNECTOR}[ \t]+)?{NAME_WORD}){{0,5}}?)"
    rf",?[ \t]+(?P<suffix>{LEGAL_SUFFIX})(?![\wÁÉÍÓÚÑ])"
)


def parse_number(token: str) -> Decimal | None:
    """
    Parses '1.234,56', '1,234.56', '852.51', '2,1000' into a Decimal.
    If both separators appear, the last one is the decimal point; a single
    separator is treated as decimal (known limitation: '1.234' alone is
    read as 1.234, not one thousand two hundred thirty-four).
    """
    token = token.strip()
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    else:
        token = token.replace(",", ".")
    try:
        return Decimal(token)
    except Exception:
        return None


def money(value: Decimal) -> Decimal:
    """Rounds to cents the way invoices do (half up)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def read_pdf_text(path: Path) -> str:
    """Returns the whole text layer of a PDF (all pages) using pdfium."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in pdf).replace("\r", "")
    finally:
        pdf.close()


@dataclass
class Cell:
    """One text run from the PDF with its position (points; y grows upwards)."""

    page: int
    left: float
    bottom: float
    right: float
    top: float
    text: str


@dataclass
class Region:
    """Text recovered by OCR from one image embedded in the PDF (page coordinates, y grows upwards)."""

    page: int
    left: float
    bottom: float
    right: float
    top: float
    text: str


def read_pdf_cells(path: Path) -> list[Cell]:
    """
    Returns every text run of every page with its bounding box, using
    pdfium's own run detection. In bordered header tables each table cell
    normally comes out as its own run, which is what lets us pair a label
    with the value printed under (or beside) it, whatever order the text
    layer stores them in.
    """
    cells = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for page_no, page in enumerate(pdf):
            textpage = page.get_textpage()
            for i in range(textpage.count_rects()):
                left, bottom, right, top = textpage.get_rect(i)
                text = textpage.get_text_bounded(left, bottom, right, top).strip()
                if text:
                    cells.append(Cell(page_no, left, bottom, right, top, text))
    finally:
        pdf.close()
    return cells


def is_kerning_gap(gap: float, glyph_height: float) -> bool:
    """
    True if a zero-width synthetic space sits between glyphs that almost
    touch (a font-kerning artefact, e.g. 'PIN TURAS'), not a real word gap.
    """
    height = max(glyph_height, 1e-6)
    return -0.2 * height <= gap < KERN_GAP_RATIO * height


def read_pdf_text_repaired(path: Path) -> str:
    """
    Like read_pdf_text, but drops the synthetic spaces pdfium inserts
    inside words because of kerning ('PIN TURAS' -> 'PINTURAS'). Real
    spaces have a width and are kept. Used only for company-name
    extraction, where a split word would corrupt the result; line and
    totals parsing keeps using the plain text.
    """
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = []
        for page in pdf:
            textpage = page.get_textpage()
            full = textpage.get_text_range()
            kept = []
            for k, ch in enumerate(full):
                if ch == " " and 0 < k < len(full) - 1:
                    left, _, right, _ = textpage.get_charbox(k)
                    if right - left < 0.01:  # synthetic: no width
                        _, p_bottom, p_right, p_top = textpage.get_charbox(k - 1)
                        n_left, n_bottom, _, _ = textpage.get_charbox(k + 1)
                        height = p_top - p_bottom
                        same_line = abs(p_bottom - n_bottom) < 0.5 * max(height, 1e-6)
                        if same_line and is_kerning_gap(n_left - p_right, height):
                            continue
                kept.append(ch)
            pages.append("".join(kept))
        return "\n".join(pages).replace("\r", "")
    finally:
        pdf.close()


@dataclass
class Line:
    """One parsed product line."""

    codigo: str
    descripcion: str
    iva: Decimal | None
    cantidad: Decimal
    precio: Decimal
    importe: Decimal
    albaran: str = ""
    albaran_fecha: str = ""


@dataclass
class Result:
    """Everything extracted from one invoice, plus how well it reconciled."""

    numero: str | None = None
    fecha: str | None = None
    proveedor_cif: str | None = None
    proveedor_nombre: str | None = None
    lines: list[Line] = field(default_factory=list)
    bases: dict = field(default_factory=dict)  # rate -> base
    cuotas: dict = field(default_factory=dict)  # rate -> cuota
    total: Decimal | None = None
    evidence: dict = field(default_factory=dict)
    reconciled: bool = False
    totals_only: bool = False  # totals deduced from the tax block alone (no product lines)
    ocr_regions_used: int = 0  # embedded images read by OCR to complete this result
    albaran_totals: dict = field(default_factory=dict)  # ref -> (sum of lines, printed total)
    notes: list[str] = field(default_factory=list)


def has_iva_column(lines: list[str]) -> bool:
    """
    True if some line looks like a line-item table header that includes an
    IVA column (it mentions %IVA together with quantity/price words).
    Needed to tell a real IVA column apart from a number that is merely
    part of a description ('CAJA REGISTRO MODELO 10').
    """
    for line in lines:
        upper = line.upper()
        if re.search(r"%\s*IVA", upper) and re.search(r"CANTIDAD|PRECIO", upper):
            return True
    return False


def detect_code(token: str) -> bool:
    """A leading article code: digits only (4+), or an alphanumeric code (4+) containing a digit and a letter."""
    return bool(re.fullmatch(r"\d{4,}", token)) or bool(
        re.fullmatch(r"(?=.*\d)(?=.*[A-Z])[A-Z0-9\-.]{4,}", token)
    )


def match_amounts(numbers: list[Decimal]) -> tuple[int, Decimal, Decimal, Decimal] | None:
    """
    Tries the trailing numbers as: (5) units, price, dto %, net unit
    price, total; then (3) units, price, total; then (4) units, price,
    dto %, total; then (4) units, price, net price (= price), total.
    Order matters: the 3-number layout has no free
    parameter, whereas the 4-number one can match by coincidence when
    the number before the units is a small IVA rate ('4 0,18 3,8480 0,69'
    also satisfies units x price x (1 - dto%) = total). The first
    layout whose arithmetic holds wins; returns (how many numbers used,
    units, price, total) or None.
    """
    if len(numbers) >= 5:
        q, p, d, n, i = numbers[-5:]
        if abs(q * p * (1 - d / 100) - i) <= LINE_TOLERANCE and abs(p * (1 - d / 100) - n) <= LINE_TOLERANCE:
            return 5, q, p, i
    q, p, i = numbers[-3:]
    if abs(q * p - i) <= LINE_TOLERANCE:
        return 3, q, p, i
    if len(numbers) >= 4:
        q, p, d, i = numbers[-4:]
        if abs(q * p * (1 - d / 100) - i) <= LINE_TOLERANCE:
            return 4, q, p, i
        # empty Dto. column: units, price, net price (= price), total
        q, p, n, i = numbers[-4:]
        if abs(n - p) <= LINE_TOLERANCE and abs(q * p - i) <= LINE_TOLERANCE:
            return 4, q, p, i
    return None


def parse_line(text: str, iva_column: bool) -> Line | None:
    """
    Tries to read one text line as a product line. Accepts it only if the
    trailing numbers satisfy the units x price = total arithmetic (with
    optional discount and net-price columns); anything else (notes,
    headers, addresses, totals rows) is rejected. A row made only of an
    article code and numbers is accepted: some invoices draw the
    description as an image, so it is filled in later from OCR.
    """
    tokens = text.split()
    has_word = any(re.search(r"[A-Za-zÁÉÍÓÚÑ]{2,}", t) for t in tokens)
    if len(tokens) < 4 or not (has_word or detect_code(tokens[0])):
        return None

    run_start = len(tokens)
    while run_start > 0 and NUMERIC_TOKEN.match(tokens[run_start - 1]):
        run_start -= 1
    numbers = [parse_number(t) for t in tokens[run_start:]]
    if len(numbers) < 3 or any(n is None for n in numbers):
        return None

    matched = match_amounts(numbers)
    if matched is None:
        return None
    used, cantidad, precio, importe = matched

    leading = tokens[: len(tokens) - used]
    iva = None
    if iva_column and len(leading) >= 2 and NUMERIC_TOKEN.match(leading[-1]):
        candidate = parse_number(leading[-1])
        if candidate is not None and 0 <= candidate <= MAX_RATE:
            iva = candidate
            leading = leading[:-1]

    codigo = ""
    if leading and detect_code(leading[0]):
        codigo, leading = leading[0], leading[1:]
    if not leading and not codigo:
        return None
    return Line(codigo, " ".join(leading), iva, cantidad, precio, importe)


def parse_lines(text: str) -> list[Line]:
    """Parses every product line in the text and tags each with the albaran it follows."""
    raw_lines = [l.strip() for l in text.split("\n")]
    iva_column = has_iva_column(raw_lines)
    albaran, albaran_fecha = "", ""
    parsed = []
    for raw in raw_lines:
        match = ALBARAN_RE.search(raw)
        if match and not parse_line(raw, iva_column):
            albaran, albaran_fecha = match.group(1).strip(), match.group(2)
            continue
        line = parse_line(raw, iva_column)
        if line:
            line.albaran, line.albaran_fecha = albaran, albaran_fecha
            parsed.append(line)
    return parsed


def money_tokens_in(text: str) -> set[Decimal]:
    """Every amount-looking number (two decimals) in the document: the evidence pool for totals."""
    return {parse_number(m.group()) for m in MONEY_TOKEN.finditer(text)}


def all_numbers_in(text: str) -> set[Decimal]:
    """Every number of any shape in the text; used to see which candidate rates are actually printed."""
    found = re.findall(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])", text)
    return {n for n in (parse_number(t) for t in found) if n is not None}


def find_in_pool(value: Decimal, pool: set[Decimal], tol: Decimal = Decimal("0.01")) -> Decimal | None:
    """The pool value closest to `value` within `tol` (default 1 cent, for per-line rounding), else None."""
    matches = [p for p in pool if abs(p - value) <= tol]
    return min(matches, key=lambda p: abs(p - value)) if matches else None


@lru_cache(maxsize=None)
def snap_rate(base: Decimal, cuota: Decimal) -> Decimal | None:
    """
    The rate (a multiple of 0.5, up to MAX_RATE) that turns `base` into
    exactly `cuota` to the cent, or None. Reading the rate from the
    numbers means 7.5%, 2%, an old 16%... work without being listed.
    """
    best = None
    rate = Decimal(0)
    while rate <= MAX_RATE:
        if money(base * rate / 100) == cuota:
            error = abs(base * rate / 100 - cuota)
            if best is None or error < best[0]:
                best = (error, rate)
        rate += RATE_STEP
    return best[1] if best else None


def totals_from_line_rates(lines: list[Line], result: Result) -> None:
    """Case A: every line printed its own IVA rate, so group by whatever rates appear and compute each cuota."""
    for rate in sorted({l.iva for l in lines}, reverse=True):
        base = money(sum((l.importe for l in lines if l.iva == rate), Decimal(0)))
        result.bases[rate] = base
        result.cuotas[rate] = money(base * rate / 100)
    result.total = money(sum(result.bases.values()) + sum(result.cuotas.values()))


def infer_rates_from_totals(
    subtotal: Decimal, pool: set[Decimal], numbers: set[Decimal], result: Result
) -> None:
    """
    Case B: lines show no IVA rate, so infer it. Look for a split of the
    line subtotal into 1..MAX_PARTS bases, each with a cuota printed in
    the document that equals base x (some rate) to the cent, such that
    subtotal + cuotas is a printed total. Fewest rates wins; if several
    fit, prefer the one whose rates are actually printed as numbers.
    """
    candidates = sorted({v for v in pool if 0 < v <= subtotal}, reverse=True)
    solutions = []
    for parts in range(1, MAX_PARTS + 1):
        if parts == 1:
            base_sets = [(subtotal,)]
        else:
            base_sets = (
                combo
                for combo in combinations_with_replacement(candidates, parts)
                if sum(combo) == subtotal
            )
        for bases in base_sets:
            options = [
                [(c, snap_rate(b, c)) for c in pool if c > 0 and snap_rate(b, c) is not None]
                for b in bases
            ]
            for choice in product(*options):
                total = subtotal + sum(c for c, _ in choice)
                if total in pool:
                    solutions.append((bases, choice, total))
        if solutions:
            break

    if not solutions:
        result.notes.append("No rate split reproduces cuota(s) and a total that are printed in the text")
        return

    def printed_rates(solution) -> int:
        return sum(1 for _, rate in solution[1] if rate in numbers)

    solutions.sort(key=printed_rates, reverse=True)
    if len(solutions) > 1 and printed_rates(solutions[0]) == printed_rates(solutions[1]):
        result.notes.append("Several rate splits fit equally well; review the tax block")

    bases, choice, total = solutions[0]
    for base, (cuota, rate) in zip(bases, choice):
        result.bases[rate] = base
        result.cuotas[rate] = cuota
    result.total = total


def solve_totals(lines: list[Line], pool: set[Decimal], numbers: set[Decimal], result: Result) -> None:
    """
    Label-free totals. If every line carries an IVA rate, group by those
    rates (Case A); otherwise infer the rate(s) from the arithmetic
    (Case B). Either way, the computed base, cuota and total must be
    found in the document (within 1 cent) for the invoice to count as
    reconciled. No list of allowed rates is consulted; unusual rates
    only add a note.
    """
    subtotal = money(sum((l.importe for l in lines), Decimal(0)))

    if lines and all(l.iva is not None for l in lines):
        totals_from_line_rates(lines, result)
    else:
        infer_rates_from_totals(subtotal, pool, numbers, result)
        if result.total is None:
            return

    checks = {"subtotal lineas": find_in_pool(subtotal, pool) is not None}
    checks["total"] = find_in_pool(result.total, pool) is not None
    for rate in result.bases:
        checks[f"base {rate}%"] = find_in_pool(result.bases[rate], pool) is not None
        checks[f"cuota {rate}%"] = find_in_pool(result.cuotas[rate], pool) is not None
        if rate not in USUAL_RATES:
            result.notes.append(f"Tipo de IVA inusual ({rate}%): confirmar")
    result.evidence = checks
    result.reconciled = checks["total"] and all(
        v for k, v in checks.items() if k.startswith(("base", "cuota"))
    )


def iter_tax_ids(text: str) -> list[tuple[int, str]]:
    """Every company CIF and personal NIF/NIE in the text as (position, id), in order of appearance."""
    found = [(m.start(), m.group(1) + m.group(2)) for m in CIF_RE.finditer(text)]
    found += [(m.start(), m.group(1)) for m in PERSONAL_ID_RE.finditer(text)]
    return sorted(found)


def find_header(text: str, result: Result) -> None:
    """
    Invoice number, date and supplier CIF, with a few generic strategies:
    labelled value ('NUMERO: X', 'Factura Nº: X'), then a bare
    '<reference> <date>' line when the value is printed away from its label.
    Supplier CIF = first CIF/NIF that is not the hotel's own.
    """
    numero = re.search(
        r"(?:N[ÚU]MERO|FACTURA\s*N[ºO°]\.?|N[ºO°]\.?\s*FACTURA)[ \t]*:[ \t]*([A-Z0-9][A-Z0-9/\-]*)", text, re.I
    )
    fecha = re.search(rf"FECHA[ \t]*:[ \t]*({DATE})", text, re.I)
    if numero and as_invoice_number(numero.group(1)):
        result.numero = as_invoice_number(numero.group(1))
    if fecha:
        result.fecha = fecha.group(1)

    if not (result.numero and result.fecha) and re.search(r"FACTURA", text, re.I):
        bare = re.search(rf"^([A-Z]{{0,3}}\s?\d{{1,10}}(?:\s\d{{1,10}})?)\s+({DATE})\s*$", text, re.M)
        if bare:
            result.numero = result.numero or bare.group(1)
            result.fecha = result.fecha or bare.group(2)

    for _, tax_id in iter_tax_ids(text):
        if tax_id != own_nif():
            result.proveedor_cif = tax_id
            break


def to_date(text: str):
    """Parses dd/mm/yy(yy) (also with '-') into a date, or None."""
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def as_invoice_number(text: str) -> str | None:
    """
    The cell text if it plausibly is an invoice number: has a digit, is not
    a date, an amount, a CIF or another label. Otherwise None. This type
    check is what stops 'FECHA' or a client code from being taken as a number.
    """
    text = re.sub(r"^(?:factura\s*)?(?:n[úu]m(?:ero)?|n[ºo°])\.?\s*[:.]?\s*", "", text.strip(), flags=re.I)
    if not (2 <= len(text) <= 25) or not re.search(r"\d", text):
        return None
    if re.fullmatch(DATE, text) or re.fullmatch(r"\d+[.,]\d{2}", text) or CIF_RE.fullmatch(text):
        return None
    if NUMBER_LABEL_RE.match(text) or DATE_LABEL_RE.match(text):
        return None
    return text


def as_invoice_date(text: str) -> str | None:
    """The cell text if it is exactly one date, else None."""
    text = text.strip()
    return text if re.fullmatch(DATE, text) else None


def value_candidates(label: Cell, cells: list[Cell]) -> list[tuple[float, Cell]]:
    """
    Cells that could be the value of `label`, nearest first: directly
    below it (horizontal overlap) or to its right on the same row.
    """
    found = []
    label_width = label.right - label.left
    label_mid = (label.top + label.bottom) / 2
    for cell in cells:
        if cell is label or cell.page != label.page:
            continue
        drop = label.bottom - cell.top
        overlap = min(label.right, cell.right) - max(label.left, cell.left)
        narrow = max(min(label_width, cell.right - cell.left), 1)
        if -1 <= drop <= MAX_DROP and overlap >= 0.3 * narrow:
            found.append((max(drop, 0), cell))
            continue
        same_row = abs((cell.top + cell.bottom) / 2 - label_mid) <= ROW_TOLERANCE
        gap = cell.left - label.right
        if same_row and -1 <= gap <= MAX_GAP_RIGHT:
            found.append((max(gap, 0), cell))
    return sorted(found, key=lambda pair: pair[0])


def header_from_cells(cells: list[Cell], result: Result) -> None:
    """
    Fills a missing invoice number / date by pairing label cells with the
    value beneath or beside them, keeping the first candidate that passes
    the type check. The value seen most often across pages wins, so a
    repeated header (page 1, page 2...) acts as a consistency check.
    """
    for label_re, checker, attr in (
        (NUMBER_LABEL_RE, as_invoice_number, "numero"),
        (DATE_LABEL_RE, as_invoice_date, "fecha"),
    ):
        if getattr(result, attr):
            continue
        votes = Counter()
        for label in (c for c in cells if label_re.match(c.text.strip())):
            for _, candidate in value_candidates(label, cells):
                value = checker(candidate.text)
                if value:
                    votes[value] += 1
                    break
        if votes:
            setattr(result, attr, votes.most_common(1)[0][0])


def check_albaran_totals(text: str, iva_column: bool, result: Result) -> None:
    """
    Second reconciliation level: when an invoice prints 'Total albaran: X'
    after each delivery note, the lines since the last albaran header must
    add up to X. A mismatch points at the exact albaran with a missed or
    misread line.
    """
    current, running = None, Decimal(0)
    for raw in (l.strip() for l in text.split("\n")):
        header = ALBARAN_RE.search(raw)
        if header and not parse_line(raw, iva_column):
            current, running = header.group(1).strip(), Decimal(0)
            continue
        line = parse_line(raw, iva_column)
        if line:
            running += line.importe
            continue
        total = TOTAL_ALBARAN_RE.search(raw)
        if total and current is not None:
            result.albaran_totals[current] = (money(running), parse_number(total.group(1)))
    for ref, (summed, printed) in result.albaran_totals.items():
        if printed is None or abs(summed - printed) > Decimal("0.01"):
            result.notes.append(f"Albaran {ref}: lineas suman {summed}, total impreso {printed}")


def check_dates(result: Result) -> None:
    """Sanity: an invoice cannot be dated before the delivery notes it invoices."""
    invoice_date = to_date(result.fecha) if result.fecha else None
    albaran_dates = [to_date(l.albaran_fecha) for l in result.lines if l.albaran_fecha]
    latest = max((d for d in albaran_dates if d), default=None)
    if invoice_date and latest and invoice_date < latest:
        result.notes.append(f"Fecha de factura {result.fecha} anterior a un albaran ({latest}): revisar")


def normalise_name(text: str) -> str:
    """Upper-case letters and digits only, accents removed: a comparison key for company names."""
    table = str.maketrans("ÁÉÍÓÚÜ", "AEIOUU")
    return re.sub(r"[^A-Z0-9Ñ]", "", text.upper().translate(table))


def find_supplier_name(text: str, supplier_cif: str | None) -> str | None:
    """
    The supplier's legal name: every '<Name> <legal suffix>' in the text is
    a candidate; the hotel's own name (and anything on a line with the
    hotel's NIF) is discarded; the candidate closest to the supplier's
    CIF wins, because invoices print name and CIF together. Repetition
    breaks ties. No supplier-specific layout is assumed.
    """
    own_keys = [normalise_name(n) for n in own_names()]
    hotel_nif = own_nif()
    cif_positions = []
    if supplier_cif:
        cif_positions = [start for start, tax_id in iter_tax_ids(text) if tax_id == supplier_cif]

    candidates = {}  # key -> [display name, best distance, count]
    for match in COMPANY_RE.finditer(text):
        name, suffix = match.group("name"), match.group("suffix")
        words = name.split()
        while words and normalise_name(words[0]) in NAME_STOPWORDS:
            words.pop(0)  # 'FACTURA Acme Systems' -> 'Acme Systems'
        if not words:
            continue
        name = " ".join(words)
        key = normalise_name(name)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start: line_end if line_end != -1 else len(text)]
        is_hotel_line = bool(hotel_nif) and hotel_nif in line.replace("-", "").replace(" ", "").upper()
        if any(own in key for own in own_keys) or is_hotel_line:
            continue
        distance = min((abs(match.start() - p) for p in cif_positions), default=10**9)
        entry = candidates.setdefault(key, [f"{name} {suffix}".replace("  ", " "), distance, 0])
        entry[1] = min(entry[1], distance)
        entry[2] += 1

    if not candidates:
        return None
    best = min(candidates.values(), key=lambda e: (e[1], -e[2]))
    return best[0]


def large_image_boxes(path: Path) -> list[tuple[int, float, float, float, float]]:
    """(page, left, bottom, right, top) of every embedded image big enough to hold a text block."""
    boxes = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for page_no, page in enumerate(pdf):
            for obj in page.get_objects():
                if obj.type == pdfium_raw.FPDF_PAGEOBJ_IMAGE:
                    left, bottom, right, top = obj.get_bounds()
                    if right - left >= OCR_MIN_WIDTH and top - bottom >= OCR_MIN_HEIGHT:
                        boxes.append((page_no, left, bottom, right, top))
    finally:
        pdf.close()
    return boxes


def ocr_text_from_data(data: dict) -> tuple[str, float]:
    """
    Rebuilds text from Tesseract word data (lines, with a blank line
    between paragraphs) and returns it with the mean word confidence.
    """
    lines, confidences = [], []
    last_key = None
    for word, conf, block, par, line in zip(
        data["text"], data["conf"], data["block_num"], data["par_num"], data["line_num"]
    ):
        if not word.strip() or float(conf) < 0:
            continue
        confidences.append(float(conf))
        key = (block, par, line)
        if key != last_key:
            if last_key is not None and last_key[:2] != key[:2]:
                lines.append("")
            lines.append(word)
            last_key = key
        else:
            lines[-1] += " " + word
    mean = sum(confidences) / len(confidences) if confidences else 0.0
    return "\n".join(lines).strip(), mean


def read_image_regions(path: Path) -> tuple[list[Region], str | None]:
    """
    OCR (Tesseract) of every large image embedded in the PDF, cropped
    from a ~300 DPI render of its page. Regions whose mean word
    confidence is low (logos, decoration) are dropped. Returns
    (regions, problem); problem explains why OCR could not run, e.g.
    Tesseract not installed (set TESSERACT_CMD to its full path).
    """
    try:
        import pytesseract

        command = getattr(settings, "TESSERACT_CMD", None) or os.environ.get("TESSERACT_CMD")
        if command:
            pytesseract.pytesseract.tesseract_cmd = command
        pytesseract.get_tesseract_version()
    except Exception as error:
        return [], f"OCR de imagenes no disponible ({type(error).__name__}): instala Tesseract o define TESSERACT_CMD"

    wanted = large_image_boxes(path)
    if not wanted:
        return [], None
    regions = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for page_no in sorted({box[0] for box in wanted}):
            page = pdf[page_no]
            _, page_height = page.get_size()
            bitmap = page.render(scale=OCR_SCALE).to_pil().convert("L")
            for _, left, bottom, right, top in (b for b in wanted if b[0] == page_no):
                crop = bitmap.crop((
                    int(left * OCR_SCALE), int((page_height - top) * OCR_SCALE),
                    int(right * OCR_SCALE), int((page_height - bottom) * OCR_SCALE),
                ))
                try:
                    data = pytesseract.image_to_data(
                        crop, lang=ocr_language(), config="--psm 6", output_type=pytesseract.Output.DICT
                    )
                except Exception as error:
                    return regions, f"OCR de imagenes fallo ({type(error).__name__}): {error}"
                text, confidence = ocr_text_from_data(data)
                if text and confidence >= OCR_MIN_CONFIDENCE:
                    regions.append(Region(page_no, left, bottom, right, top, text))
    finally:
        pdf.close()
    return regions, None


def has_real_description(text: str) -> bool:
    """True if the text contains an actual word (3+ letters), not just codes like '24P'."""
    return bool(re.search(r"[A-Za-zÁÉÍÓÚÑ]{3,}", text))


def attach_descriptions(lines: list[Line], cells: list[Cell], regions: list[Region]) -> None:
    """
    For product rows whose description is an image: find the row's code
    cell, then the smallest OCR region on the same page that vertically
    contains it, and use that region's first paragraph as the description
    (the rest is usually marketing text). Each code cell is used once, so
    the same product on two rows gets its own region.
    """
    used = set()
    for line in lines:
        if not line.codigo or has_real_description(line.descripcion):
            continue
        for index, anchor in enumerate(cells):
            if index in used or anchor.text.strip() != line.codigo:
                continue
            middle = (anchor.top + anchor.bottom) / 2
            containing = [
                r for r in regions if r.page == anchor.page and r.bottom <= middle <= r.top
            ]
            if containing:
                best = min(containing, key=lambda r: (r.right - r.left) * (r.top - r.bottom))
                first_paragraph = re.split(r"\n\s*\n", best.text.strip())[0]
                line.descripcion = " ".join(first_paragraph.split())
                used.add(index)
                break


def completeness(result: Result) -> int:
    """How much of an invoice was recovered: 1 point per key field, plus lines and reconciliation."""
    fields = [result.proveedor_nombre, result.proveedor_cif, result.numero, result.fecha]
    return sum(1 for f in fields if f) + (2 if result.lines else 0) + (3 if result.reconciled else 0)


COMPLETE_SCORE = 9  # all four fields + lines + reconciled


def totals_without_lines(pool: set[Decimal], result: Result) -> None:
    """
    For invoices with no quantity x price rows (services, rent, fees):
    look for a tax block on its own. A (base, cuota) pair where cuota is
    base x a rate to the cent, and base + cuota is a printed total; also
    two such pairs with different rates whose sum is a printed total.
    The largest total that fits wins (fewest rates on a tie). This is
    weaker than reconciling against lines, so the result is only ever
    marked totals_only, never reconciled.
    """
    amounts = sorted({v for v in pool if v >= Decimal("1")}, reverse=True)
    pairs = [
        (b, c, snap_rate(b, c))
        for b in amounts
        for c in amounts
        if 0 < c < b and snap_rate(b, c) is not None
    ]
    solutions = []
    for b, c, r in pairs:
        if b + c in pool:
            solutions.append((b + c, 1, [(b, c, r)]))
    for i, (b1, c1, r1) in enumerate(pairs):
        for b2, c2, r2 in pairs[i + 1:]:
            if r1 != r2 and b1 != b2 and b1 + c1 + b2 + c2 in pool:
                solutions.append((b1 + c1 + b2 + c2, 2, [(b1, c1, r1), (b2, c2, r2)]))
    if not solutions:
        result.notes.append("Sin lineas y sin bloque de impuestos coherente")
        return
    total, _, parts = max(solutions, key=lambda s: (s[0], -s[1]))
    for base, cuota, rate in parts:
        result.bases[rate], result.cuotas[rate] = base, cuota
    result.total = total
    result.totals_only = True
    result.notes.append("Sin lineas: totales deducidos solo del bloque de impuestos")


def cross_check_tax_block(pool: set[Decimal], result: Result) -> None:
    """
    The lines did not reconcile with any total. Look for the printed tax
    block on its own and compare: if it is found, report its base and
    total (filling them in when nothing else was computed) and say how far
    the lines are from it. A positive difference means lines are missing
    (or a global discount applies); this points at the problem instead of
    leaving the total blank.
    """
    block = Result()
    totals_without_lines(pool, block)
    if not block.totals_only:
        return
    printed_base = sum(block.bases.values())
    lines_sum = money(sum((l.importe for l in result.lines), Decimal(0)))
    difference = money(printed_base - lines_sum)
    if result.total is None:
        result.bases, result.cuotas, result.total = block.bases, block.cuotas, block.total
        result.totals_only = True
    hint = "faltan lineas o hay un descuento global" if difference > 0 else "sobran lineas o hay un recargo"
    result.notes.append(
        f"Bloque de impuestos: base {printed_base}, total {block.total}; "
        f"las lineas suman {lines_sum} (diferencia {difference:+.2f}): {hint}"
    )


def extract_text(
    text: str,
    cells: list[Cell] | None = None,
    clean_text: str | None = None,
    regions: list[Region] | None = None,
) -> Result:
    """
    Runs the full template-free extraction on already-extracted text (any
    source: pdfium, OCR, Docling rows). Optional inputs: positioned
    cells (fill in header fields the inline patterns missed),
    `clean_text` (kerning-repaired, used for the supplier name), and
    `regions` (OCR of images embedded in the PDF, which supply the text
    that is missing from the text layer: header block, descriptions...).
    """
    result = Result()
    extra = "\n".join(r.text for r in regions) if regions else ""
    combined = text + ("\n" + extra if extra else "")
    # The text layer (and positioned cells) are more reliable than OCR, so they
    # decide first; OCR text only fills fields that are still empty.
    find_header(text, result)
    if cells and not (result.numero and result.fecha):
        header_from_cells(cells, result)
    if extra:
        from_ocr = Result()
        find_header(extra, from_ocr)
        result.numero = result.numero or from_ocr.numero
        result.fecha = result.fecha or from_ocr.fecha
        result.proveedor_cif = result.proveedor_cif or from_ocr.proveedor_cif
    name_source = (clean_text or text) + ("\n" + extra if extra else "")
    result.proveedor_nombre = find_supplier_name(name_source, result.proveedor_cif)
    if not result.proveedor_nombre:
        result.notes.append("Nombre del proveedor no encontrado")
    result.lines = parse_lines(text)
    if result.lines and regions and cells:
        attach_descriptions(result.lines, cells, regions)
    if result.lines:
        solve_totals(result.lines, money_tokens_in(combined), all_numbers_in(combined), result)
        if not result.reconciled:
            cross_check_tax_block(money_tokens_in(combined), result)
        check_albaran_totals(text, has_iva_column(text.split("\n")), result)
        check_dates(result)
    else:
        result.notes.append("No product lines recognised")
        totals_without_lines(money_tokens_in(combined), result)
    return result


def extract(path: Path) -> Result:
    """
    Full extraction of one PDF. First pass uses only the text layer
    (fast). If the result is incomplete AND the PDF has embedded images
    big enough to hold text, those images are read with OCR and the
    extraction is repeated with their text; the better result is kept.
    """
    text, cells, repaired = read_pdf_text(path), read_pdf_cells(path), read_pdf_text_repaired(path)
    result = extract_text(text, cells, repaired)
    if completeness(result) >= COMPLETE_SCORE or not large_image_boxes(path):
        return result

    regions, problem = read_image_regions(path)
    if regions:
        improved = extract_text(text, cells, repaired, regions)
        improved.ocr_regions_used = len(regions)
        if completeness(improved) >= completeness(result):
            return improved
    elif problem:
        result.notes.append(problem)
    return result


INFO_NOTES = ("Nombre del proveedor no encontrado", "No product lines recognised")


def review_problems(result: Result) -> list[str]:
    """
    Everything that needs a human look on this result, as short Spanish
    reasons: missing fields, totals that do not reconcile, and the notes
    the checks produced. An empty list means the invoice can be trusted.
    """
    problems = []
    if not result.proveedor_nombre:
        problems.append("sin nombre de proveedor")
    if not result.proveedor_cif:
        problems.append("sin CIF de proveedor")
    if not result.numero:
        problems.append("sin numero de factura")
    if not result.fecha:
        problems.append("sin fecha")
    if not result.lines:
        problems.append("ninguna linea reconocida")
    elif not result.reconciled:
        problems.append("los totales NO cuadran")
    problems += [note for note in result.notes if note not in INFO_NOTES]
    return problems


def decimal_text(value: Decimal | None, places: int | None = None) -> str | None:
    """A Decimal as plain text (never exponent notation), optionally rounded to cents; None stays None."""
    if value is None:
        return None
    return format(money(value) if places == 2 else value, "f")


def rate_text(value: Decimal | None) -> str | None:
    """A tax rate without pointless zeros: 21.0 -> '21', 7.5 -> '7.5'."""
    return None if value is None else format(value.normalize(), "f")


def iso_date(text: str | None) -> str | None:
    """'16/03/2026' or '17/02/26' -> '2026-03-16' / '2026-02-17'; None if it cannot be read."""
    parsed = to_date(text) if text else None
    return parsed.isoformat() if parsed else None


def result_to_dict(result: Result | None, archivo: str, estado: str, motivos: list[str]) -> dict:
    """
    One invoice as a JSON-ready dict (schema 'factura/1'). Money and
    rates are strings so no float rounding can creep in; dates are ISO
    (the text as printed is kept too). `result` may be None for files
    that could not be extracted (scans, corrupt files): the same keys
    are returned, empty, so consumers always see one shape.
    """
    empty = result is None
    result = result or Result()
    base_total = sum(result.bases.values(), Decimal(0)) if result.bases else None
    iva_total = sum(result.cuotas.values(), Decimal(0)) if result.cuotas else None

    if result.reconciled:
        metodo = "lineas_y_bloque_impuestos"
    elif result.totals_only:
        metodo = "solo_bloque_impuestos"
    elif result.total is not None:
        metodo = "calculado_de_lineas_sin_confirmar"
    else:
        metodo = None

    return {
        "esquema": "factura/1",
        "archivo": archivo,
        "estado": estado,
        "proveedor": {"nombre": result.proveedor_nombre, "cif": result.proveedor_cif},
        "factura": {
            "numero": result.numero,
            "fecha": iso_date(result.fecha),
            "fecha_texto": result.fecha,
        },
        "lineas": [
            {
                "codigo": line.codigo or None,
                "descripcion": line.descripcion or None,
                "cantidad": decimal_text(line.cantidad),
                "precio_unitario": decimal_text(line.precio),
                "iva_porcentaje": rate_text(line.iva),
                "importe": decimal_text(line.importe, 2),
                "albaran": line.albaran or None,
                "albaran_fecha": iso_date(line.albaran_fecha),
            }
            for line in result.lines
        ],
        "impuestos": [
            {"tipo": rate_text(rate), "base": decimal_text(result.bases[rate], 2), "cuota": decimal_text(result.cuotas[rate], 2)}
            for rate in sorted(result.bases, reverse=True)
        ],
        "totales": {
            "total_sin_iva": decimal_text(base_total, 2),
            "iva": decimal_text(iva_total, 2),
            "total_con_iva": decimal_text(result.total, 2),
        },
        "verificacion": {
            "cuadra": bool(result.reconciled),
            "metodo": metodo,
            "comprobaciones": {name: bool(ok) for name, ok in result.evidence.items()},
            "albaranes": {
                ref: {
                    "suma_lineas": decimal_text(summed, 2),
                    "total_impreso": decimal_text(printed, 2),
                    "cuadra": printed is not None and abs(summed - printed) <= Decimal("0.01"),
                }
                for ref, (summed, printed) in result.albaran_totals.items()
            },
            "ocr_imagenes": result.ocr_regions_used,
            "avisos": motivos,
            "confirmado": False,
            "corregido": False,
        },
    }


def report(path: Path, result: Result) -> None:
    """Prints one invoice's extraction in a compact, human-checkable form."""
    print("=" * 72)
    print(path.name)
    print(f"  proveedor: {result.proveedor_nombre} (CIF {result.proveedor_cif})")
    print(f"  numero {result.numero} | fecha {result.fecha}")
    print(f"  {len(result.lines)} lineas:")
    for l in result.lines:
        iva = f"{l.iva}%" if l.iva is not None else "  - "
        print(
            f"    {l.codigo:>13} {l.descripcion[:34]:<34} IVA {iva:>4} "
            f"{l.cantidad:>7} x {l.precio:>9} = {l.importe:>8}   [alb {l.albaran}]"
        )
    for rate in result.bases:
        print(f"  IVA {rate}%: base {result.bases[rate]}  cuota {result.cuotas[rate]}")
    print(f"  TOTAL calculado: {result.total}")
    print(f"  evidencia en el texto: {result.evidence}")
    if result.albaran_totals:
        ok = sum(1 for s, p in result.albaran_totals.values() if p is not None and abs(s - p) <= Decimal("0.01"))
        print(f"  albaranes que cuadran: {ok}/{len(result.albaran_totals)}")
    if result.ocr_regions_used:
        print(f"  (completado leyendo {result.ocr_regions_used} imagenes incrustadas con OCR)")
    print(f"  CUADRA: {'SI' if result.reconciled else 'NO -> revisar'}")
    for note in result.notes:
        print(f"  nota: {note}")
