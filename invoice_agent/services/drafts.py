"""
Borradores: rellenarlos desde una extracción, detectar duplicados y
revalidarlos (tras la extracción y tras cada corrección).
"""

from decimal import Decimal

from invoice_agent.models import (
    AccionRevision,
    Borrador,
    BorradorImpuesto,
    BorradorLinea,
    Empresa,
    EstadoDocumento,
    EventoRevision,
    Extraccion,
)
from invoice_agent.schemas import COLUMN_TO_FIELD, FIELD_TO_COLUMN
from invoice_agent.services import validation
from invoice_agent.services.normalize import (
    InvoiceData,
    normalize_invoice_number,
    normalize_tax_id,
)

HEADER_COLUMNS = [c for c in FIELD_TO_COLUMN.values() if c != "cliente_nif"]
LINE_COLUMNS = ["descripcion", "cantidad", "precio_unitario", "descuento", "tipo_impositivo", "subtotal", "total"]
TAX_COLUMNS = ["tipo", "porcentaje", "base", "cuota"]


def json_value(value):
    """Valor serializable para JSON (auditoría, evidencias)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def fit(model, column: str, value):
    """
    Ajusta un valor a la columna: textos recortados a max_length y números
    que no caben en NUMERIC(max_digits, decimal_places) -> None (lectura
    imposible) en vez de que PostgreSQL rechace toda la fila.
    """
    if value is None:
        return None
    field = model._meta.get_field(column)
    if isinstance(value, str):
        limit = getattr(field, "max_length", None)
        return value[:limit] if limit else value
    if isinstance(value, Decimal) and getattr(field, "max_digits", None):
        integer_digits = field.max_digits - field.decimal_places
        return value if abs(value) < Decimal(10) ** integer_digits else None
    return value


def evidence_dict(data: InvoiceData) -> dict:
    return {
        name: {
            "state": fv.state,
            "confidence": fv.confidence,
            "page": fv.page,
            "text": fv.text,
            "extracted_value": json_value(fv.value),
            "raw": fv.raw,
        }
        for name, fv in data.fields.items()
    }


def update_keys(borrador: Borrador) -> None:
    borrador.proveedor_nif_norm = normalize_tax_id(borrador.proveedor_nif)[:40]
    borrador.numero_norm = normalize_invoice_number(borrador.numero_factura)


def fill_from_extraction(borrador: Borrador, extraccion: Extraccion) -> None:
    """Copia lo extraído al borrador (cabecera, líneas, impuestos) y borra correcciones y avisos aceptados."""
    borrador.extraccion = extraccion
    for column in HEADER_COLUMNS:
        setattr(borrador, column, getattr(extraccion, column))
    borrador.avisos_aceptados = []
    borrador.corregido = False
    update_keys(borrador)
    borrador.save()
    borrador.lineas.all().delete()
    borrador.impuestos.all().delete()
    BorradorLinea.objects.bulk_create(
        BorradorLinea(borrador=borrador, orden=line.orden, **{c: getattr(line, c) for c in LINE_COLUMNS})
        for line in extraccion.lineas.all()
    )
    BorradorImpuesto.objects.bulk_create(
        BorradorImpuesto(borrador=borrador, **{c: getattr(tax, c) for c in TAX_COLUMNS})
        for tax in extraccion.impuestos.all()
    )


def find_duplicates(borrador: Borrador) -> list[dict]:
    """
    Posibles duplicados dentro de la misma empresa (nunca se mira otra empresa):
    - business_key: mismo NIF de proveedor y mismo número normalizado
    - same_amount_date: mismo proveedor, misma fecha y mismo total, otro número
    - legacy: la misma factura ya registrada en invoice_data (Gestión de Facturas)
    Los documentos rechazados no cuentan. El duplicado por fichero idéntico
    (SHA-256) se resuelve antes, en la subida.
    """
    found = []
    nif, numero = borrador.proveedor_nif_norm, borrador.numero_norm
    if not nif:
        return found
    others = (
        Borrador.objects.filter(empresa_id=borrador.empresa_id, proveedor_nif_norm=nif)
        .exclude(pk=borrador.pk)
        .exclude(documento__estado=EstadoDocumento.REJECTED)
        .select_related("documento")
    )
    if numero:
        for other in others.filter(numero_norm=numero)[:5]:
            found.append(
                {
                    "kind": "business_key",
                    "documento": str(other.documento.public_id),
                    "approved": other.aprobado_en is not None,
                }
            )
    if borrador.fecha_factura and borrador.total_bruto is not None:
        same = others.filter(fecha_factura=borrador.fecha_factura, total_bruto=borrador.total_bruto)
        if numero:
            same = same.exclude(numero_norm=numero)
        for other in same[:5]:
            found.append({"kind": "same_amount_date", "documento": str(other.documento.public_id)})
    if numero:
        from invoice_data.models import ExtractedInvoiceData

        candidates = ExtractedInvoiceData.objects.filter(proveedor_cif__icontains=nif[-8:]).only(
            "proveedor_cif", "numero_factura", "document_id"
        )[:200]
        for legacy in candidates:
            if normalize_tax_id(legacy.proveedor_cif) == nif and normalize_invoice_number(legacy.numero_factura) == numero:
                found.append({"kind": "legacy", "documento": legacy.document_id})
                break
    return found


def corrected_fields(borrador: Borrador) -> set[str]:
    return set(
        EventoRevision.objects.filter(borrador=borrador, accion=AccionRevision.CORRECTED).values_list("campo", flat=True)
    )


def build_context(borrador: Borrador) -> validation.ValidationContext:
    from invoice_agent.services.normalize import normalize_tax_id as norm

    page_problems = []
    extraccion = borrador.extraccion
    if extraccion is not None:
        for page in (extraccion.intento.preparacion or {}).get("pages", []):
            for message in page.get("problems", []):
                page_problems.append({"page": page.get("number"), "message": message})
    return validation.ValidationContext(
        empresa_nif=norm(borrador.empresa.nif),
        own_nifs=frozenset(norm(n) for n in Empresa.objects.values_list("nif", flat=True)),
        duplicates=find_duplicates(borrador),
        page_problems=page_problems,
    )


def draft_data(borrador: Borrador) -> InvoiceData:
    """Datos validables del borrador, conservando la confianza extraída de los campos no corregidos."""
    from invoice_agent.services.normalize import from_draft

    extraccion = borrador.extraccion
    evidencias = extraccion.evidencias if extraccion else {}
    data = from_draft(borrador, evidencias)
    corrected = corrected_fields(borrador)
    for name, fv in data.fields.items():
        column = FIELD_TO_COLUMN[name]
        ev = evidencias.get(name) or {}
        if column not in corrected and fv.state == "present" and ev.get("state") == "present":
            fv.confidence = ev.get("confidence")
    if extraccion is not None:
        data.document_type = (extraccion.salida_modelo or {}).get("document_type", "invoice")
        # Las incidencias de línea ilegible dependen de lo extraído mientras no se corrijan las líneas.
        if "lineas" not in corrected:
            raw_lines = (extraccion.salida_modelo or {}).get("line_items", [])
            for index, line in enumerate(data.lines):
                if index < len(raw_lines):
                    line.unreadable = list(raw_lines[index].get("unreadable_fields", []))
    return data


def revalidate(borrador: Borrador) -> list[dict]:
    """Valida el borrador, guarda las incidencias y limpia avisos aceptados que ya no aplican."""
    issues = [issue.to_dict() for issue in validation.validate(draft_data(borrador), build_context(borrador))]
    current_codes = {i["code"] for i in issues if i["severity"] == "warning"}
    borrador.incidencias = issues
    borrador.avisos_aceptados = [code for code in borrador.avisos_aceptados if code in current_codes]
    borrador.save(update_fields=["incidencias", "avisos_aceptados", "actualizado"])
    return issues


def blockers(borrador: Borrador) -> list[dict]:
    return validation.pending_blockers(borrador.incidencias, borrador.avisos_aceptados)


def column_label(column: str) -> str:
    name = COLUMN_TO_FIELD.get(column)
    return validation.FIELD_LABELS.get(name, column) if name else column
