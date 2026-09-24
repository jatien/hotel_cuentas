from django.test import TestCase

from invoice_data.services.invoice2data_extraction import get_templates


class TemplateLoadingTests(TestCase):
    """Confirms our custom invoice2data templates load without errors."""

    def test_apagafoc_template_loads(self):
        templates = get_templates()
        issuers = [t.get("issuer") for t in templates]
        self.assertIn("APAGAFOC SL", issuers)

    def test_apagafoc_keyword_matches_expected_text(self):
        templates = get_templates()
        apagafoc_template = next(t for t in templates if t.get("issuer") == "APAGAFOC SL")
        sample_text = "APAGAFOC, SL - Registro Mercantil\nFACTURA\nNUMERO: F1-078435"
        self.assertTrue(apagafoc_template.matches_input(sample_text))

    def test_apagafoc_keyword_does_not_match_unrelated_text(self):
        templates = get_templates()
        apagafoc_template = next(t for t in templates if t.get("issuer") == "APAGAFOC SL")
        unrelated_text = "FACTURA\nNUMERO: 12345\nProveedor: Otro Suministrador SL"
        self.assertFalse(apagafoc_template.matches_input(unrelated_text))