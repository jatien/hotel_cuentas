"""
Tests of the extraction engine (invoice_data.services.extractor).

All data is fictional: no real supplier, CIF or invoice appears here. The
hotel's own identity is fixed by override_settings, so these tests do not
depend on the project's real settings.
"""

import json
from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from invoice_data.services.extractor import (
    ALBARAN_RE,
    Cell,
    Line,
    Region,
    Result,
    attach_descriptions,
    extract_text,
    find_header,
    find_supplier_name,
    header_from_cells,
    is_kerning_gap,
    parse_line,
    parse_simple_line,
    result_to_dict,
    review_problems,
)


@override_settings(INVOICE_OWN_NIF="B99999999", INVOICE_OWN_NAMES=("HOTEL DEMO",))
class EngineTestCase(SimpleTestCase):
    """Base class for engine tests: a fixed, fictional hotel identity regardless of real settings."""


HEADER_WITH_IVA = "Codigo Descripcion %IVA Cantidad Precio Importe"
HEADER_NO_IVA = "REFERENCIA DESCRIPCION CANTIDAD PRECIO NETO"


class RateIndependenceTests(EngineTestCase):
    """Rates come from the invoice, so none of these depends on a rate list."""

    def test_per_line_rates_not_in_any_usual_list(self):
        """Per-line IVA column with 7.5% and 16% (old Spanish rate) plus 2%: all read straight from the column."""
        text = "\n".join(
            [
                "FACTURA",
                HEADER_WITH_IVA,
                "10001 ARTICULO UNO 7,5 2,00 25,0000 50,00",
                "10002 ARTICULO DOS 16 1,00 100,0000 100,00",
                "10003 ARTICULO TRES 2 4,00 10,0000 40,00",
                # tax block: bases, cuotas, total (7.5% of 50 = 3.75 ; 16% of 100 = 16.00 ; 2% of 40 = 0.80)
                "50,00 7,5 3,75",
                "100,00 16 16,00",
                "40,00 2 0,80",
                "TOTAL 210,55",
            ]
        )
        result = extract_text(text)
        self.assertEqual(set(result.bases), {Decimal("7.5"), Decimal("16"), Decimal("2")})
        self.assertEqual(result.total, Decimal("210.55"))
        self.assertTrue(result.reconciled)

    def test_single_unusual_rate_inferred_without_iva_column(self):
        """No IVA column and an 8.5% rate: inferred from base 200.00 -> cuota 17.00 -> total 217.00."""
        text = "\n".join(
            [
                HEADER_NO_IVA,
                "2001 ARTICULO A 4,00 25,0000 100,00",
                "2002 ARTICULO B 2,00 50,0000 100,00",
                "BASE 200,00 CUOTA 17,00 TOTAL 217,00",
            ]
        )
        result = extract_text(text)
        self.assertEqual(list(result.bases), [Decimal("8.5")])
        self.assertEqual(result.total, Decimal("217.00"))
        self.assertTrue(result.reconciled)

    def test_two_rates_only_in_tax_block(self):
        """No IVA column; two rates (7.5% and 3%) exist only as a tax block. The split must be recovered."""
        text = "\n".join(
            [
                HEADER_NO_IVA,
                "3001 ARTICULO A 2,00 25,0000 50,00",
                "3002 ARTICULO B 2,00 25,0000 50,00",
                "3003 ARTICULO C 1,00 50,0000 50,00",
                "BASE 100,00 7,5 7,50",
                "BASE 50,00 3 1,50",
                "TOTAL FACTURA 159,00",
            ]
        )
        result = extract_text(text)
        self.assertEqual(set(result.bases), {Decimal("7.5"), Decimal("3")})
        self.assertEqual(result.bases[Decimal("7.5")], Decimal("100.00"))
        self.assertEqual(result.bases[Decimal("3")], Decimal("50.00"))
        self.assertTrue(result.reconciled)

    def test_unusual_rate_adds_a_note_but_is_not_rejected(self):
        """A rate outside the soft 'usual' hint (8.5%) is accepted, only annotated for confirmation."""
        text = "\n".join(
            [
                HEADER_NO_IVA,
                "2001 ARTICULO A 4,00 25,0000 100,00",
                "2002 ARTICULO B 2,00 50,0000 100,00",
                "BASE 200,00 CUOTA 17,00 TOTAL 217,00",
            ]
        )
        result = extract_text(text)
        self.assertTrue(result.reconciled)
        self.assertTrue(any("inusual" in note for note in result.notes))


