import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from cuadro_cuentas.management.commands.cargar_plan_cuentas import ARCHIVO_POR_DEFECTO
from cuadro_cuentas.models import CuentaContable
from cuadro_cuentas.tipos_alta import TIPOS_ALTA

from .utils import crear_subcuenta_directa


def cargar(*args):
    salida = StringIO()
    call_command("cargar_plan_cuentas", *args, stdout=salida)
    return salida.getvalue()


class ArchivoDelPlanTests(SimpleTestCase):
    """Comprueba el JSON sin tocar la base de datos."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.datos = json.loads(Path(ARCHIVO_POR_DEFECTO).read_text(encoding="utf-8"))
        cls.codigos = [c["codigo"] for c in cls.datos["cuentas"]]

    def test_codigos_numericos_sin_repetir(self):
        self.assertTrue(all(c.isdigit() and 1 <= len(c) <= 5 for c in self.codigos))
        self.assertEqual(len(self.codigos), len(set(self.codigos)))

    def test_cada_cuenta_tiene_su_cuenta_superior(self):
        todos = set(self.codigos)
        huerfanas = [c for c in self.codigos if len(c) > 1 and c[:-1] not in todos]
        self.assertEqual(huerfanas, [])

    def test_estan_los_9_grupos_y_las_cuentas_clave(self):
        for codigo in ["1", "9", "100", "129", "400", "410", "472", "477", "4751", "572", "600",
                       "628", "640", "642", "705", "790", "501", "502"]:
            self.assertIn(codigo, self.codigos)

    def test_todas_las_cuentas_de_3_digitos_tienen_definicion(self):
        sin = [c["codigo"] for c in self.datos["cuentas"] if len(c["codigo"]) == 3 and not c["definicion"]]
        self.assertEqual(sin, [])

    def test_las_bases_de_los_tipos_de_alta_existen_en_el_plan(self):
        todos = set(self.codigos)
        for tipo in TIPOS_ALTA:
            for base in tipo["bases"] or []:
                with self.subTest(tipo=tipo["clave"], base=base):
                    self.assertIn(base, todos)
                    self.assertIn(len(base), (3, 4, 5))


class CargarPlanCuentasTests(TestCase):
    def test_carga_completa_con_jerarquia(self):
        cargar()
        total = len(json.loads(Path(ARCHIVO_POR_DEFECTO).read_text(encoding="utf-8"))["cuentas"])
        self.assertEqual(CuentaContable.objects.count(), total)
        self.assertEqual(CuentaContable.objects.get(codigo="400").padre.codigo, "40")
        self.assertEqual(CuentaContable.objects.get(codigo="4751").padre.codigo, "475")
        self.assertFalse(CuentaContable.objects.filter(imputable=True).exists())
        self.assertIn("suministradores", CuentaContable.objects.get(codigo="400").definicion)

    def test_es_idempotente_y_no_toca_las_subcuentas(self):
        cargar()
        crear_subcuenta_directa("40000001", "Proveedor prueba")
        salida = cargar()
        self.assertIn(" 0 nuevas", salida)
        sub = CuentaContable.objects.get(codigo="40000001")
        self.assertTrue(sub.imputable)
        self.assertEqual(sub.padre.codigo, "400")

    def test_limpiar_borra_lo_que_sobra_salvo_si_tiene_subcuentas(self):
        cargar()
        CuentaContable.objects.create(codigo="999", nombre="Sobrante", imputable=False)
        CuentaContable.objects.create(codigo="998", nombre="Sobrante con hija", imputable=False)
        crear_subcuenta_directa("99800001", "Hija")
        salida = cargar("--limpiar")
        self.assertFalse(CuentaContable.objects.filter(codigo="999").exists())
        self.assertTrue(CuentaContable.objects.filter(codigo="998").exists())
        self.assertIn("Se conserva 998", salida)
class SubcuentasBasicasTests(TestCase):
    def test_crea_las_basicas_una_sola_vez(self):
        cargar()
        call_command("crear_subcuentas_basicas", stdout=StringIO())
        self.assertTrue(CuentaContable.objects.filter(codigo="47200001", nombre="IVA soportado").exists())
        self.assertTrue(CuentaContable.objects.filter(codigo="47510001").exists())
        antes = CuentaContable.objects.count()
        call_command("crear_subcuentas_basicas", stdout=StringIO())
        self.assertEqual(CuentaContable.objects.count(), antes)