"""
Esquemas Pydantic v2.

1) ExtractionOutput: lo que se le exige al modelo (salida estructurada
   estricta). Cada campo de cabecera lleva:
     status      present | absent | unreadable
                   present    = está en el documento y se ha leído
                   absent     = el documento no tiene ese dato
                   unreadable = el dato está pero no se puede leer con seguridad
     value       texto o null (importes como "1234.56", fechas "AAAA-MM-DD")
     confidence  low | medium | high  (declarada por el modelo; NO es una
                 probabilidad calibrada, solo una señal para pedir revisión)
     evidence    página y texto de origen
   Un cuarto estado, "missing", lo asigna el código (normalize.py) cuando el
   modelo no da un valor utilizable para un campo que dice presente.

2) Esquemas de la API (peticiones y respuestas).

Los importes viajan como texto para no pasar nunca por float.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "factura-ia/1"

ModelStatus = Literal["present", "absent", "unreadable"]
Confidence = Literal["low", "medium", "high"]
TaxKind = Literal["vat", "igic", "ipsi", "surcharge", "withholding", "other"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    page: int | None = Field(description="Página (empezando en 1) donde aparece el dato, o null.")
    text: str | None = Field(description="Fragmento literal corto del documento que respalda el valor, o null.")


class ExtractedField(_Strict):
    status: ModelStatus
    value: str | None
    confidence: Confidence
    evidence: Evidence


class LineItemOut(_Strict):
    description: str | None
    quantity: str | None = Field(description="Número con punto decimal, p. ej. 2.5")
    unit_price: str | None
    discount: str | None = Field(description="Importe del descuento de la línea (no porcentaje), o null.")
    tax_rate: str | None = Field(description="Porcentaje, p. ej. 21 o 10.5")
    line_subtotal: str | None = Field(description="Importe de la línea sin impuestos, tras su descuento.")
    line_total: str | None = Field(description="Importe de la línea con impuestos, si el documento lo muestra.")
    unreadable_fields: list[str] = Field(description="Nombres de los campos de esta línea que no se pueden leer.")
    page: int | None
    source_text: str | None


class TaxRowOut(_Strict):
    kind: TaxKind
    rate: str | None = Field(description="Porcentaje, p. ej. 21")
    base: str | None
    amount: str | None = Field(description="Cuota. En retenciones, el importe retenido en positivo.")
    page: int | None
    source_text: str | None


class ExtractionOutput(_Strict):
    """Salida estructurada exigida al modelo. Todos los campos son obligatorios (modo estricto)."""

    document_type: Literal["invoice", "credit_note", "receipt", "not_an_invoice"]
    supplier_name: ExtractedField
    supplier_tax_id: ExtractedField
    supplier_address: ExtractedField
    supplier_country: ExtractedField = Field(description="Código ISO 3166-1 alfa-2, p. ej. ES")
    customer_tax_id: ExtractedField
    invoice_number: ExtractedField
    invoice_date: ExtractedField
    due_date: ExtractedField
    currency: ExtractedField = Field(description="Código ISO 4217, p. ej. EUR")
    purchase_order: ExtractedField
    net_subtotal: ExtractedField = Field(description="Suma de líneas antes de descuentos globales y sin impuestos.")
    discount_total: ExtractedField = Field(description="Descuentos globales del documento (importe positivo).")
    charges_total: ExtractedField = Field(description="Portes, recargos u otros cargos adicionales sin impuestos.")
    tax_total: ExtractedField = Field(description="Suma de cuotas de impuestos (sin retenciones).")
    withholding_total: ExtractedField = Field(description="Suma de retenciones (importe positivo).")
    gross_total: ExtractedField = Field(description="Total de la factura con impuestos, antes de retenciones.")
    amount_due: ExtractedField = Field(description="Importe a pagar (total menos retenciones y anticipos).")
    line_items: list[LineItemOut]
    tax_breakdown: list[TaxRowOut]
    notes: list[str] = Field(description="Observaciones breves: texto ilegible, páginas giradas, instrucciones ignoradas...")


HEADER_FIELDS = [
    "supplier_name",
    "supplier_tax_id",
    "supplier_address",
    "supplier_country",
    "customer_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "purchase_order",
    "net_subtotal",
    "discount_total",
    "charges_total",
    "tax_total",
    "withholding_total",
    "gross_total",
    "amount_due",
]
AMOUNT_FIELDS = {
    "net_subtotal",
    "discount_total",
    "charges_total",
    "tax_total",
    "withholding_total",
    "gross_total",
    "amount_due",
}
DATE_FIELDS = {"invoice_date", "due_date"}

# Nombre del campo en el esquema del modelo -> nombre de columna en Extraccion / Borrador.
FIELD_TO_COLUMN = {
    "supplier_name": "proveedor_nombre",
    "supplier_tax_id": "proveedor_nif",
    "supplier_address": "proveedor_direccion",
    "supplier_country": "proveedor_pais",
    "customer_tax_id": "cliente_nif",
    "invoice_number": "numero_factura",
    "invoice_date": "fecha_factura",
    "due_date": "fecha_vencimiento",
    "currency": "moneda",
    "purchase_order": "referencia_pedido",
    "net_subtotal": "subtotal_neto",
    "discount_total": "descuentos",
    "charges_total": "cargos",
    "tax_total": "total_impuestos",
    "withholding_total": "total_retenciones",
    "gross_total": "total_bruto",
    "amount_due": "importe_a_pagar",
}
COLUMN_TO_FIELD = {v: k for k, v in FIELD_TO_COLUMN.items()}


def strict_json_schema(model: type[BaseModel]) -> dict:
    """
    JSON Schema apto para el modo estricto de salidas estructuradas
    (todas las propiedades obligatorias, additionalProperties=false, sin
    "default"). Pydantic ya lo genera casi así; esto lo garantiza en todo el árbol.
    """
    schema = model.model_json_schema()

    def fix(node):
        """Recorre solo subesquemas (nunca los nombres de propiedades como si fueran palabras clave)."""
        if isinstance(node, list):
            for item in node:
                fix(item)
            return
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        node.pop("title", None)
        if "$ref" in node and len(node) > 1:
            # Algunos proveedores rechazan "$ref" con claves hermanas (p. ej. description).
            ref = node["$ref"]
            node.clear()
            node["$ref"] = ref
            return
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}).keys())
        for prop_schema in node.get("properties", {}).values():
            fix(prop_schema)
        for key in ("$defs", "definitions"):
            for sub in node.get(key, {}).values():
                fix(sub)
        for key in ("items", "anyOf", "allOf", "oneOf"):
            if key in node:
                fix(node[key])

    fix(schema)
    return schema


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict | list | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody


class DocumentOut(ApiModel):
    id: str
    empresa_id: int
    empresa: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int | None
    estado: str
    estado_label: str
    error_code: str
    error_message: str
    borrador_id: int | None
    creado: datetime
    duplicado: bool = False


class IssueOut(ApiModel):
    code: str
    severity: Literal["error", "warning", "info"]
    blocking: bool
    field: str | None
    message: str
    details: dict | None = None
    acknowledged: bool = False


class FieldEvidenceOut(ApiModel):
    state: str
    confidence: str | None
    page: int | None
    text: str | None
    extracted_value: str | None


class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    descripcion: str = Field(default="", max_length=500)
    cantidad: Decimal | None = None
    precio_unitario: Decimal | None = None
    descuento: Decimal | None = None
    tipo_impositivo: Decimal | None = None
    subtotal: Decimal | None = None
    total: Decimal | None = None


class TaxIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: TaxKind = "vat"
    porcentaje: Decimal | None = None
    base: Decimal | None = None
    cuota: Decimal | None = None


class LineOut(LineIn):
    model_config = ConfigDict(from_attributes=True)


class TaxOut(TaxIn):
    model_config = ConfigDict(from_attributes=True)


class DraftPatch(BaseModel):
    """PATCH de un borrador. Solo se cambian las claves enviadas."""

    model_config = ConfigDict(extra="forbid")
    proveedor_nombre: str | None = Field(default=None, max_length=255)
    proveedor_nif: str | None = Field(default=None, max_length=40)
    proveedor_direccion: str | None = Field(default=None, max_length=500)
    proveedor_pais: str | None = Field(default=None, max_length=2)
    numero_factura: str | None = Field(default=None, max_length=100)
    fecha_factura: date | None = None
    fecha_vencimiento: date | None = None
    moneda: str | None = Field(default=None, max_length=3)
    referencia_pedido: str | None = Field(default=None, max_length=100)
    subtotal_neto: Decimal | None = None
    descuentos: Decimal | None = None
    cargos: Decimal | None = None
    total_impuestos: Decimal | None = None
    total_retenciones: Decimal | None = None
    total_bruto: Decimal | None = None
    importe_a_pagar: Decimal | None = None
    lineas: list[LineIn] | None = Field(default=None, max_length=500)
    impuestos: list[TaxIn] | None = Field(default=None, max_length=50)
    aceptar_avisos: list[str] | None = Field(default=None, max_length=50)
    comentario: str = Field(default="", max_length=2000)

    @field_validator("moneda", "proveedor_pais")
    @classmethod
    def upper(cls, value):
        return value.upper() if isinstance(value, str) else value


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comentario: str = Field(default="", max_length=2000)


class ReprocessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    descartar_correcciones: bool = False
    comentario: str = Field(default="", max_length=2000)


class DraftOut(ApiModel):
    id: int
    estado: str
    proveedor_nombre: str
    proveedor_nif: str
    proveedor_direccion: str
    proveedor_pais: str
    numero_factura: str
    fecha_factura: date | None
    fecha_vencimiento: date | None
    moneda: str
    referencia_pedido: str
    subtotal_neto: Decimal | None
    descuentos: Decimal | None
    cargos: Decimal | None
    total_impuestos: Decimal | None
    total_retenciones: Decimal | None
    total_bruto: Decimal | None
    importe_a_pagar: Decimal | None
    lineas: list[LineOut]
    impuestos: list[TaxOut]
    incidencias: list[IssueOut]
    corregido: bool
    aprobado_en: datetime | None
    aprobado_por: str
    rechazado_en: datetime | None
    motivo_rechazo: str


class ExtractionOut(ApiModel):
    documento: DocumentOut
    extraccion_id: int | None
    modelo: str | None
    prompt_version: str | None
    schema_version: str | None
    metodo: str | None
    evidencias: dict[str, FieldEvidenceOut]
    validacion: list[IssueOut]
    borrador: DraftOut | None


class EventOut(ApiModel):
    accion: str
    campo: str
    valor_extraido: object | None
    valor_anterior: object | None
    valor_nuevo: object | None
    estado_anterior: str
    estado_nuevo: str
    comentario: str
    actor: str
    creado: datetime