class RefusesWhatDoesNotAddUpTests(EngineTestCase):
    """The safety side: bad numbers must be flagged, not trusted."""

    def test_corrupted_total_does_not_reconcile(self):
        """Same as the 8.5% invoice but the printed total is wrong (216.00): must NOT reconcile."""
        text = "\n".join(
            [
                HEADER_NO_IVA,
                "2001 ARTICULO A 4,00 25,0000 100,00",
                "2002 ARTICULO B 2,00 50,0000 100,00",
                "BASE 200,00 CUOTA 17,00 TOTAL 216,00",
            ]
        )
        result = extract_text(text)
        self.assertFalse(result.reconciled)

    def test_misread_line_amount_is_rejected(self):
        """An OCR-style digit error (qty x price != amount) drops the line instead of importing a wrong number."""
        text = "\n".join(
            [
                HEADER_NO_IVA,
                "2001 ARTICULO A 4,00 25,0000 100,00",
                "2002 ARTICULO B 2,00 50,0000 180,00",  # should be 100,00
                "BASE 200,00 CUOTA 17,00 TOTAL 217,00",
            ]
        )
        result = extract_text(text)
        self.assertEqual(len(result.lines), 1)
        self.assertFalse(result.reconciled)


def cell(text, left, bottom, right, top, page=0):
    """Shorthand for building a positioned text cell in tests."""
    return Cell(page, left, bottom, right, top, text)


class HeaderFromCellsTests(EngineTestCase):
    """Label -> value pairing by position, with type checks and page voting."""

    def test_value_below_its_label(self):
        """'FACTURA' label with the number directly under it (label above value)."""
        cells = [
            cell("FECHA", 54, 648, 82, 660),
            cell("FACTURA", 118, 648, 157, 660),
            cell("15/03/2026", 45, 630, 90, 642),
            cell("01/14372", 118, 630, 156, 642),
        ]
        result = Result()
        header_from_cells(cells, result)
        self.assertEqual(result.numero, "01/14372")
        self.assertEqual(result.fecha, "15/03/2026")

    def test_value_to_the_right_of_label(self):
        """'Fecha' label with the date on the same row to its right."""
        cells = [cell("Fecha", 40, 700, 70, 712), cell("28/02/2026", 90, 700, 140, 712)]
        result = Result()
        header_from_cells(cells, result)
        self.assertEqual(result.fecha, "28/02/2026")

    def test_type_check_skips_neighbouring_labels(self):
        """The cell right of 'FECHA' is another label; the real date is below. Types decide, not adjacency."""
        cells = [
            cell("FECHA", 50, 700, 80, 712),
            cell("FACTURA", 85, 700, 130, 712),
            cell("15/03/2026", 45, 682, 90, 694),
        ]
        result = Result()
        header_from_cells(cells, result)
        self.assertEqual(result.fecha, "15/03/2026")

    def test_albaran_date_and_due_date_are_not_the_invoice_date(self):
        """'Fecha albaran:' and 'Vencimientos' are not date labels, so their dates are never picked."""
        cells = [
            cell("Fecha albarán:", 170, 549, 233, 560),
            cell("03/03/2026", 249, 549, 294, 560),
            cell("Vencimientos:", 400, 100, 470, 112),
            cell("20/03/2026", 480, 100, 530, 112),
        ]
        result = Result()
        header_from_cells(cells, result)
        self.assertIsNone(result.fecha)

    def test_value_repeated_on_every_page_wins(self):
        """A stray candidate on one page loses to the value repeated on both pages."""
        cells = [
            cell("FACTURA", 118, 648, 157, 660, page=0),
            cell("01/14372", 118, 630, 156, 642, page=0),
            cell("FACTURA", 118, 648, 157, 660, page=1),
            cell("01/14372", 118, 630, 156, 642, page=1),
            cell("Factura Nº", 300, 300, 350, 312, page=1),
            cell("99999", 305, 282, 345, 294, page=1),
        ]
        result = Result()
        header_from_cells(cells, result)
        self.assertEqual(result.numero, "01/14372")


class AlbaranFormatTests(EngineTestCase):
    """The albaran header is written differently by each supplier; one pattern must read all seen so far."""

    def test_three_real_formats(self):
        cases = {
            "ALBARAN A1 50642 DE FECHA 12/02/26": "A1 50642",
            "Albarán '995' 03/02/2026:": "995",
            "Nº albarán: REF26/583 Fecha albarán: 03/03/2026 Ref.:": "REF26/583",
        }
        for line, expected in cases.items():
            match = ALBARAN_RE.search(line)
            self.assertIsNotNone(match, line)
            self.assertEqual(match.group(1).strip(), expected)

    def test_total_albaran_line_is_not_a_header(self):
        self.assertIsNone(ALBARAN_RE.search("Total albarán: 97,93"))


class AlbaranTotalsTests(EngineTestCase):
    """Second reconciliation level: lines per albaran must add up to the printed 'Total albaran'."""

    BASE = [
        "REFERENCIA DESCRIPCION UDS. PRECIO DTO IMPORTE",
        "Nº albarán: ALB1 Fecha albarán: 03/03/2026 Ref.:",
        "1001 ARTICULO A 2 10,00 20,00",
        "1002 ARTICULO B 1 5,00 5,00",
    ]

    def test_matching_total_is_accepted(self):
        text = "\n".join(self.BASE + ["Total albarán: 25,00", "BASE 25,00 CUOTA 5,25 TOTAL 30,25"])
        result = extract_text(text)
        self.assertEqual(result.albaran_totals["ALB1"][0], Decimal("25.00"))
        self.assertFalse(any("Albaran ALB1" in n for n in result.notes))

    def test_wrong_total_points_at_the_albaran(self):
        text = "\n".join(self.BASE + ["Total albarán: 27,00", "BASE 25,00 CUOTA 5,25 TOTAL 30,25"])
        result = extract_text(text)
        self.assertTrue(any("Albaran ALB1" in n for n in result.notes))


