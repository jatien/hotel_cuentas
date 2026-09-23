from django.test import TestCase

from invoice_data.services.line_items import GENERIC_LINE_PATTERN, _interpret_numbers


class GenericLinePatternTests(TestCase):
    """Confirms the single generic pattern handles both real invoice layouts seen so far."""

    def test_matches_line_without_per_line_iva(self):
        text = "11001011 MANO DE OBRA OFICIAL 1ª TA03 1.50 40.0000 60.00"
        matches = list(GENERIC_LINE_PATTERN.finditer(text))
        self.assertEqual(len(matches), 1)
        fields = _interpret_numbers(matches[0].group("numbers"))
        self.assertIsNone(fields["iva"])
        self.assertEqual(fields["cantidad"], "1.50")
        self.assertEqual(fields["precio"], "40.0000")
        self.assertEqual(fields["importe"], "60.00")

    def test_matches_line_with_per_line_iva(self):
        text = "000000099099 CHALOTA BANANA KILO 4 0,45 2,1000 0,95"
        matches = list(GENERIC_LINE_PATTERN.finditer(text))
        self.assertEqual(len(matches), 1)
        fields = _interpret_numbers(matches[0].group("numbers"))
        self.assertEqual(fields["iva"], "4")
        self.assertEqual(fields["cantidad"], "0,45")
        self.assertEqual(fields["precio"], "2,1000")
        self.assertEqual(fields["importe"], "0,95")

    def test_non_product_divider_line_is_not_matched(self):
        text = "Albaran '1162' 07/02/2026:"
        matches = list(GENERIC_LINE_PATTERN.finditer(text))
        self.assertEqual(matches, [])