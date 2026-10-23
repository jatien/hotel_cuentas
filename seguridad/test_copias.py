import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from pyrage import x25519

from seguridad import copias
from seguridad.models import CopiaSeguridad


def _pg_dump_disponible():
    try:
        copias._ejecutable("pg_dump")
        copias._ejecutable("pg_restore")
        return True
    except ImproperlyConfigured:
        return False


class CopiasBase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.clave = x25519.Identity.generate()
        (self.tmp / "clave.txt").write_text(f"# privada\n{self.clave}\n", encoding="utf-8")
        media = self.tmp / "media" / "facturas"
        media.mkdir(parents=True)
        (media / "factura.pdf").write_bytes(b"%PDF-1.4 secreto-de-prueba")
        privada = self.tmp / "private_media"
        privada.mkdir()
        (privada / "original.pdf").write_bytes(b"%PDF-1.4 otro-secreto")
        ajustes = self.settings(
            BACKUP_DIR=str(self.tmp / "copias"), BACKUP_AGE_RECIPIENTS=[str(self.clave.to_public())],
            MEDIA_ROOT=self.tmp / "media",
            INVOICE_AGENT={**settings.INVOICE_AGENT, "PRIVATE_STORAGE_ROOT": str(privada)},
        )
        ajustes.enable()
        self.addCleanup(ajustes.disable)


class FicherosTests(CopiasBase):
    def test_copia_cifrada_y_verificable(self):
        salida = copias.copia_ficheros()
        r = salida.registro
        self.assertEqual(r.estado, CopiaSeguridad.Estado.OK, r.error)
        contenido = salida.ruta.read_bytes()
        self.assertTrue(contenido.startswith(b"age-encryption.org/v1"))
        self.assertNotIn(b"secreto-de-prueba", contenido)
        self.assertEqual(len(r.sha256), 64)
        self.assertFalse(list(Path(settings.BACKUP_DIR).glob("*.partial")))

        resultado = copias.verificar(salida.ruta, self.tmp / "clave.txt")
        self.assertEqual(resultado["ficheros"], 2)
        r.refresh_from_db()
        self.assertIsNotNone(r.verificada)

    def test_clave_equivocada(self):
        salida = copias.copia_ficheros()
        otra = self.tmp / "otra.txt"
        otra.write_text(str(x25519.Identity.generate()))
        with self.assertRaises(Exception):
            copias.verificar(salida.ruta, otra)

    def test_sin_destinatarios(self):
        with self.settings(BACKUP_AGE_RECIPIENTS=[]), self.assertRaises(ImproperlyConfigured):
            copias.copia_ficheros()


@skipUnless(_pg_dump_disponible(), "pg_dump/pg_restore no están instalados")
class BaseDatosTests(CopiasBase):
    def test_volcado_cifrado_y_restaurable(self):
        salida = copias.copia_base_datos()
        self.assertEqual(salida.registro.estado, CopiaSeguridad.Estado.OK, salida.registro.error)
        self.assertNotIn(b"seguridad_copiaseguridad", salida.ruta.read_bytes())
        self.assertGreater(copias.verificar(salida.ruta, self.tmp / "clave.txt")["objetos"], 10)

    def test_fallo_de_pg_dump_queda_registrado(self):
        with self.settings(DATABASES={"default": {**settings.DATABASES["default"], "NAME": "no_existe_xyz"}}):
            salida = copias.copia_base_datos()
        self.assertEqual(salida.registro.estado, CopiaSeguridad.Estado.ERROR)
        self.assertIn("no_existe_xyz", salida.registro.error)
        self.assertFalse(list(Path(settings.BACKUP_DIR).glob("*.age*")))

    def test_comando(self):
        salida = StringIO()
        call_command("copia_seguridad", stdout=salida)
        self.assertEqual(CopiaSeguridad.objects.filter(estado="ok").count(), 2)
        self.assertIn("OK", salida.getvalue())


class PodaTests(CopiasBase):
    def test_borra_antiguas_pero_conserva_las_ultimas(self):
        import os

        destino = copias.directorio()
        hace = (timezone.now() - timedelta(days=60)).timestamp()
        for i in range(5):
            f = destino / f"cuentas-2026010{i}-030000.dump.age"
            f.write_bytes(b"x")
            os.utime(f, (hace, hace))
        borradas = copias.podar(dias=30, minimo=3)
        self.assertEqual(sorted(borradas), ["cuentas-20260100-030000.dump.age", "cuentas-20260101-030000.dump.age"])
        self.assertEqual(len(list(destino.glob("*.age"))), 3)


class ComandoTests(CopiasBase):
    def test_generar_clave(self):
        salida = StringIO()
        call_command("copia_seguridad", generar_clave=True, stdout=salida)
        texto = salida.getvalue()
        self.assertIn("BACKUP_AGE_RECIPIENTS=age1", texto)
        self.assertIn("AGE-SECRET-KEY-", texto)

    def test_fallo_termina_con_error(self):
        with mock.patch.object(copias, "copia_base_datos") as bd:
            bd.return_value = copias._Salida(
                CopiaSeguridad.objects.create(tipo="bd", estado="error", error="boom"), None)
            with self.assertRaises(SystemExit) as salida:
                call_command("copia_seguridad", solo_bd=True, stdout=StringIO(), stderr=StringIO())
        self.assertEqual(salida.exception.code, 1)


class ChecksTests(TestCase):
    def test_avisos_de_despliegue(self):
        from seguridad.checks import base_de_datos, copias as check_copias

        bd = {**settings.DATABASES["default"], "HOST": "192.168.1.30", "USER": "postgres", "OPTIONS": {}}
        with self.settings(DATABASES={"default": bd}):
            self.assertEqual({a.id for a in base_de_datos(None)}, {"seguridad.W001", "seguridad.W002"})
        bd.update({"USER": "cuentas_app", "OPTIONS": {"sslmode": "verify-full"}})
        with self.settings(DATABASES={"default": bd}):
            self.assertEqual(base_de_datos(None), [])
        with self.settings(BACKUP_AGE_RECIPIENTS=[]):
            self.assertEqual([a.id for a in check_copias(None)], ["seguridad.W003"])