class DateSanityTests(EngineTestCase):
    """An invoice dated before the delivery notes it bills is suspicious."""

    def test_invoice_before_albaran_is_flagged(self):
        text = "\n".join(
            [
                "FECHA: 01/03/2026",
                "REFERENCIA DESCRIPCION UDS. PRECIO DTO IMPORTE",
                "Nº albarán: ALB1 Fecha albarán: 03/03/2026 Ref.:",
                "1001 ARTICULO A 2 10,00 20,00",
                "BASE 20,00 CUOTA 4,20 TOTAL 24,20",
            ]
        )
        result = extract_text(text)
        self.assertTrue(any("anterior a un albaran" in n for n in result.notes))


class SupplierNameTests(EngineTestCase):
    """The supplier is the legal name printed next to its CIF, never the hotel and never a second company."""

    def test_name_closest_to_cif_beats_another_company_in_the_text(self):
        """A group company is named later in the legal text, but the CIF sits beside the real name."""
        text = (
            "ZETAFOC, SL – Registro Mercantil de PROVINCIA – CIF: B-77777777\n"
            "PROTECCION DE DATOS: bajo la responsabilidad de ZETAFOC SL y OMEGA SEGUR NORTE SL por un interes\n"
        )
        self.assertEqual(find_supplier_name(text, "B77777777"), "ZETAFOC SL")

    def test_hotel_name_is_never_chosen_even_when_it_is_closest(self):
        """The customer block follows the supplier CIF immediately; it must still lose."""
        text = f"FRUTAS PEÑALBA SL\nTeléfono: 971345051\nCIF B22222226\nHOTEL DEMO S.L. Página\n"
        self.assertEqual(find_supplier_name(text, "B22222226"), "FRUTAS PEÑALBA SL")

    def test_name_does_not_swallow_the_previous_line(self):
        """An address in capitals on the line above must not become part of the name."""
        text = "07000 CIUDAD DE EJEMPLO\nFRUTAS PEÑALBA SL\nCIF B22222226"
        self.assertEqual(find_supplier_name(text, "B22222226"), "FRUTAS PEÑALBA SL")

    def test_digits_and_dots_break_the_name(self):
        """Registry data before the name ('H.AB7139') is not part of it."""
        text = "REGISTRO MERCANTIL T.159 L.158 F.85 H.AB7139 PINTURAS LUMEN S.L. - CL MAYOR,9 - B33333336"
        self.assertEqual(find_supplier_name(text, "B33333336"), "PINTURAS LUMEN S.L.")

    def test_lowercase_connectors_inside_a_name(self):
        text = "Frutas del Valle SL\nCIF B12345674"
        self.assertEqual(find_supplier_name(text, "B12345674"), "Frutas del Valle SL")

    def test_no_company_returns_none(self):
        """A freelancer's invoice (no legal-form suffix) is reported as not found, not guessed."""
        self.assertIsNone(find_supplier_name("Juan Pérez García\nNIF 12345678Z\nTotal 100,00", None))


class KerningRepairTests(EngineTestCase):
    """pdfium inserts a zero-width space inside some words ('PIN TURAS'); real word gaps are wider."""

    def test_touching_glyphs_are_a_kerning_artefact(self):
        """Measured on a real invoice PDF: T ends at 288.20, U starts at 289.35, glyph height 3.59."""
        self.assertTrue(is_kerning_gap(1.15, 3.59))

    def test_real_word_gap_is_kept(self):
        """Measured on the same PDF: S ends at 303.30, F starts at 305.88 (a real space)."""
        self.assertFalse(is_kerning_gap(2.58, 3.59))


class CsvRegressionTests(EngineTestCase):
    """Each test reproduces a failure found while testing a batch of real invoices."""

    def test_invoice_number_label_does_not_swallow_the_next_label(self):
        """Regression: the number came out as the next label because the pattern jumped over a line break."""
        result = Result()
        find_header("Nº Factura:\nFECHA\n01/03/2026", result)
        self.assertIsNone(result.numero)

    def test_nº_prefix_is_stripped_from_the_number(self):
        """Regression: the number kept its 'nº' prefix."""
        cells = [cell("Factura", 50, 700, 90, 712), cell("nº 26009", 55, 682, 100, 694)]
        result = Result()
        header_from_cells(cells, result)
        self.assertEqual(result.numero, "26009")

    def test_noise_word_before_company_name_is_dropped(self):
        """Regression: a noise word before the company name ('FACTURA Acme Systems S.A.') is dropped."""
        text = "FACTURA Acme Systems S.A.\nCIF A66666666"
        self.assertEqual(find_supplier_name(text, "A66666666"), "Acme Systems S.A.")

    def test_freelancer_nif_and_nie_are_recognised_as_supplier_id(self):
        """Regression: no supplier id on invoices from sole traders (personal NIF / NIE)."""
        result = Result()
        find_header("Juan Pérez García\nNIF 12345678Z\nCliente NIF B07794803", result)
        self.assertEqual(result.proveedor_cif, "12345678Z")
        result = Result()
        find_header("Ana Ruiz\nNIE X1234567L", result)
        self.assertEqual(result.proveedor_cif, "X1234567L")


