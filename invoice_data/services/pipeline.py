"""
Connects the extraction engine (extractor.py) to the database.

    process_document(document)   run extraction and store the result
    save_extraction(document, d) store an already-built "factura/1" dict
    document_to_dict(document)   rebuild the JSON from what is in the database

The JSON is always rebuilt from the database, never cached, so a manual
correction made on the review screen is reflected in every export.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction

from invoice_data.models import (
    EstadoExtraccion,
    ExtractedInvoiceData,
    IngestionStatus,
    InvoiceDocument,
    InvoiceLineItem,
    InvoiceTaxBreakdown,
    Proveedor,
)
from invoice_data.services import extractor
from invoice_data.services.ocr import OcrWord, get_pages_for_ocr, get_raw_pages, group_words_into_rows, ocr_pages
from invoice_data.services.preprocessing import preprocess_document

AMOUNT_COLUMN_X_TOLERANCE = 150  # px: amount-shaped words within this x-gap of each other are one column
AMOUNT_COLUMN_MARGIN = 20  # px: description text must end at least this far left of the amount column


class DatosConfirmados(Exception):
    """The document already has confirmed data; re-extracting would overwrite a person's review."""


def _ocr_quality(result: extractor.Result) -> tuple[bool, int]:
    """
    Sort key for comparing two OCR attempts on the same document:
    reconciled beats not-reconciled; among two reconciled (or two
    not-reconciled) results, fewer review problems is better.
    """
    return (result.reconciled, -len(extractor.review_problems(result)))


def _cluster_amount_column_x(amount_words: list[OcrWord]) -> float | None:
    """
    The left edge of the amount column, or None if the amount-shaped
    words on the page don't form a clear column. Finds the LARGEST group
    of amount-words whose x-positions sit within AMOUNT_COLUMN_X_TOLERANCE
    of each other (simple 1D clustering on sorted x); requires that group
    to have at least 2 members and cover at least half of all amount-shaped
    words found, so a couple of incidental numbers scattered across
    ordinary text don't get mistaken for a real column.
    """
    if len(amount_words) < 2:
        return None
    xs = sorted(w.x for w in amount_words)
    best_group, current = [], [xs[0]]
    for x in xs[1:]:
        if x - current[-1] <= AMOUNT_COLUMN_X_TOLERANCE:
            current.append(x)
        else:
            if len(current) > len(best_group):
                best_group = current
            current = [x]
    if len(current) > len(best_group):
        best_group = current
    if len(best_group) < 2 or len(best_group) < len(amount_words) / 2:
        return None
    return min(best_group)


def reconstruct_column_table(words: list[OcrWord]) -> list[extractor.Line] | None:
    """
    Reads a two-column table (descriptions in one column, amounts in a
    separate column - NOT on the same printed line) from OCR'd words.
    Tried only as a last-resort fallback, after normal same-line parsing
    has already failed - see build_result().

    Column detection: any word whose text is a whole, valid money amount
    is a candidate; _cluster_amount_column_x finds whether enough of them
    sit in a tight x-band to call it a real column. No document-specific
    pixel position is assumed - it's derived fresh from each page.

    Pairing: NOT by matching y-position. Testing against a real invoice
    showed its two columns are not pixel-aligned row by row (the amount
    column is laid out independently of how the, sometimes multi-line,
    description text wraps). Instead, valid description rows (outside
    the amount column, with totals-block/heading noise filtered out via
    extractor.is_valid_charge_description - the same rule parse_simple_line
    uses) and amount values are each sorted top-to-bottom on their own,
    then paired by RANK (1st description with 1st amount, and so on).

    This only returns lines when the two counts match EXACTLY. A
    mismatch (OCR dropped a token, misread something into looking like
    a heading, etc.) means the pairing can't be trusted - guessing a
    misaligned pairing would be worse than reporting nothing, so this
    returns None rather than a wrong result whenever the counts differ.
    """
    # Rows (across the WHOLE page width) that contain a totals-block word -
    # e.g. a 'Subtotal 2.266,85' row where the label and its own amount
    # happen to share a row. Any money-word in one of these rows is a
    # summary figure, not a charge, and must not be counted as one - even
    # though it sits in the same x-column as the real charge amounts.
    stopword_rows = {
        id(w)
        for row in group_words_into_rows(words)
        if any(
            t in extractor.SIMPLE_LINE_STOPWORDS
            for t in re.split(r"[\s,:.]+", " ".join(x.text for x in row).upper())
        )
        for w in row
    }

    amount_words = [w for w in words if extractor.MONEY_TOKEN.fullmatch(w.text) and id(w) not in stopword_rows]
    column_x = _cluster_amount_column_x(amount_words)
    if column_x is None:
        return None

    amounts = sorted((w for w in amount_words if w.x >= column_x - 5), key=lambda w: w.y)
    description_words = [w for w in words if w.x < column_x - AMOUNT_COLUMN_MARGIN]

    descriptions = []
    for row in group_words_into_rows(description_words):
        text = " ".join(w.text for w in sorted(row, key=lambda w: w.x))
        if extractor.is_valid_charge_description(text):
            descriptions.append(text)

    if not descriptions or len(descriptions) != len(amounts):
        return None

    return [
        extractor.Line("", desc, None, None, None, extractor.parse_number(amount.text))
        for desc, amount in zip(descriptions, amounts)
    ]


