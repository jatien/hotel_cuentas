"""
Validación determinista (Python, nunca el modelo).

validate(data, context) -> list[Issue]

Gravedad:
    error    impide aprobar hasta que se corrija el dato (no se puede "aceptar")
    warning  con review=True bloquea hasta que una persona lo acepte o lo corrija;
             con review=False es informativo
    info     solo informativo

Tolerancias explícitas: cada comparación admite TOLERANCE_MINOR_UNITS
unidades mínimas de la moneda (1 céntimo en EUR, 1 yen en JPY, 0,001 en
KWD) por cada importe redondeado que interviene en la suma.

Que los totales cuadren NO demuestra que la factura sea auténtica ni
legalmente válida; solo que es coherente consigo misma.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError

from cuadro_cuentas.nif import validar_nif
from invoice_agent.services.normalize import (
    CURRENCY_MINOR_UNITS,
    InvoiceData,
    minor_units,
)
from invoice_agent.services.tax_rules import regime_for

IMPORTANT_FIELDS = ("supplier_name", "supplier_tax_id", "invoice_number", "invoice_date", "currency", "gross_total")
REQUIRED_FIELDS = ("invoice_number", "invoice_date", "gross_total")
MAIN_TAX_KINDS = {"vat", "igic", "ipsi", "other"}
SPANISH_TAX_ID = re.compile(r"\d{8}[A-Z]|[XYZ]\d{7}[A-Z]|[A-W]\d{7}[0-9A-J]")
FIELD_LABELS = {
    "supplier_name": "nombre del proveedor",
    "supplier_tax_id": "NIF del proveedor",
    "supplier_address": "dirección del proveedor",
    "supplier_country": "país del proveedor",
    "customer_tax_id": "NIF del cliente",
    "invoice_number": "número de factura",
    "invoice_date": "fecha de factura",
    "due_date": "fecha de vencimiento",
    "currency": "moneda",
    "purchase_order": "referencia de pedido",
    "net_subtotal": "subtotal neto",
    "discount_total": "descuentos",
    "charges_total": "cargos adicionales",
    "tax_total": "total de impuestos",
    "withholding_total": "retenciones",
    "gross_total": "total de la factura",
    "amount_due": "importe a pagar",
}


@dataclass
class Issue:
    code: str
    severity: str  # error | warning | info
    message: str
    field: str | None = None
    review: bool = False
    details: dict | None = None

    @property
    def blocking(self) -> bool:
        return self.severity == "error" or (self.severity == "warning" and self.review)

    @property
    def acknowledgeable(self) -> bool:
        return self.severity == "warning" and self.review

    def to_dict(self) -> dict:
        data = asdict(self)
        data["blocking"] = self.blocking
        return data


@dataclass
class ValidationContext:
    empresa_nif: str = ""
    own_nifs: frozenset[str] = frozenset()
    duplicates: list[dict] = field(default_factory=list)
    page_problems: list[dict] = field(default_factory=list)
    today: date = field(default_factory=date.today)
    tolerance_minor_units: int = 1


def _fmt(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2f}"


class _Checker:
    def __init__(self, data: InvoiceData, ctx: ValidationContext):
        self.data, self.ctx = data, ctx
        self.issues: list[Issue] = []
        currency = data.get("currency")
        self.unit = Decimal(1).scaleb(-minor_units(currency))

    def add(self, code, severity, message, field=None, review=False, **details):
        self.issues.append(Issue(code, severity, message, field, review, details or None))

    def tol(self, terms: int = 1) -> Decimal:
        return self.unit * self.ctx.tolerance_minor_units * max(1, terms)

    def close(self, a: Decimal, b: Decimal, terms: int = 1) -> bool:
        return abs(a - b) <= self.tol(terms)

    # ------------------------------------------------------------------ #
    def run(self) -> list[Issue]:
        self.document_type()
        self.presence()
        self.formats()
        self.tax_ids()
        self.dates()
        self.lines()
        self.taxes()
        self.reconcile()
        self.duplicates()
        self.pages()
        return self.issues

    def document_type(self):
        kind = self.data.document_type
        if kind == "not_an_invoice":
            self.add("NOT_AN_INVOICE", "error", "El modelo indica que el documento no es una factura.")
        elif kind == "receipt":
            self.add("SIMPLIFIED_INVOICE", "warning", "Parece un ticket o factura simplificada.", review=True)
        elif kind == "credit_note":
            self.add("CREDIT_NOTE", "info", "Factura rectificativa / abono: los importes pueden ser negativos.")

    def presence(self):
        fields = self.data.fields
        name, tax_id = fields["supplier_name"], fields["supplier_tax_id"]
        if not name.usable and not tax_id.usable:
            self.add("SUPPLIER_MISSING", "error", "No se ha identificado al proveedor (ni nombre ni NIF).", "supplier_tax_id")
        for key in REQUIRED_FIELDS:
            fv = fields[key]
            label = FIELD_LABELS[key]
            if fv.state == "unreadable":
                self.add("FIELD_UNREADABLE", "error", f"El campo «{label}» está en el documento pero no se puede leer.", key)
            elif fv.state == "absent":
                self.add("REQUIRED_ABSENT", "error", f"El documento no muestra «{label}».", key)
            elif not fv.usable:
                self.add("REQUIRED_MISSING", "error", f"Falta «{label}» o no tiene un valor válido.", key)
        currency = fields["currency"]
        if not currency.usable:
            self.add(
                "CURRENCY_MISSING", "warning", "No se ha determinado la moneda; indícala antes de aprobar.", "currency", True
            )
        for key, fv in fields.items():
            if key in REQUIRED_FIELDS:
                continue
            if fv.state == "unreadable":
                important = key in IMPORTANT_FIELDS
                self.add(
                    "FIELD_UNREADABLE" if important else "OPTIONAL_UNREADABLE",
                    "error" if important else "warning",
                    f"El campo «{FIELD_LABELS[key]}» no se puede leer.",
                    key,
                )
            elif fv.state == "missing" and key not in ("currency",):
                self.add("FIELD_INVALID", "warning", f"El valor de «{FIELD_LABELS[key]}» no es válido.", key, True, raw=fv.raw)
        for key in IMPORTANT_FIELDS:
            fv = fields[key]
            if fv.usable and fv.confidence == "low":
                self.add(
                    "LOW_CONFIDENCE",
                    "warning",
                    f"El modelo marca «{FIELD_LABELS[key]}» con confianza baja: compruébalo en el original.",
                    key,
                    True,
                )

    def formats(self):
        currency = self.data.get("currency")
        if currency and currency not in CURRENCY_MINOR_UNITS:
            self.add("CURRENCY_UNKNOWN", "warning", f"Moneda poco habitual: {currency}.", "currency", True)
        gross = self.data.get("gross_total")
        if gross is not None and gross < 0 and self.data.document_type != "credit_note":
            self.add("NEGATIVE_TOTAL", "warning", "Total negativo en un documento que no es un abono.", "gross_total", True)

    def tax_ids(self):
        supplier = self.data.get("supplier_tax_id")
        country = self.data.get("supplier_country")
        if supplier:
            if supplier in self.ctx.own_nifs:
                self.add(
                    "SUPPLIER_IS_OWN_COMPANY",
                    "error",
                    "El NIF del proveedor es el de una de nuestras empresas (¿proveedor y cliente intercambiados?).",
                    "supplier_tax_id",
                )
            else:
                # validar_nif comprueba el dígito de control de NIF/NIE/CIF españoles y
                # solo el formato de un NIF-IVA de otro país de la UE.
                try:
                    validar_nif(supplier)
                except ValidationError as error:
                    self.add("TAX_ID_INVALID", "error", error.messages[0], "supplier_tax_id")
                else:
                    if not SPANISH_TAX_ID.fullmatch(supplier):
                        self.add(
                            "TAX_ID_UNVERIFIED",
                            "info",
                            "NIF extranjero: solo se ha comprobado el formato.",
                            "supplier_tax_id",
                        )
                if country and country != "ES" and SPANISH_TAX_ID.fullmatch(supplier):
                    self.add(
                        "COUNTRY_TAX_ID_MISMATCH",
                        "warning",
                        f"NIF español con país {country}.",
                        "supplier_country",
                        True,
                    )
        customer = self.data.get("customer_tax_id")
        empresa = self.ctx.empresa_nif
        if customer and empresa and customer != empresa:
            if customer in self.ctx.own_nifs:
                self.add(
                    "CUSTOMER_OTHER_COMPANY",
                    "warning",
                    f"La factura va a nombre de otra empresa del grupo ({customer}).",
                    "customer_tax_id",
                    True,
                )
            else:
                self.add(
                    "CUSTOMER_MISMATCH",
                    "warning",
                    f"El NIF del cliente ({customer}) no coincide con el de la empresa ({empresa}).",
                    "customer_tax_id",
                    True,
                )

    def dates(self):
        issued, due = self.data.get("invoice_date"), self.data.get("due_date")
        today = self.ctx.today
        if issued:
            if issued > today + timedelta(days=1):
                self.add("FUTURE_DATE", "warning", "La fecha de factura es posterior a hoy.", "invoice_date", True)
            elif issued < today - timedelta(days=365 * 6):
                self.add("OLD_DATE", "warning", "La factura tiene más de seis años.", "invoice_date", True)
        if issued and due and due < issued:
            self.add("DUE_BEFORE_ISSUE", "warning", "El vencimiento es anterior a la fecha de factura.", "due_date", True)

    def lines(self):
        for index, line in enumerate(self.data.lines, start=1):
            if line.unreadable:
                self.add(
                    "LINE_UNREADABLE",
                    "warning",
                    f"Línea {index}: no se pueden leer {', '.join(line.unreadable)}.",
                    review=True,
                    line=index,
                )
            if line.quantity is not None and line.unit_price is not None and line.subtotal is not None:
                expected = line.quantity * line.unit_price - (line.discount or 0)
                if not self.close(expected, line.subtotal):
                    self.add(
                        "LINE_ARITHMETIC",
                        "warning",
                        f"Línea {index}: cantidad × precio − descuento = {_fmt(expected)}, pero el importe es {_fmt(line.subtotal)}.",
                        review=True,
                        line=index,
                    )
            if line.subtotal is not None and line.tax_rate is not None and line.total is not None:
                expected = line.subtotal * (1 + line.tax_rate / 100)
                if not self.close(expected, line.total):
                    self.add(
                        "LINE_TOTAL_MISMATCH",
                        "warning",
                        f"Línea {index}: el total con impuestos no corresponde al tipo {line.tax_rate} %.",
                        review=True,
                        line=index,
                    )
        subtotals = [line.subtotal for line in self.data.lines]
        net = self.data.get("net_subtotal")
        if subtotals and None not in subtotals and net is not None:
            total = sum(subtotals, Decimal(0))
            if not self.close(total, net, len(subtotals)):
                self.add(
                    "LINES_SUM_MISMATCH",
                    "warning",
                    f"La suma de las líneas ({_fmt(total)}) no coincide con el subtotal ({_fmt(net)}).",
                    "net_subtotal",
                    True,
                )
        breakdown_rates = {t.rate for t in self.data.taxes if t.kind in MAIN_TAX_KINDS and t.rate is not None}
        line_rates = {line.tax_rate for line in self.data.lines if line.tax_rate is not None}
        if breakdown_rates and line_rates - breakdown_rates:
            self.add(
                "LINE_RATE_NOT_IN_BREAKDOWN",
                "warning",
                "Hay líneas con un tipo impositivo que no aparece en el desglose de impuestos.",
            )

    def taxes(self):
        regime = regime_for(self.data.get("supplier_country"))
        per_line_rounding = self.unit * Decimal(len(self.data.lines)) / 2
        for index, row in enumerate(self.data.taxes, start=1):
            if row.rate is not None and row.base is not None and row.amount is not None:
                expected = row.base * row.rate / 100
                if abs(expected - row.amount) > self.tol() + per_line_rounding:
                    self.add(
                        "TAX_ARITHMETIC",
                        "error",
                        f"Impuesto {index}: {row.rate} % de {_fmt(row.base)} es {_fmt(expected)}, no {_fmt(row.amount)}.",
                        "tax_total",
                        tax=index,
                    )
            if row.rate is not None and regime.rate_is_known(row.kind, row.rate) is False:
                self.add(
                    "TAX_RATE_UNUSUAL",
                    "warning",
                    f"Tipo de {row.kind} poco habitual en {regime.code}: {row.rate} %.",
                    "tax_total",
                    True,
                    regime=regime.code,
                )
            if row.amount is None or row.base is None:
                self.add("TAX_ROW_INCOMPLETE", "warning", f"Impuesto {index}: falta la base o la cuota.", "tax_total", True)
        tax_rows = [t for t in self.data.taxes if t.kind != "withholding" and t.amount is not None]
        tax_total = self.data.get("tax_total")
        if tax_rows and tax_total is not None:
            total = sum((t.amount for t in tax_rows), Decimal(0))
            if not self.close(total, tax_total, len(tax_rows)):
                self.add(
                    "TAX_TOTAL_MISMATCH",
                    "error",
                    f"La suma de cuotas ({_fmt(total)}) no coincide con el total de impuestos ({_fmt(tax_total)}).",
                    "tax_total",
                )
        withholding_rows = [t for t in self.data.taxes if t.kind == "withholding" and t.amount is not None]
        withholding = self.data.get("withholding_total")
        if withholding_rows and withholding is not None:
            total = sum((abs(t.amount) for t in withholding_rows), Decimal(0))
            if not self.close(total, abs(withholding), len(withholding_rows)):
                self.add(
                    "WITHHOLDING_MISMATCH",
                    "error",
                    f"La suma de retenciones ({_fmt(total)}) no coincide con el total retenido ({_fmt(withholding)}).",
                    "withholding_total",
                )

    def reconcile(self):
        get = self.data.get
        gross = get("gross_total")
        net, discount, charges = get("net_subtotal"), get("discount_total") or Decimal(0), get("charges_total") or Decimal(0)
        main = [t for t in self.data.taxes if t.kind in MAIN_TAX_KINDS]
        bases = [t.base for t in main]
        amounts = [t.amount for t in self.data.taxes if t.kind != "withholding"]
        breakdown_ok = bool(main) and None not in bases and None not in amounts

        if breakdown_ok and net is not None:
            expected_base = net - abs(discount) + charges
            base_sum = sum(bases, Decimal(0))
            if not self.close(expected_base, base_sum, len(bases) + 2):
                self.add(
                    "BASE_MISMATCH",
                    "warning",
                    f"Subtotal − descuentos + cargos = {_fmt(expected_base)}, pero la suma de bases es {_fmt(base_sum)}.",
                    "net_subtotal",
                    True,
                )

        checked = False
        if gross is not None:
            if breakdown_ok:
                expected = sum(bases, Decimal(0)) + sum(amounts, Decimal(0))
                terms = len(bases) + len(amounts)
                checked = True
            elif net is not None and get("tax_total") is not None:
                expected = net - abs(discount) + charges + get("tax_total")
                terms = 4
                checked = True
            if checked and not self.close(expected, gross, terms):
                self.add(
                    "TOTAL_MISMATCH",
                    "error",
                    f"Bases + impuestos = {_fmt(expected)}, pero el total de la factura es {_fmt(gross)}.",
                    "gross_total",
                    expected=str(expected),
                )
            if not checked:
                self.add(
                    "TOTAL_UNVERIFIED",
                    "warning",
                    "No hay desglose suficiente para comprobar el total: revísalo con el original.",
                    "gross_total",
                    True,
                )

        due = get("amount_due")
        if gross is not None and due is not None:
            withholding = abs(get("withholding_total") or Decimal(0))
            if not self.close(gross - withholding, due, 2):
                self.add(
                    "AMOUNT_DUE_MISMATCH",
                    "warning",
                    f"Total − retenciones = {_fmt(gross - withholding)}, pero el importe a pagar es {_fmt(due)} "
                    "(¿anticipos o pagos a cuenta?).",
                    "amount_due",
                    True,
                )

    def duplicates(self):
        for dup in self.ctx.duplicates:
            kind = dup.get("kind")
            if kind == "business_key" and dup.get("approved"):
                self.add(
                    "DUPLICATE_APPROVED",
                    "error",
                    "Ya hay una factura APROBADA con el mismo proveedor y número.",
                    "invoice_number",
                    documento=dup.get("documento"),
                )
            elif kind == "business_key":
                self.add(
                    "DUPLICATE_SUSPECTED",
                    "warning",
                    "Hay otro documento con el mismo proveedor y número de factura.",
                    "invoice_number",
                    True,
                    documento=dup.get("documento"),
                )
            elif kind == "same_amount_date":
                self.add(
                    "POSSIBLE_DUPLICATE",
                    "warning",
                    "Hay otra factura del mismo proveedor con la misma fecha y el mismo total.",
                    "gross_total",
                    True,
                    documento=dup.get("documento"),
                )
            elif kind == "legacy":
                self.add(
                    "LEGACY_MATCH",
                    "info",
                    "Esta factura ya figura en Gestión de Facturas (app invoice_data).",
                    "invoice_number",
                    documento=dup.get("documento"),
                )

    def pages(self):
        for problem in self.ctx.page_problems:
            self.add(
                "PAGE_QUALITY",
                "warning",
                f"Página {problem.get('page')}: {problem.get('message')}",
                review=True,
                page=problem.get("page"),
            )


def validate(data: InvoiceData, ctx: ValidationContext) -> list[Issue]:
    return _Checker(data, ctx).run()


def pending_blockers(issues: list[dict], acknowledged: list[str]) -> list[dict]:
    """Incidencias que aún impiden aprobar (las de revisión aceptadas ya no cuentan)."""
    accepted = set(acknowledged)
    return [
        issue
        for issue in issues
        if issue["severity"] == "error" or (issue["blocking"] and issue["code"] not in accepted)
    ]