class TotalsWithoutLinesTests(EngineTestCase):
    """Service invoices (fees, rent) have no quantity x price rows: totals come from the tax block alone."""

    def test_single_rate_service_invoice(self):
        text = "Honorarios de asesoria marzo\nBase imponible 300,00\nIVA 21% 63,00\nTOTAL 363,00"
        result = extract_text(text)
        self.assertEqual(result.total, Decimal("363.00"))
        self.assertEqual(result.bases, {Decimal("21"): Decimal("300.00")})
        self.assertTrue(result.totals_only)
        self.assertFalse(result.reconciled)  # weaker evidence than lines: never marked reconciled

    def test_two_rate_tax_block(self):
        text = "Servicios\nBase 100,00 10% 10,00\nBase 200,00 21% 42,00\nTotal factura 352,00"
        result = extract_text(text)
        self.assertEqual(result.total, Decimal("352.00"))
        self.assertEqual(set(result.bases), {Decimal("10"), Decimal("21")})

    def test_unrelated_amounts_do_not_invent_a_tax_block(self):
        text = "Concepto varios\n17,35\n42,10\n91,77"
        result = extract_text(text)
        self.assertIsNone(result.total)
        self.assertFalse(result.totals_only)


class LineLayoutTests(EngineTestCase):
    """The different column layouts suppliers use for the numbers at the end of a product row."""

    def test_five_number_layout_with_code_only_row(self):
        """Five-number layout: units, price, dto %, net price, total; description is an image so the row has only codes."""
        line = parse_line("X10020 24P 1,00 105,00 10,00 94,50 94,50", iva_column=False)
        self.assertIsNotNone(line)
        self.assertEqual((line.codigo, line.cantidad, line.precio, line.importe), ("X10020", Decimal("1.00"), Decimal("105.00"), Decimal("94.50")))

    def test_five_number_layout_with_several_units(self):
        """Net unit price x units = total, with a discount: 3 x 200 less 15% = 510 (net price 170)."""
        line = parse_line("B12345 TALADRO PERCUTOR 3,00 200,00 15,00 170,00 510,00", iva_column=False)
        self.assertEqual(line.importe, Decimal("510.00"))
        self.assertEqual(line.cantidad, Decimal("3.00"))

    def test_iva_rate_is_not_mistaken_for_units_regression(self):
        """
        Regression: '4 0,18 3,8480 0,69' also satisfies units x price x (1-dto%) = total
        (with 4 as units and 3,848 as a discount). It is really IVA 4, 0,18 units, price 3,8480.
        """
        line = parse_line("000000000069 PRODUCTO EJEMPLO KILO 4 0,18 3,8480 0,69", iva_column=True)
        self.assertEqual(line.iva, Decimal("4"))
        self.assertEqual(line.cantidad, Decimal("0.18"))

    def test_four_number_layout_with_discount_still_works(self):
        """Four-number layout: units, price, dto %, total."""
        line = parse_line("19930050115 PINTURA BLANCA 15L 2 119,43 59 97,93", iva_column=False)
        self.assertEqual(line.importe, Decimal("97.93"))

    def test_totals_row_is_not_a_product(self):
        """A row of only amounts (the totals block) has no code and no words: rejected."""
        self.assertIsNone(parse_line("127,80 127,80 26,84 154,64 €", iva_column=False))


class ImageDescriptionTests(EngineTestCase):
    """Descriptions drawn as images are attached to their row by vertical position."""

    def test_description_taken_from_the_region_containing_the_row(self):
        lines = [Line("X10020", "24P", None, Decimal(1), Decimal(105), Decimal("94.50")),
                 Line("TAP5", "24P", None, Decimal(1), Decimal(37), Decimal("33.30"))]
        cells = [cell("X10020", 48, 400, 90, 412), cell("TAP5", 48, 300, 70, 312)]
        regions = [
            Region(0, 114, 340, 335, 440, "UD. ADHESIVO PARA SUELOS MARCA X\nBALDE 20 KG.\n\nCola acrilica sin disolventes"),
            Region(0, 114, 200, 335, 330, "UD. CINTA ADHESIVA MARCA Y\n\n- Fijacion inmediata"),
        ]
        attach_descriptions(lines, cells, regions)
        self.assertEqual(lines[0].descripcion, "UD. ADHESIVO PARA SUELOS MARCA X BALDE 20 KG.")
        self.assertEqual(lines[1].descripcion, "UD. CINTA ADHESIVA MARCA Y")

    def test_existing_real_description_is_never_overwritten(self):
        lines = [Line("X10020", "TALADRO PERCUTOR", None, Decimal(1), Decimal(105), Decimal("94.50"))]
        cells = [cell("X10020", 48, 400, 90, 412)]
        regions = [Region(0, 114, 340, 335, 440, "OTRO TEXTO")]
        attach_descriptions(lines, cells, regions)
        self.assertEqual(lines[0].descripcion, "TALADRO PERCUTOR")


