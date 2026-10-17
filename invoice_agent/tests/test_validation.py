"""Normalización y validación determinista (sin base de datos ni modelo)."""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from invoice_agent.schemas import ExtractionOutput
from invoice_agent.services.normalize import (
    from_model_output,
    normalize_currency,
    normalize_invoice_number,
    parse_amount,
    parse_date,
)
from invoice_agent.services.validation import (
    ValidationContext,
    pending_blockers,
    validate,
)
from invoice_agent.tests.helpers import (
    EMPRESA_NIF,
    OTRA_EMPRESA_NIF,
    SUPPLIER_NIF,
    field,
    model_output,
)


def data_for(**overrides):
    return from_model_output(ExtractionOutput.model_validate(model_output(**overrides)))


def codes(issues):
    return {i.code for i in issues}


CTX = ValidationContext(
    empresa_nif=EMPRESA_NIF,
    own_nifs=frozenset({EMPRESA_NIF, OTRA_EMPRESA_NIF}),
    today=date(2026, 10, 1),
)


class ParseTests(SimpleTestCase):
    def test_amount_formats(self):
        self.assertEqual(parse_amount("1234.56"), Decimal("1234.56"))
        self.assertEqual(parse_amount("1.234,56"), Decimal("1234.56"))
        self.assertEqual(parse_amount("1,234.56"), Decimal("1234.56"))
        self.assertEqual(parse_amount("12,5 €"), Decimal("12.5"))
        self.assertEqual(parse_amount("(12,50)"), Decimal("-12.50"))
        self.assertEqual(parse_amount("12,50-"), Decimal("-12.50"))
        self.assertEqual(parse_amount("-3.10"), Decimal("-3.10"))

    def test_amount_never_guesses(self):
        for value in (None, "", "abc", "—", "1e400"):
            self.assertIsNone(parse_amount(value), value)

    def test_dates(self):
        self.assertEqual(parse_date("2026-09-15"), date(2026, 9, 15))
        self.assertEqual(parse_date("15/09/2026"), date(2026, 9, 15))
        self.assertEqual(parse_date("15-09-26"), date(2026, 9, 15))
        self.assertIsNone(parse_date("31/02/2026"))
        self.assertIsNone(parse_date("mañana"))

    def test_currency_and_numbers(self):
        self.assertEqual(normalize_currency("€"), "EUR")
        self.assertEqual(normalize_currency("eur"), "EUR")
        self.assertIsNone(normalize_currency("$"))  # ambiguo: no se adivina
        self.assertEqual(normalize_invoice_number("F-2026/0042"), normalize_invoice_number("f 2026 0042"))


class FieldStateTests(SimpleTestCase):
    def test_present_without_usable_value_becomes_missing(self):
        data = data_for(invoice_date=field("no se ve"))
        self.assertEqual(data.fields["invoice_date"].state, "missing")
        self.assertIsNone(data.get("invoice_date"))

    def test_absent_unreadable_and_missing_are_distinct(self):
        data = data_for(
            due_date=field(None),
            invoice_number=field("F-20?6", status="unreadable", confidence="low"),
            gross_total=field("ciento veintiuno"),
        )
        self.assertEqual(data.fields["due_date"].state, "absent")
        self.assertEqual(data.fields["invoice_number"].state, "unreadable")
        self.assertEqual(data.fields["gross_total"].state, "missing")
        found = codes(validate(data, CTX))
        self.assertIn("FIELD_UNREADABLE", found)
        self.assertIn("REQUIRED_MISSING", found)
        self.assertNotIn("REQUIRED_ABSENT", found)

    def test_absent_required_field(self):
        found = codes(validate(data_for(invoice_number=field(None)), CTX))
        self.assertIn("REQUIRED_ABSENT", found)

    def test_absent_with_value_is_kept_but_low_confidence(self):
        data = data_for(purchase_order={**field("PO-1"), "status": "absent"})
        self.assertEqual(data.fields["purchase_order"].state, "present")
        self.assertEqual(data.fields["purchase_order"].confidence, "low")