def _store_ocr_text(document: InvoiceDocument, text: str) -> None:
    """Saves OCR text on the document (same fields run_ocr() would set), without re-running OCR."""
    document.ocr_text = text
    document.status = IngestionStatus.OCR_DONE
    document.save(update_fields=["ocr_text", "status"])


def build_result(document: InvoiceDocument) -> extractor.Result:
    """
    Runs the right extraction path for a document:
    - digital PDF (with or without embedded images): the engine reads the
      text layer and OCRs embedded images only if the first pass is incomplete;
    - scan or photo (no text layer): OCRs the RAW page first (cheap - no
      image cleanup cost). Only if that doesn't reconcile does it ALSO
      try a cleaned-up version (preprocess_document) and keep whichever
      result is genuinely better.

      This two-tier approach exists because testing against real scans
      showed preprocessing is NOT a safe default: on one real document it
      fixed a badly-garbled amount, but on the SAME document it also
      broke a different amount and part of a CIF that the raw scan had
      read correctly. Applying it unconditionally would sometimes make a
      good result worse, so it is only used when the raw attempt already
      failed, and only kept if it demonstrably improves on it.

      If the best attempt still has no lines and doesn't reconcile, a
      last-resort tier tries reconstruct_column_table() on that attempt's
      OCR words - for invoices whose descriptions and amounts sit in
      separate columns rather than on the same text line (see that
      function's docstring). This only ever fills in .lines when it is
      confident (an exact description/amount count match); otherwise the
      result is left exactly as the OCR attempts already produced it.
    """
    if not document.needs_ocr:
        return extractor.extract(Path(document.file.path))

    raw_text, raw_words = ocr_pages(get_raw_pages(document))
    raw_result = extractor.extract_text(raw_text)
    if raw_result.reconciled:
        _store_ocr_text(document, raw_text)
        return raw_result

    preprocess_document(document, force=True)
    cleaned_text, cleaned_words = ocr_pages(get_pages_for_ocr(document))  # prefers the just-saved InvoicePage rows
    cleaned_result = extractor.extract_text(cleaned_text)

    if _ocr_quality(cleaned_result) > _ocr_quality(raw_result):
        best_result, best_text, best_words = cleaned_result, cleaned_text, cleaned_words
    else:
        best_result, best_text, best_words = raw_result, raw_text, raw_words  # cleaning did not help (or hurt)
    _store_ocr_text(document, best_text)

    if not best_result.reconciled:
        # Tried whenever the invoice hasn't reconciled, even if same-line
        # parsing already produced SOME lines: on a genuine two-column
        # table, OCR's flattened reading order can accidentally fuse an
        # unrelated description and amount onto one output row, giving
        # parse_line/parse_simple_line a non-empty but wrong result that
        # would otherwise block this fallback from ever being tried.
        table_lines = reconstruct_column_table(best_words)
        if table_lines:
            best_result.lines = table_lines
            extractor.solve_totals(
                table_lines, extractor.money_tokens_in(best_text), extractor.all_numbers_in(best_text), best_result
            )
            # The exact-count safety gate in reconstruct_column_table rules out an
            # obviously wrong pairing, but not a heading row that happens to read as
            # a plausible description - a real test document showed this can shift
            # every pairing by one position while the count still matches. Flag it
            # explicitly so a reviewer knows to check line-by-line against the
            # document, not just trust that the reconciliation check would catch it.
            best_result.notes.append(
                "Líneas obtenidas emparejando dos columnas (descripción e importe en "
                "columnas separadas): verificar manualmente que cada importe corresponde "
                "a la línea correcta."
            )

    return best_result


