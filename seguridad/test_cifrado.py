from io import StringIO
from unittest import mock

from cryptography.fernet import Fernet
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.core.management import call_command
from django.db import connection, models
from django.test import TestCase, override_settings
from django.test.utils import isolate_apps

from seguridad import cifrado
from seguridad.cifrado import CampoCifrado

CLAVE_A = Fernet.generate_key().decode()
CLAVE_B = Fernet.generate_key().decode()


@override_settings(FIELD_ENCRYPTION_KEYS=[CLAVE_A], BLIND_INDEX_KEY="indice-de-prueba")
class FuncionesTests(TestCase):
    def test_ida_y_vuelta(self):
        valor = cifrado.cifrar("12345678Z")
        self.assertTrue(valor.startswith("enc1:"))
        self.assertNotIn("12345678Z", valor)
        self.assertEqual(cifrado.descifrar(valor), "12345678Z")

    def test_no_determinista(self):
        self.assertNotEqual(cifrado.cifrar("x"), cifrado.cifrar("x"))

    def test_texto_sin_cifrar_se_lee_tal_cual(self):
        self.assertEqual(cifrado.descifrar("antiguo"), "antiguo")

    def test_rotacion(self):
        viejo = cifrado.cifrar("dato")
        with self.settings(FIELD_ENCRYPTION_KEYS=[CLAVE_B, CLAVE_A]):
            nuevo = cifrado.recifrar(viejo)
            self.assertEqual(cifrado.descifrar(nuevo), "dato")
        with self.settings(FIELD_ENCRYPTION_KEYS=[CLAVE_B]):
            self.assertEqual(cifrado.descifrar(nuevo), "dato")
            with self.assertRaises(cifrado.ErrorCifrado):
                cifrado.descifrar(viejo)

    def test_sin_claves(self):
        with self.settings(FIELD_ENCRYPTION_KEYS=[]), self.assertRaises(ImproperlyConfigured):
            cifrado.cifrar("x")

    def test_clave_invalida(self):
        with self.settings(FIELD_ENCRYPTION_KEYS=["no-es-una-clave"]), self.assertRaises(ImproperlyConfigured):
            cifrado.cifrar("x")

    def test_indice_ciego_normaliza_y_es_estable(self):
        self.assertEqual(cifrado.indice_ciego("12345678z "), cifrado.indice_ciego("12345678Z"))
        self.assertNotEqual(cifrado.indice_ciego("12345678Z"), cifrado.indice_ciego("87654321X"))
        con_clave_a = cifrado.indice_ciego("12345678Z")
        with self.settings(BLIND_INDEX_KEY="otra"):
            self.assertNotEqual(cifrado.indice_ciego("12345678Z"), con_clave_a)


@isolate_apps("seguridad")
@override_settings(FIELD_ENCRYPTION_KEYS=[CLAVE_A])
class CampoCifradoTests(TestCase):
    """Con una tabla real en PostgreSQL: en la base de datos solo hay texto cifrado."""

    def setUp(self):
        class Ficha(models.Model):
            documento = CampoCifrado(blank=True, null=True)

            class Meta:
                app_label = "seguridad"
                db_table = "seguridad_test_ficha"

        self.Ficha = Ficha
        with connection.schema_editor() as editor:
            editor.create_model(Ficha)

    def _en_bd(self, pk):
        with connection.cursor() as c:
            c.execute("SELECT documento FROM seguridad_test_ficha WHERE id = %s", [pk])
            return c.fetchone()[0]

    def test_se_guarda_cifrado_y_se_lee_en_claro(self):
        f = self.Ficha.objects.create(documento="X1234567L")
        self.assertTrue(self._en_bd(f.pk).startswith("enc1:"))
        self.assertNotIn("X1234567L", self._en_bd(f.pk))
        self.assertEqual(self.Ficha.objects.get(pk=f.pk).documento, "X1234567L")

    def test_vacio_y_nulo_no_se_cifran(self):
        self.assertEqual(self._en_bd(self.Ficha.objects.create(documento="").pk), "")
        self.assertIsNone(self._en_bd(self.Ficha.objects.create(documento=None).pk))

    def test_filtrar_por_contenido_falla_en_vez_de_no_encontrar(self):
        with self.assertRaises(FieldError):
            list(self.Ficha.objects.filter(documento="X1234567L"))
        self.assertEqual(self.Ficha.objects.filter(documento__isnull=True).count(), 0)

    def test_rotar_cifrado_cifra_lo_antiguo_y_cambia_de_clave(self):
        with connection.cursor() as c:
            c.execute("INSERT INTO seguridad_test_ficha (documento) VALUES ('en-claro') RETURNING id")
            antiguo = c.fetchone()[0]
        cifrado_a = self.Ficha.objects.create(documento="con-clave-a")

        # El modelo de prueba no está en el registro global de apps: se le indica al comando.
        campos = [(self.Ficha, self.Ficha._meta.get_field("documento"))]
        with self.settings(FIELD_ENCRYPTION_KEYS=[CLAVE_B, CLAVE_A]), \
                mock.patch("seguridad.management.commands.rotar_cifrado.campos_cifrados", return_value=campos):
            call_command("rotar_cifrado", stdout=StringIO())

        with self.settings(FIELD_ENCRYPTION_KEYS=[CLAVE_B]):  # la clave A ya no hace falta
            self.assertEqual(self.Ficha.objects.get(pk=antiguo).documento, "en-claro")
            self.assertEqual(self.Ficha.objects.get(pk=cifrado_a.pk).documento, "con-clave-a")
        self.assertTrue(self._en_bd(antiguo).startswith("enc1:"))

    def test_check_exige_claves(self):
        from seguridad.checks import claves_de_cifrado

        campos = [(self.Ficha, self.Ficha._meta.get_field("documento"))]
        with mock.patch("seguridad.cifrado.campos_cifrados", return_value=campos):
            with self.settings(FIELD_ENCRYPTION_KEYS=[]):
                self.assertEqual([e.id for e in claves_de_cifrado(None)], ["seguridad.E001"])
            self.assertEqual(claves_de_cifrado(None), [])


class GenerarClavesTests(TestCase):
    def test_genera_clave_fernet_valida(self):
        salida = StringIO()
        call_command("rotar_cifrado", generar_clave=True, stdout=salida)
        Fernet(salida.getvalue().strip().encode())  # no lanza