class CompanyNameRegressionTests(EngineTestCase):
    """Names must keep every real word, and still drop registry fragments."""

    def test_word_glued_to_a_period_is_kept(self):
        """Regression: the first word of a name was lost when a period preceded it."""
        text = "Datos: SUR.FRAU FOOD SERVICE SUR S.L. CIF B55555555"
        self.assertEqual(find_supplier_name(text, "B55555555"), "FRAU FOOD SERVICE SUR S.L.")

    def test_short_digit_in_a_name_is_kept(self):
        self.assertEqual(find_supplier_name("K7 Suelos Sur, S.L.\nCIF - B44444444", "B44444444"), "K7 Suelos Sur S.L.")

    def test_registry_fragment_with_digits_is_not_part_of_the_name(self):
        text = "REGISTRO H.AB7139 PINTURAS LUMEN S.L. - CL MAYOR,9 - B33333336"
        self.assertEqual(find_supplier_name(text, "B33333336"), "PINTURAS LUMEN S.L.")


class EmptyDiscountLayoutTests(EngineTestCase):
    def test_units_price_net_price_total_without_discount(self):
        """Row with an empty Dto. column: net price equals price."""
        line = parse_line("X10020 24P 5,00 10,00 10,00 50,00", iva_column=False)
        self.assertIsNotNone(line)
        self.assertEqual((line.cantidad, line.precio, line.importe), (Decimal("5.00"), Decimal("10.00"), Decimal("50.00")))


class TaxBlockCrossCheckTests(EngineTestCase):
    """When lines do not reconcile, the printed tax block is still reported, with the gap explained."""

    def test_missing_lines_are_reported_with_the_size_of_the_gap(self):
        """One line of 100,00 was parsed but the invoice's base is 150,00: 50,00 is missing."""
        text = "\n".join([
            "REFERENCIA DESCRIPCION UDS. PRECIO NETO",
            "2001 ARTICULO A 4,00 25,0000 100,00",
            "BASE 150,00 CUOTA 31,50 TOTAL 181,50",
        ])
        result = extract_text(text)
        self.assertFalse(result.reconciled)
        self.assertEqual(result.total, Decimal("181.50"))
        self.assertTrue(result.totals_only)
        self.assertTrue(any("+50.00" in n and "faltan lineas" in n for n in result.notes))

    def test_reconciled_invoice_gets_no_cross_check_note(self):
        text = "\n".join([
            "REFERENCIA DESCRIPCION UDS. PRECIO NETO",
            "2001 ARTICULO A 4,00 25,0000 100,00",
            "BASE 100,00 CUOTA 21,00 TOTAL 121,00",
        ])
        result = extract_text(text)
        self.assertTrue(result.reconciled)
        self.assertFalse(any("Bloque de impuestos" in n for n in result.notes))


class TextLayerBeatsOcrTests(EngineTestCase):
    """OCR text may only fill gaps; it never overrides what the text layer already says."""

    def test_date_inside_an_image_does_not_replace_the_invoice_date(self):
        """
        Regression: the invoice date flipped to a date read inside an image once OCR ran.
        The real date sits under its 'Fecha' label cell; a date inside an image must not beat it.
        """
        text = "FACTURA\nX10020 24P 1,00 105,00 10,00 94,50 94,50"
        cells = [cell("Fecha", 250, 700, 280, 712), cell("10/03/2026", 245, 682, 290, 694)]
        regions = [Region(0, 100, 300, 300, 400, "Entrega prevista Fecha: 04/03/2026\nCIF - B44444444")]
        result = extract_text(text, cells=cells, regions=regions)
        self.assertEqual(result.fecha, "10/03/2026")
        self.assertEqual(result.proveedor_cif, "B44444444")  # gap in the text layer: OCR fills it

    def test_ocr_fills_a_missing_date(self):
        text = "FACTURA\nX10020 24P 1,00 105,00 10,00 94,50 94,50"
        regions = [Region(0, 100, 300, 300, 400, "Fecha: 04/03/2026")]
        self.assertEqual(extract_text(text, regions=regions).fecha, "04/03/2026")

    def test_number_with_a_space_before_the_date_is_read_from_a_plain_line(self):
        """'G26 123 10/03/2026' (letter+digits, space, digits) is one number followed by the date."""
        result = Result()
        find_header("FACTURA\nG26 123 10/03/2026\n", result)
        self.assertEqual((result.numero, result.fecha), ("G26 123", "10/03/2026"))


