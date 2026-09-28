"""
Connects the extraction engine (extractor.py) to the database.

    process_document(document)   run extraction and store the result
    save_extraction(document, d) store an already-built "factura/1" dict
    document_to_dict(document)   rebuild the JSON from what is in the database

The JSON is always rebuilt from the database, never cached, so a manual
correction made on the review screen is reflected in every export.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction

from invoice_data.models import (
    EstadoExtraccion,
    ExtractedInvoiceData,
    InvoiceDocument,
    InvoiceLineItem,
    InvoiceTaxBreakdown,
    Proveedor,
)
from invoice_data.services import extractor
from invoice_data.services.ocr import run_ocr


class DatosConfirmados(Exception):
    """The document already has confirmed data; re-extracting would overwrite a person's review."""


def build_result(document: InvoiceDocument) -> extractor.Result:
    """
    Runs the right extraction path for a document:
    - digital PDF (with or without embedded images): the engine reads the
      text layer and OCRs embedded images only if the first pass is incomplete;
    - scan or photo (no text layer): full-page OCR first, then the same
      arithmetic-based extraction over the OCR text.
    """
    if document.needs_ocr:
        return extractor.extract_text(run_ocr(document))
    return extractor.extract(Path(document.file.path))


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
