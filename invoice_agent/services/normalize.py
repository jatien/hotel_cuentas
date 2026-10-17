"""
Normalización: de la salida del modelo (texto) a valores tipados (Decimal,
date, códigos ISO), y de un Borrador a la misma estructura, para que la
validación determinista trabaje siempre sobre InvoiceData.

Estados de un campo:
    present     leído del documento con un valor utilizable
    absent      el documento no tiene ese dato (según el modelo)
    unreadable  el dato está pero no se puede leer
    missing     el modelo dijo "present" pero no dio un valor utilizable
                (vacío, formato imposible...), o la persona lo dejó vacío
Nunca se inventa un valor: si no se puede interpretar, queda en None.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from cuadro_cuentas.nif import normalizar_nif
from invoice_agent.schemas import (
    AMOUNT_FIELDS,
    COLUMN_TO_FIELD,
    DATE_FIELDS,
    HEADER_FIELDS,
    ExtractionOutput,
)

# Moneda ISO 4217 -> decimales (unidad mínima). Lista de monedas habituales; una
# moneda de tres letras que no esté aquí se acepta con aviso y 2 decimales.
CURRENCY_MINOR_UNITS = {
    "EUR": 2, "USD": 2, "GBP": 2, "CHF": 2, "SEK": 2, "NOK": 2, "DKK": 2, "PLN": 2, "CZK": 2,
    "HUF": 2, "RON": 2, "BGN": 2, "CAD": 2, "AUD": 2, "NZD": 2, "MXN": 2, "BRL": 2, "ARS": 2,
    "CLP": 0, "COP": 2, "PEN": 2, "UYU": 2, "MAD": 2, "TRY": 2, "CNY": 2, "HKD": 2, "SGD": 2,
    "INR": 2, "ZAR": 2, "AED": 2, "SAR": 2, "ILS": 2, "JPY": 0, "KRW": 0, "ISK": 0,
    "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "TND": 3,
}
CURRENCY_ALIASES = {
    "€": "EUR", "EURO": "EUR", "EUROS": "EUR", "EUR.": "EUR",
    "£": "GBP", "LIBRAS": "GBP", "US$": "USD", "USD$": "USD", "DOLARES": "USD", "DÓLARES": "USD",
    "¥": "JPY", "CHF.": "CHF",
}
COUNTRY_ALIASES = {
    "ESPAÑA": "ES", "ESPANA": "ES", "SPAIN": "ES", "FRANCIA": "FR", "FRANCE": "FR", "PORTUGAL": "PT",
    "ALEMANIA": "DE", "GERMANY": "DE", "ITALIA": "IT", "ITALY": "IT", "REINO UNIDO": "GB",
    "UNITED KINGDOM": "GB", "IRLANDA": "IE", "IRELAND": "IE", "PAISES BAJOS": "NL", "NETHERLANDS": "NL",
}
TEXT_LIMITS = {
    "supplier_name": 255, "supplier_tax_id": 40, "supplier_address": 500, "customer_tax_id": 40,
    "invoice_number": 100, "purchase_order": 100,
}


@dataclass
class FieldValue:
    state: str
    value: object = None
    confidence: str | None = None
    page: int | None = None
    text: str | None = None
    raw: str | None = None

    @property
    def usable(self) -> bool:
        return self.state == "present" and self.value is not None


@dataclass
class Line:
    description: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    discount: Decimal | None = None
    tax_rate: Decimal | None = None
    subtotal: Decimal | None = None
    total: Decimal | None = None
    page: int | None = None
    unreadable: list[str] = field(default_factory=list)


@dataclass
class TaxRow:
    kind: str = "vat"
    rate: Decimal | None = None
    base: Decimal | None = None
    amount: Decimal | None = None


@dataclass
class InvoiceData:
    fields: dict[str, FieldValue]
    lines: list[Line] = field(default_factory=list)
    taxes: list[TaxRow] = field(default_factory=list)
    document_type: str = "invoice"
    notes: list[str] = field(default_factory=list)

    def get(self, name: str):
        """Valor utilizable de un campo, o None."""
        fv = self.fields.get(name)
        return fv.value if fv is not None and fv.usable else None


# --------------------------------------------------------------------------- #
# Conversores individuales (puros)
# --------------------------------------------------------------------------- #
_AMOUNT_CLEAN = re.compile(r"[^\d,.\-()]")
_CURRENCY_TOKENS = re.compile(r"(?i)[€$£¥]|\b(?:eur|euros?|usd|gbp|chf|jpy)\b")


def parse_amount(text) -> Decimal | None:
    """
    Texto -> Decimal, admitiendo "1234.56", "1.234,56", "1,234.56", "12,5 €",
    "(12,50)" (negativo). None si no se puede interpretar sin adivinar.
    """
    if text is None:
        return None
    if isinstance(text, Decimal):
        return text
    raw = str(text).strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    without_currency = _CURRENCY_TOKENS.sub("", raw)
    if re.search(r"[^\W\d_]", without_currency):  # quedan letras: "1e400", "doce", "l2,50"... no se adivina
        return None
    cleaned = _AMOUNT_CLEAN.sub("", without_currency).strip("()")
    if cleaned.endswith("-"):  # "12,50-" (formato de algunos ERP)
        negative, cleaned = True, cleaned[:-1]
    if cleaned.startswith("-"):
        negative, cleaned = True, cleaned[1:]
    if not cleaned or not re.fullmatch(r"[\d.,]+", cleaned) or not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        decimal_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        cleaned = cleaned.replace(thousands, "").replace(decimal_sep, ".")
    elif cleaned.count(",") > 1:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if abs(value) >= Decimal("1e11"):  # no cabe en NUMERIC(14,2): basura de lectura
        return None
    return -value if negative else value


def parse_date(text) -> date | None:
    """Acepta AAAA-MM-DD (lo que se pide al modelo) y DD/MM/AAAA o DD-MM-AAAA (formato español)."""
    if text is None:
        return None
    if isinstance(text, date):
        return text
    raw = str(text).strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", raw)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def normalize_currency(text) -> str | None:
    if text is None:
        return None
    raw = str(text).strip().upper()
    if not raw:
        return None
    raw = CURRENCY_ALIASES.get(raw, raw)
    return raw if re.fullmatch(r"[A-Z]{3}", raw) else None


def normalize_country(text) -> str | None:
    if text is None:
        return None
    raw = str(text).strip().upper()
    raw = COUNTRY_ALIASES.get(raw, raw)
    return raw if re.fullmatch(r"[A-Z]{2}", raw) else None


def normalize_tax_id(text) -> str:
    return normalizar_nif(str(text or ""))


def normalize_invoice_number(text) -> str:
    """Clave de negocio: solo letras y números, en mayúsculas (A-2026/15 == A2026/15)."""
    folded = unicodedata.normalize("NFKD", str(text or "")).upper()
    return re.sub(r"[^0-9A-Z]", "", folded)[:100]


def clean_text(text, limit: int) -> str | None:
    if text is None:
        return None
    value = " ".join(str(text).split())
    return value[:limit] or None


def minor_units(currency: str | None) -> int:
    return CURRENCY_MINOR_UNITS.get(currency or "", 2)


# --------------------------------------------------------------------------- #
# Desde la salida del modelo
# --------------------------------------------------------------------------- #
def _convert(name: str, value):
    if name in AMOUNT_FIELDS:
        return parse_amount(value)
    if name in DATE_FIELDS:
        return parse_date(value)
    if name == "currency":
        return normalize_currency(value)
    if name == "supplier_country":
        return normalize_country(value)
    if name in ("supplier_tax_id", "customer_tax_id"):
        return normalize_tax_id(value) or None
    return clean_text(value, TEXT_LIMITS.get(name, 255))


def from_model_output(output: ExtractionOutput) -> InvoiceData:
    fields = {}
    for name in HEADER_FIELDS:
        extracted = getattr(output, name)
        raw = extracted.value
        converted = _convert(name, raw)
        state = extracted.status
        confidence = extracted.confidence
        if state == "present" and converted is None:
            state = "missing"
        elif state == "absent" and converted is not None:
            state, confidence = "present", "low"  # el modelo se contradice: se conserva el valor, pero dudoso
        fields[name] = FieldValue(
            state=state,
            value=converted if state in ("present", "unreadable") else None,
            confidence=confidence,
            page=extracted.evidence.page,
            text=clean_text(extracted.evidence.text, 300),
            raw=clean_text(raw, 300),
        )
    lines = [
        Line(
            description=clean_text(item.description, 500) or "",
            quantity=parse_amount(item.quantity),
            unit_price=parse_amount(item.unit_price),
            discount=parse_amount(item.discount),
            tax_rate=parse_amount(item.tax_rate),
            subtotal=parse_amount(item.line_subtotal),
            total=parse_amount(item.line_total),
            page=item.page,
            unreadable=list(item.unreadable_fields),
        )
        for item in output.line_items
    ]
    taxes = [
        TaxRow(kind=row.kind, rate=parse_amount(row.rate), base=parse_amount(row.base), amount=parse_amount(row.amount))
        for row in output.tax_breakdown
    ]
    return InvoiceData(
        fields=fields, lines=lines, taxes=taxes, document_type=output.document_type, notes=list(output.notes)
    )


# --------------------------------------------------------------------------- #
# Desde un borrador (valores revisados por una persona)
# --------------------------------------------------------------------------- #
def from_draft(borrador, evidencias: dict | None = None) -> InvoiceData:
    """
    Estructura validable a partir de un Borrador. Un campo con valor cuenta
    como "present"; uno vacío conserva el estado extraído si era
    absent/unreadable (sigue sin estar en el documento) y si no, "missing".
    """
    evidencias = evidencias or {}
    fields = {}
    for column, name in COLUMN_TO_FIELD.items():
        if column == "cliente_nif":
            value = (borrador.extraccion.cliente_nif if borrador.extraccion else "") or None
        else:
            value = getattr(borrador, column)
        if value in ("", None):
            original = evidencias.get(name, {}).get("state")
            state = original if original in ("absent", "unreadable") else "missing"
            fields[name] = FieldValue(state=state, value=None)
        else:
            if name in ("supplier_tax_id", "customer_tax_id"):
                value = normalize_tax_id(value)
            fields[name] = FieldValue(state="present", value=value, confidence="high")
    lines = [
        Line(
            description=line.descripcion,
            quantity=line.cantidad,
            unit_price=line.precio_unitario,
            discount=line.descuento,
            tax_rate=line.tipo_impositivo,
            subtotal=line.subtotal,
            total=line.total,
        )
        for line in borrador.lineas.all()
    ]
    taxes = [TaxRow(kind=t.tipo, rate=t.porcentaje, base=t.base, amount=t.cuota) for t in borrador.impuestos.all()]
    return InvoiceData(fields=fields, lines=lines, taxes=taxes)