def full_invoice_text() -> str:
    """A small, fully consistent invoice (fictional): two lines, one IVA rate, tax block and header."""
    return "\n".join([
        "FACTURA",
        "Nº Factura: X-2026/15   Fecha: 17/02/26",
        "Proveedor Ejemplo Norte, S.L.  CIF: B12345674",
        "REFERENCIA DESCRIPCION UDS. PRECIO NETO",
        "AB1234 TORNILLO GALVANIZADO 10,00 2,5000 25,00",
        "AB5678 TUERCA ACERO 5,00 15,0000 75,00",
        "BASE 100,00 CUOTA 21,00 TOTAL 121,00",
    ])


class JsonOutputTests(EngineTestCase):
    """The JSON is the contract with whatever consumes the extraction (schema 'factura/1')."""

    def as_json(self, result, estado="OK", motivos=None):
        data = result_to_dict(result, "ejemplo.pdf", estado, motivos or [])
        return json.loads(json.dumps(data, ensure_ascii=False))  # must survive a real JSON round trip

    def test_full_invoice_fields(self):
        data = self.as_json(extract_text(full_invoice_text()))
        self.assertEqual(data["esquema"], "factura/1")
        self.assertEqual(data["proveedor"]["cif"], "B12345674")
        self.assertEqual(data["factura"]["numero"], "X-2026/15")
        self.assertEqual(data["factura"]["fecha"], "2026-02-17")       # two-digit year -> ISO
        self.assertEqual(data["factura"]["fecha_texto"], "17/02/26")   # as printed, kept too
        self.assertEqual(len(data["lineas"]), 2)
        self.assertEqual(data["lineas"][0]["importe"], "25.00")
        self.assertEqual(data["totales"], {"total_sin_iva": "100.00", "iva": "21.00", "total_con_iva": "121.00"})
        self.assertEqual(data["impuestos"], [{"tipo": "21", "base": "100.00", "cuota": "21.00"}])
        self.assertTrue(data["verificacion"]["cuadra"])
        self.assertEqual(data["verificacion"]["metodo"], "lineas_y_bloque_impuestos")

    def test_money_is_text_not_float(self):
        """No float can slip in: amounts and rates are strings, so 0.1 + 0.2 style errors cannot occur."""
        data = self.as_json(extract_text(full_invoice_text()))
        self.assertIsInstance(data["totales"]["total_con_iva"], str)
        self.assertIsInstance(data["lineas"][0]["precio_unitario"], str)
        self.assertIsInstance(data["impuestos"][0]["tipo"], str)

    def test_rates_have_no_pointless_zeros(self):
        text = "\n".join([
            "REFERENCIA DESCRIPCION UDS. PRECIO NETO",
            "AB1234 ARTICULO A 4,00 25,0000 100,00",
            "BASE 100,00 7,5 7,50 TOTAL 107,50",
        ])
        data = self.as_json(extract_text(text))
        self.assertEqual(data["impuestos"][0]["tipo"], "7.5")

    def test_totals_only_invoice_says_how_it_was_obtained(self):
        data = self.as_json(extract_text("Honorarios marzo\nBase 300,00\nIVA 21% 63,00\nTOTAL 363,00"))
        self.assertEqual(data["verificacion"]["metodo"], "solo_bloque_impuestos")
        self.assertFalse(data["verificacion"]["cuadra"])
        self.assertEqual(data["totales"]["total_con_iva"], "363.00")

    def test_failed_file_has_exactly_the_same_shape(self):
        """A scan or corrupt file (no result) must give the same keys as a full invoice, just empty."""
        def shape(d):
            return {k: shape(v) if isinstance(v, dict) and k not in ("comprobaciones", "albaranes") else None for k, v in d.items()}
        full = self.as_json(extract_text(full_invoice_text()))
        empty = self.as_json(None, estado="OCR", motivos=["sin capa de texto"])
        self.assertEqual(shape(full), shape(empty))
        self.assertEqual(empty["lineas"], [])
        self.assertIsNone(empty["totales"]["total_con_iva"])
        self.assertEqual(empty["verificacion"]["avisos"], ["sin capa de texto"])

    def test_clean_invoice_has_no_problems(self):
        self.assertEqual(review_problems(extract_text(full_invoice_text())), [])

    def test_missing_pieces_are_listed(self):
        problems = review_problems(extract_text("solo texto sin datos de factura"))
        self.assertIn("sin numero de factura", problems)
        self.assertIn("ninguna linea reconocida", problems)


if __name__ == "__main__":
    unittest.main()


class HotelIdentityTests(EngineTestCase):
    """The hotel's own NIF and name come from settings; they must never be reported as the supplier."""

    def test_own_nif_is_never_the_supplier(self):
        result = Result()
        find_header("Cliente NIF B99999999\nSuministros Ejemplo, S.L. CIF B12345674", result)
        self.assertEqual(result.proveedor_cif, "B12345674")

    def test_own_nif_with_hyphen_is_recognised_too(self):
        result = Result()
        find_header("Cliente NIF B-99999999\nSuministros Ejemplo, S.L. CIF B12345674", result)
        self.assertEqual(result.proveedor_cif, "B12345674")

    def test_line_carrying_the_hotel_nif_is_not_a_supplier_name(self):
        text = "Datos fiscales: OTRA EMPRESA DEMO S.L. NIF B99999999\nSuministros Ejemplo, S.L. CIF B12345674"
        self.assertEqual(find_supplier_name(text, "B12345674"), "Suministros Ejemplo S.L.")

    @override_settings(INVOICE_OWN_NIF="", INVOICE_OWN_NAMES=())
    def test_unconfigured_identity_does_not_break_or_hide_the_supplier(self):
        """With no hotel identity configured nothing is excluded, and an empty NIF must not match every line."""
        text = "Suministros Ejemplo, S.L. CIF B12345674"
        self.assertEqual(find_supplier_name(text, "B12345674"), "Suministros Ejemplo S.L.")