MAX_AMOUNT = Decimal("9999999")  # largest amount that fits every DecimalField (max_digits=12, up to 4 places)


def _decimal(text: str | None) -> Decimal | None:
    """
    '154.64' -> Decimal('154.64'). None, unreadable, or absurdly large
    values (typical of OCR garbage) become None instead of reaching the
    database, where PostgreSQL would reject the whole record.
    """
    if text in (None, ""):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if abs(value) <= MAX_AMOUNT else None


def _cut(text: str | None, length: int) -> str:
    """Text trimmed to a column's max_length (PostgreSQL rejects over-long values; SQLite would not notice)."""
    return (text or "")[:length]


def _date(text: str | None) -> date | None:
    """ISO date text -> date; None or unreadable -> None."""
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def supplier_for(cif: str | None, nombre: str | None) -> Proveedor | None:
    """
    The Proveedor for a CIF, created if new. An existing supplier keeps its
    name (it may have been corrected by hand) unless it had none. No CIF
    means no supplier link.
    """
    cif = (cif or "").strip()[:20]
    if not cif:
        return None
    nombre = _cut(nombre, 200)
    proveedor, created = Proveedor.objects.get_or_create(cif=cif[:20], defaults={"nombre": nombre})
    if not created and nombre and not proveedor.nombre:
        proveedor.nombre = nombre
        proveedor.save(update_fields=["nombre"])
    return proveedor


def save_extraction(document: InvoiceDocument, data: dict) -> ExtractedInvoiceData:
    """
    Stores a "factura/1" dict as rows: header, IVA breakdown and lines.
    Everything previously stored for the document is replaced in the same
    transaction, so a failure never leaves half an invoice.
    """
    verificacion = data["verificacion"]
    with transaction.atomic():
        ExtractedInvoiceData.objects.filter(document=document).delete()
        document.impuestos.all().delete()
        document.lineas.all().delete()

        document.proveedor = supplier_for(data["proveedor"]["cif"], data["proveedor"]["nombre"])
        document.save(update_fields=["proveedor"])

        datos = ExtractedInvoiceData.objects.create(
            document=document,
            numero_factura=_cut(data["factura"]["numero"], 100),
            fecha_factura=_date(data["factura"]["fecha"]),
            fecha_texto=_cut(data["factura"]["fecha_texto"], 30),
            proveedor_nombre=_cut(data["proveedor"]["nombre"], 200),
            proveedor_cif=_cut(data["proveedor"]["cif"], 20),
            base_imponible=_decimal(data["totales"]["total_sin_iva"]),
            iva_total=_decimal(data["totales"]["iva"]),
            total=_decimal(data["totales"]["total_con_iva"]),
            cuadra=verificacion["cuadra"],
            metodo=_cut(verificacion["metodo"], 40),
            estado=EstadoExtraccion.OK if data["estado"] == "OK" else EstadoExtraccion.REVISAR,
            verificacion={
                "comprobaciones": verificacion["comprobaciones"],
                "albaranes": verificacion["albaranes"],
                "ocr_imagenes": verificacion["ocr_imagenes"],
                "avisos": verificacion["avisos"],
            },
            motor_version=extractor.VERSION,
        )
        InvoiceTaxBreakdown.objects.bulk_create(
            InvoiceTaxBreakdown(document=document, tipo=tipo, base=base, cuota=cuota)
            for row in data["impuestos"]
            if None not in (tipo := _decimal(row["tipo"]), base := _decimal(row["base"]), cuota := _decimal(row["cuota"]))
        )
        InvoiceLineItem.objects.bulk_create(
            InvoiceLineItem(
                document=document,
                codigo_articulo=_cut(row["codigo"], 50),
                descripcion=_cut(row["descripcion"], 500),
                cantidad=_decimal(row["cantidad"]),
                precio_unitario=_decimal(row["precio_unitario"]),
                iva_porcentaje=_decimal(row["iva_porcentaje"]),
                importe=_decimal(row["importe"]),
                albaran=_cut(row["albaran"], 100),
                albaran_fecha=_date(row["albaran_fecha"]),
            )
            for row in data["lineas"]
        )
    return datos


