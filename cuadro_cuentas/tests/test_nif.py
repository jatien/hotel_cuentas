from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from cuadro_cuentas.nif import normalizar_nif, validar_nif


class NormalizarNifTests(SimpleTestCase):
    def test_quita_espacios_guiones_puntos_y_pasa_a_mayusculas(self):
        self.assertEqual(normalizar_nif(" a-12.345 674 "), "A12345674")

    def test_quita_prefijo_es_del_nif_iva(self):
        self.assertEqual(normalizar_nif("ESA12345674"), "A12345674")
        self.assertEqual(normalizar_nif("ES A12345674"), "A12345674")

    def test_vacio(self):
        self.assertEqual(normalizar_nif(""), "")
        self.assertEqual(normalizar_nif(None), "")


class ValidarNifTests(SimpleTestCase):
    def test_vacio_devuelve_cadena_vacia(self):
        self.assertEqual(validar_nif(""), "")

    def test_dni_correcto(self):
        self.assertEqual(validar_nif("12345678z"), "12345678Z")

    def test_dni_con_letra_incorrecta(self):
        with self.assertRaisesMessage(ValidationError, "letra del NIF"):
            validar_nif("12345678A")

    def test_nie_correcto_e_incorrecto(self):
        self.assertEqual(validar_nif("X1234567L"), "X1234567L")
        with self.assertRaisesMessage(ValidationError, "letra del NIE"):
            validar_nif("X1234567A")

    def test_cif_de_sociedad_anonima_exige_digito(self):
        self.assertEqual(validar_nif("A12345674"), "A12345674")
        with self.assertRaises(ValidationError):
            validar_nif("A1234567D")  # la A solo admite dígito de control

    def test_cif_de_organismo_exige_letra(self):
        self.assertEqual(validar_nif("Q1234567D"), "Q1234567D")
        with self.assertRaises(ValidationError):
            validar_nif("Q12345674")  # la Q solo admite letra de control

    def test_cif_que_admite_letra_o_digito(self):
        self.assertEqual(validar_nif("G12345674"), "G12345674")
        self.assertEqual(validar_nif("G1234567D"), "G1234567D")

    def test_cif_con_control_incorrecto(self):
        with self.assertRaisesMessage(ValidationError, "dígito de control"):
            validar_nif("B12345675")

    def test_nif_iva_de_otro_pais_se_acepta(self):
        self.assertEqual(validar_nif("de123456789"), "DE123456789")
        self.assertEqual(validar_nif("IE6388047V"), "IE6388047V")

    def test_texto_sin_formato_de_nif(self):
        with self.assertRaisesMessage(ValidationError, "no parece un NIF"):
            validar_nif("1234")