class MultipleOwnEntitiesTests(EngineTestCase):
    """A hotel may operate under more than one legal entity (e.g. a separate C.B. billed for utilities)."""

    @override_settings(
        INVOICE_OWN_NIF=("B99999999", "E11111112"),
        INVOICE_OWN_NAMES=("HOTEL DEMO", "SOCIOS DEMO"),
    )
    def test_either_configured_nif_is_recognised_as_the_hotels_own(self):
        result = Result()
        find_header("Cliente NIF E11111112\nProveedor Ejemplo, S.L. CIF B12345674", result)
        self.assertEqual(result.proveedor_cif, "B12345674")

    @override_settings(
        INVOICE_OWN_NIF=("B99999999", "E11111112"),
        INVOICE_OWN_NAMES=("HOTEL DEMO", "SOCIOS DEMO"),
    )
    def test_either_configured_name_is_excluded_from_being_the_supplier(self):
        text = "Socios Demo, C.B.\nCIF E11111112\nSuministros Ejemplo, S.L. CIF B12345674"
        self.assertEqual(find_supplier_name(text, "B12345674"), "Suministros Ejemplo S.L.")

    def test_a_single_string_still_works_as_before(self):
        """Backward compatibility: INVOICE_OWN_NIF as a plain string (not a tuple) must still work."""
        result = Result()
        find_header("Cliente NIF B99999999\nProveedor Ejemplo, S.L. CIF B12345674", result)
        self.assertEqual(result.proveedor_cif, "B12345674")


class OcrMisreadLabelTests(EngineTestCase):
    """Regression: OCR commonly misreads 'º' as '?', and utility-bill date labels carry extra words."""

    def test_ocr_misread_degree_symbol_in_number_label(self):
        """'N? de factura: X' (OCR turned 'Nº' into 'N?')."""
        result = Result()
        find_header("N? de factura: PNR001N0284492\nFecha: 28/08/2020", result)
        self.assertEqual(result.numero, "PNR001N0284492")

    def test_date_label_with_two_extra_words(self):
        """'Fecha emisión factura: X' - a label longer than the plain 'FECHA:' case."""
        result = Result()
        find_header("Fecha emisión factura: 28/08/2020", result)
        self.assertEqual(result.fecha, "28/08/2020")

    def test_date_label_with_one_extra_word_still_works(self):
        result = Result()
        find_header("Fecha de cargo: 28/08/2020", result)
        self.assertEqual(result.fecha, "28/08/2020")

    def test_real_utility_bill_ocr_text_recovers_header_fields(self):
        """
        The actual (garbled) Tesseract output from a real scanned ENDESA gas
        bill. Only the header fields are expected to be recovered - this
        document has no product lines and no per-line IVA breakdown (see
        UtilityBillLimitationTests below), so total/lineas stay empty.
        """
        text = (
            "ya E DATOS DE LA FACTURA\n"
            "'G R G S ( ' IMPORTE FACTURA: 1.602,16 \u20ac\n"
            "ES \" y : N? de factura: PNRO01N0284492\n"
            "dd ; Referencia: 086163232988/0432\n"
            "a A Fecha emisi\u00f3n factura: 28/08/2020\n"
            "al gas E Fecha de cargo: 28/08/2020\n"
            "Endesa Energ\u00eda, S.A.U.\n"
            "CIF A81948077.\n"
        )
        result = extract_text(text)
        self.assertEqual(result.numero, "PNRO01N0284492")
        self.assertEqual(result.fecha, "28/08/2020")
        self.assertEqual(result.proveedor_nombre, "Endesa Energía S.A.U.")
        self.assertEqual(result.proveedor_cif, "A81948077")


class UtilityBillLimitationTests(EngineTestCase):
    """
    Documents actual, known behaviour: a utility-bill-shaped total
    (Fijo + Variable - Descuentos + Otros + Impuestos = Total, no base x
    rate = cuota relationship printed) is correctly left unrecovered rather
    than guessed. This is what should change if/when a dedicated
    utility-bill path is built.
    """

    def test_category_sum_total_is_not_recovered(self):
        text = (
            "Fijo 71,68 \u20ac\n"
            "Variable 1.416,99 \u20ac\n"
            "Descuentos -255,06 \u20ac\n"
            "Otros 17,67 \u20ac\n"
            "Impuestos 350,88 \u20ac\n"
            "Total 1.602,16 \u20ac\n"
        )
        result = extract_text(text)
        self.assertIsNone(result.total)
        self.assertEqual(result.lines, [])