def process_document(document: InvoiceDocument, force: bool = False) -> ExtractedInvoiceData:
    """
    Extracts one document and stores the result. Extraction (which can take
    seconds when OCR is needed) runs before the database transaction, so a
    failure while reading the file never touches stored data.

    Raises DatosConfirmados if a person already confirmed this document's
    data, unless force=True.
    """
    existing = getattr(document, "datos", None)
    if existing is not None and existing.confirmado and not force:
        raise DatosConfirmados(document.original_filename)

    result = build_result(document)
    problems = extractor.review_problems(result)
    data = extractor.result_to_dict(
        result, document.original_filename, "REVISAR" if problems else "OK", problems
    )
    return save_extraction(document, data)


def document_to_dict(document: InvoiceDocument) -> dict:
    """
    The document as a "factura/1" dict, rebuilt from the database (so it
    includes manual corrections and the confirmation flag). A document
    never extracted returns the same shape, empty, with estado "PENDIENTE".
    """
    datos = getattr(document, "datos", None)
    if datos is None:
        return extractor.result_to_dict(None, document.original_filename, "PENDIENTE", [])

    stored = datos.verificacion or {}
    return {
        "esquema": "factura/1",
        "archivo": document.original_filename,
        "estado": "OK" if datos.estado == EstadoExtraccion.OK else "REVISAR",
        "proveedor": {
            "nombre": datos.proveedor_nombre or None,
            "cif": datos.proveedor_cif or None,
        },
        "factura": {
            "numero": datos.numero_factura or None,
            "fecha": datos.fecha_factura.isoformat() if datos.fecha_factura else None,
            "fecha_texto": datos.fecha_texto or None,
        },
        "lineas": [
            {
                "codigo": line.codigo_articulo or None,
                "descripcion": line.descripcion or None,
                "cantidad": extractor.decimal_text(line.cantidad),
                "precio_unitario": extractor.decimal_text(line.precio_unitario),
                "iva_porcentaje": extractor.rate_text(line.iva_porcentaje),
                "importe": extractor.decimal_text(line.importe, 2),
                "albaran": line.albaran or None,
                "albaran_fecha": line.albaran_fecha.isoformat() if line.albaran_fecha else None,
            }
            for line in document.lineas.all()
        ],
        "impuestos": [
            {
                "tipo": extractor.rate_text(row.tipo),
                "base": extractor.decimal_text(row.base, 2),
                "cuota": extractor.decimal_text(row.cuota, 2),
            }
            for row in document.impuestos.all()
        ],
        "totales": {
            "total_sin_iva": extractor.decimal_text(datos.base_imponible, 2),
            "iva": extractor.decimal_text(datos.iva_total, 2),
            "total_con_iva": extractor.decimal_text(datos.total, 2),
        },
        "verificacion": {
            "cuadra": datos.cuadra,
            "metodo": datos.metodo or None,
            "comprobaciones": stored.get("comprobaciones", {}),
            "albaranes": stored.get("albaranes", {}),
            "ocr_imagenes": stored.get("ocr_imagenes", 0),
            "avisos": stored.get("avisos", []),
            "confirmado": datos.confirmado,
            "corregido": datos.corregido,
        },
    }