class ValidationTests(SimpleTestCase):
    def test_consistent_invoice_has_no_blockers(self):
        issues = [i.to_dict() for i in validate(data_for(), CTX)]
        self.assertEqual(pending_blockers(issues, []), [])

    def test_incorrect_total_is_an_error(self):
        found = codes(validate(data_for(gross_total=field("125.00")), CTX))
        self.assertIn("TOTAL_MISMATCH", found)

    def test_tax_arithmetic(self):
        rows = [{"kind": "vat", "rate": "21", "base": "100.00", "amount": "20.00", "page": 1, "source_text": None}]
        found = codes(validate(data_for(tax_breakdown=rows, tax_total=field("20.00"), gross_total=field("120.00")), CTX))
        self.assertIn("TAX_ARITHMETIC", found)

    def test_multiple_vat_rates(self):
        rows = [
            {"kind": "vat", "rate": "21", "base": "100.00", "amount": "21.00", "page": 1, "source_text": None},
            {"kind": "vat", "rate": "10", "base": "50.00", "amount": "5.00", "page": 1, "source_text": None},
            {"kind": "vat", "rate": "4", "base": "25.00", "amount": "1.00", "page": 1, "source_text": None},
        ]
        data = data_for(
            line_items=[], tax_breakdown=rows, net_subtotal=field("175.00"), tax_total=field("27.00"),
            gross_total=field("202.00"),
        )
        issues = validate(data, CTX)
        self.assertEqual([i for i in issues if i.severity == "error"], [])

    def test_discount_and_withholding(self):
        rows = [
            {"kind": "vat", "rate": "21", "base": "900.00", "amount": "189.00", "page": 1, "source_text": None},
            {"kind": "withholding", "rate": "15", "base": "900.00", "amount": "135.00", "page": 1, "source_text": None},
        ]
        data = data_for(
            line_items=[],
            tax_breakdown=rows,
            net_subtotal=field("1000.00"),
            discount_total=field("100.00"),
            tax_total=field("189.00"),
            withholding_total=field("135.00"),
            gross_total=field("1089.00"),
            amount_due=field("954.00"),
        )
        issues = validate(data, CTX)
        self.assertEqual([i.code for i in issues if i.blocking], [])

    def test_amount_due_mismatch_needs_review(self):
        data = data_for(amount_due=field("100.00"))
        issue = next(i for i in validate(data, CTX) if i.code == "AMOUNT_DUE_MISMATCH")
        self.assertTrue(issue.blocking and issue.acknowledgeable)

    def test_rounding_tolerance_depends_on_currency(self):
        # 1 céntimo de diferencia en EUR se tolera; en JPY la unidad mínima es 1 yen.
        ok = data_for(gross_total=field("121.01"))
        self.assertNotIn("TOTAL_MISMATCH", codes(validate(ok, CTX)))
        too_far = data_for(gross_total=field("121.04"))
        self.assertIn("TOTAL_MISMATCH", codes(validate(too_far, CTX)))
        rows = [{"kind": "vat", "rate": "10", "base": "1000", "amount": "100", "page": 1, "source_text": None}]
        jpy = data_for(
            currency=field("JPY"), line_items=[], tax_breakdown=rows, net_subtotal=field("1000"),
            tax_total=field("100"), gross_total=field("1101"), supplier_country=field("JP"),
            supplier_tax_id=field("JP1234567890123"),
        )
        self.assertNotIn("TOTAL_MISMATCH", codes(validate(jpy, CTX)))

    def test_line_arithmetic(self):
        lines = model_output()["line_items"]
        lines[0]["line_subtotal"] = "85.00"
        found = codes(validate(data_for(line_items=lines), CTX))
        self.assertIn("LINE_ARITHMETIC", found)
        self.assertIn("LINES_SUM_MISMATCH", found)

    def test_invalid_spanish_tax_id(self):
        found = codes(validate(data_for(supplier_tax_id=field("B12345670")), CTX))
        self.assertIn("TAX_ID_INVALID", found)

    def test_foreign_tax_id_only_format(self):
        data = data_for(supplier_tax_id=field("DE123456789"), supplier_country=field("DE"))
        found = codes(validate(data, CTX))
        self.assertIn("TAX_ID_UNVERIFIED", found)
        self.assertNotIn("TAX_ID_INVALID", found)

    def test_supplier_and_customer_swapped(self):
        found = codes(validate(data_for(supplier_tax_id=field(EMPRESA_NIF)), CTX))
        self.assertIn("SUPPLIER_IS_OWN_COMPANY", found)

    def test_invoice_addressed_to_other_group_company(self):
        found = codes(validate(data_for(customer_tax_id=field(OTRA_EMPRESA_NIF)), CTX))
        self.assertIn("CUSTOMER_OTHER_COMPANY", found)

    def test_unusual_rate_is_only_a_warning_and_jurisdiction_aware(self):
        rows = [{"kind": "vat", "rate": "19", "base": "100.00", "amount": "19.00", "page": 1, "source_text": None}]
        spanish = data_for(tax_breakdown=rows, tax_total=field("19.00"), gross_total=field("119.00"))
        issue = next(i for i in validate(spanish, CTX) if i.code == "TAX_RATE_UNUSUAL")
        self.assertEqual(issue.severity, "warning")
        german = data_for(
            tax_breakdown=rows, tax_total=field("19.00"), gross_total=field("119.00"),
            supplier_country=field("DE"), supplier_tax_id=field("DE123456789"),
        )
        self.assertNotIn("TAX_RATE_UNUSUAL", codes(validate(german, CTX)))

    def test_canary_igic(self):
        rows = [{"kind": "igic", "rate": "7", "base": "100.00", "amount": "7.00", "page": 1, "source_text": None}]
        data = data_for(tax_breakdown=rows, tax_total=field("7.00"), gross_total=field("107.00"), line_items=[])
        self.assertNotIn("TAX_RATE_UNUSUAL", codes(validate(data, CTX)))

    def test_dates(self):
        found = codes(validate(data_for(due_date=field("2026-09-01")), CTX))
        self.assertIn("DUE_BEFORE_ISSUE", found)
        found = codes(validate(data_for(invoice_date=field("2027-01-01")), CTX))
        self.assertIn("FUTURE_DATE", found)

    def test_low_confidence_important_field_requires_review(self):
        issue = next(
            i for i in validate(data_for(gross_total=field("121.00", confidence="low")), CTX) if i.code == "LOW_CONFIDENCE"
        )
        self.assertTrue(issue.acknowledgeable)

    def test_not_an_invoice(self):
        self.assertIn("NOT_AN_INVOICE", codes(validate(data_for(document_type="not_an_invoice"), CTX)))

    def test_duplicates_from_context(self):
        ctx = ValidationContext(
            empresa_nif=EMPRESA_NIF,
            duplicates=[{"kind": "business_key", "documento": "x", "approved": False}],
            today=date(2026, 10, 1),
        )
        issue = next(i for i in validate(data_for(), ctx) if i.code == "DUPLICATE_SUSPECTED")
        self.assertTrue(issue.acknowledgeable)
        ctx.duplicates = [{"kind": "business_key", "documento": "x", "approved": True}]
        issue = next(i for i in validate(data_for(), ctx) if i.code == "DUPLICATE_APPROVED")
        self.assertEqual(issue.severity, "error")

    def test_acknowledged_warnings_stop_blocking_errors_do_not(self):
        issues = [i.to_dict() for i in validate(data_for(amount_due=field("1.00"), gross_total=field("130")), CTX)]
        remaining = {i["code"] for i in pending_blockers(issues, ["AMOUNT_DUE_MISMATCH", "TOTAL_MISMATCH"])}
        self.assertIn("TOTAL_MISMATCH", remaining)
        self.assertNotIn("AMOUNT_DUE_MISMATCH", remaining)

    def test_supplier_identified_by_name_only_is_enough(self):
        data = data_for(supplier_tax_id=field(None))
        self.assertNotIn("SUPPLIER_MISSING", codes(validate(data, CTX)))
        data = data_for(supplier_tax_id=field(None), supplier_name=field(None))
        self.assertIn("SUPPLIER_MISSING", codes(validate(data, CTX)))

    def test_supplier_nif_constant_is_valid(self):
        self.assertNotIn("TAX_ID_INVALID", codes(validate(data_for(supplier_tax_id=field(SUPPLIER_NIF)), CTX)))