class ThousandsSeparatorMoneyTests(EngineTestCase):
    """
    Regression: MONEY_TOKEN used to only match the tail of an amount over
    999 (e.g. 'X.234,56' -> only '234,56' was found), so any invoice
    totalling 1.000 EUR or more was invisible to totals detection.
    """

    def test_spanish_thousands_amount_is_found_whole(self):
        self.assertIn(Decimal("2266.85"), e_money_tokens("Subtotal 2.266,85 \u20ac"))

    def test_multiple_thousands_groups(self):
        self.assertIn(Decimal("12345.67"), e_money_tokens("Total 12.345,67 \u20ac"))

    def test_plain_dot_decimal_under_1000_still_works(self):
        """Same-shaped amounts under 1000 (no thousands separator) must be unaffected."""
        self.assertIn(Decimal("852.51"), e_money_tokens("TOTAL 852.51"))

    def test_plain_comma_decimal_under_1000_still_works(self):
        self.assertIn(Decimal("476.04"), e_money_tokens("21% IVA 476,04 \u20ac"))

    def test_a_reference_number_is_not_mistaken_for_an_amount(self):
        """'123.456' (three digits after the dot, no comma) is not a 2-decimal amount."""
        self.assertEqual(e_money_tokens("Referencia 123.456"), set())


class ServiceLineTests(EngineTestCase):
    """Single-charge service lines (description + one trailing amount, no quantity/unit price)."""

    def test_simple_line_is_recognised(self):
        line = parse_simple_line("Servicios mes marzo, temporada baja 2.266,85 \u20ac")
        self.assertIsNotNone(line)
        self.assertEqual(line.descripcion, "Servicios mes marzo, temporada baja")
        self.assertEqual(line.importe, Decimal("2266.85"))
        self.assertIsNone(line.cantidad)
        self.assertIsNone(line.precio)

    def test_subtotal_row_is_not_a_service_line(self):
        self.assertIsNone(parse_simple_line("Subtotal 2.266,85 \u20ac"))

    def test_total_row_is_not_a_service_line(self):
        self.assertIsNone(parse_simple_line("TOTAL 2.742,89 \u20ac"))

    def test_inline_iva_rate_row_is_not_a_service_line(self):
        self.assertIsNone(parse_simple_line("21% IVA 476,04 \u20ac"))

    def test_stopword_anywhere_in_the_description_is_caught_not_just_first_word(self):
        """Regression: 'Notas: Subtotal 2.266,85 \u20ac' (an empty notes field fused onto the
        subtotal row by the PDF's layout) was wrongly accepted when only the first word
        ('Notas') was checked; 'Subtotal' as the second word must also be caught."""
        self.assertIsNone(parse_simple_line("Notas: Subtotal 2.266,85 \u20ac"))

    def test_utility_bill_category_rows_are_not_service_lines(self):
        for row in ["Fijo 71,68 \u20ac", "Variable 1.416,99 \u20ac", "Otros 17,67 \u20ac", "Impuestos 350,88 \u20ac"]:
            self.assertIsNone(parse_simple_line(row), row)

    def test_a_bare_reference_with_no_description_is_rejected(self):
        self.assertIsNone(parse_simple_line("086163232988/0432"))

    def test_real_service_invoice_reconciles_end_to_end(self):
        """The actual (clean, digital-text) layout of a real single-charge agency invoice."""
        text = "\n".join([
            "Factura n\u00ba 9001",
            "Fecha:1/3/2026",
            "Proveedor Servicios Digitales, S.L. CIF B99988877",
            "Descripci\u00f3n Precio total",
            "Servicios mes marzo, temporada baja 2.266,85 \u20ac",
            "Notas: Subtotal 2.266,85 \u20ac",
            "21% IVA 476,04 \u20ac",
            "TOTAL 2.742,89 \u20ac",
        ])
        result = extract_text(text)
        self.assertEqual(len(result.lines), 1)
        self.assertEqual(result.lines[0].descripcion, "Servicios mes marzo, temporada baja")
        self.assertTrue(result.reconciled)
        self.assertEqual(result.total, Decimal("2742.89"))

    def test_product_invoices_are_unaffected_by_the_service_line_fallback(self):
        """A document WITH real product lines must never also pick up service-line charges."""
        text = "\n".join([
            "REFERENCIA DESCRIPCION UDS. PRECIO NETO",
            "AB1234 TORNILLO GALVANIZADO 10,00 2,5000 25,00",
            "Notas: alguna anotacion 99,00 \u20ac",  # would look like a service line on its own
            "BASE 25,00 CUOTA 5,25 TOTAL 30,25",
        ])
        result = extract_text(text)
        self.assertEqual(len(result.lines), 1)
        self.assertEqual(result.lines[0].descripcion, "TORNILLO GALVANIZADO")


def e_money_tokens(text):
    """Local alias so tests read naturally (money_tokens_in is already imported at module load via extract_text's module)."""
    from invoice_data.services.extractor import money_tokens_in
    return money_tokens_in(text)