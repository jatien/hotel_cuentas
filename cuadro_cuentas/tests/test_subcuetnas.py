from django.core.exceptions import ValidationError
from django.test import TestCase

from cuadro_cuentas.models import CuentaContable
from cuadro_cuentas.services import buscar_subcuenta_por_nif, crear_subcuenta, siguiente_codigo

from .utils import CIF_ACREEDOR, CIF_PROVEEDOR, crear_plan_minimo, crear_subcuenta_directa


def cuenta(codigo):
    return CuentaContable.objects.get(codigo=codigo)


class SiguienteCodigoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()

    def test_primer_codigo_de_cada_longitud_de_base(self):
        self.assertEqual(siguiente_codigo(cuenta("400")), "40000001")
        self.assertEqual(siguiente_codigo(cuenta("4000")), "40000001")
        self.assertEqual(siguiente_codigo(cuenta("76200")), "76200001")

    def test_sigue_al_ultimo_existente(self):
        crear_subcuenta_directa("40000001", "Uno")
        crear_subcuenta_directa("40000007", "Siete")
        self.assertEqual(siguiente_codigo(cuenta("400")), "40000008")

    def test_tras_el_ultimo_numero_posible_busca_huecos(self):
        crear_subcuenta_directa("40099999", "Último posible")
        self.assertEqual(siguiente_codigo(cuenta("400")), "40000001")

    def test_no_mezcla_cuentas_distintas(self):
        crear_subcuenta_directa("41000003", "Acreedor")
        self.assertEqual(siguiente_codigo(cuenta("400")), "40000001")


class CrearSubcuentaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()

    def test_crea_subcuenta_imputable_colgando_de_la_base(self):
        sub = crear_subcuenta("400", "  Frutas   Prueba  SL ", nif="a-12345674")
        self.assertEqual(sub.codigo, "40000001")
        self.assertEqual(sub.padre.codigo, "400")
        self.assertTrue(sub.imputable)
        self.assertEqual(sub.nombre, "Frutas Prueba SL")
        self.assertEqual(sub.nif, CIF_PROVEEDOR)

    def test_altas_seguidas_numeran_correlativo(self):
        codigos = [crear_subcuenta("572", f"Banco {i}").codigo for i in range(3)]
        self.assertEqual(codigos, ["57200001", "57200002", "57200003"])

    def test_subcuenta_de_cuenta_de_4_digitos_cuelga_de_ella(self):
        crear_subcuenta("400", "Por la 400")  # ocupa 40000001
        sub = crear_subcuenta("4000", "Por la 4000")
        self.assertEqual(sub.codigo, "40000002")
        self.assertEqual(sub.padre.codigo, "4000")

    def test_nif_repetido_en_la_misma_base_falla(self):
        crear_subcuenta("400", "Primero", nif=CIF_PROVEEDOR)
        with self.assertRaisesMessage(ValidationError, "Ya existe"):
            crear_subcuenta("400", "Duplicado", nif=f"ES{CIF_PROVEEDOR}")

    def test_mismo_nif_en_otra_base_se_permite(self):
        crear_subcuenta("400", "Como proveedor", nif=CIF_PROVEEDOR)
        sub = crear_subcuenta("410", "Como acreedor", nif=CIF_PROVEEDOR)
        self.assertEqual(sub.codigo, "41000001")

    def test_nif_incorrecto_falla(self):
        with self.assertRaises(ValidationError):
            crear_subcuenta("400", "Mal", nif="A12345675")

    def test_codigo_manual_correcto(self):
        self.assertEqual(crear_subcuenta("400", "Manual", codigo="40012345").codigo, "40012345")

    def test_codigo_manual_con_otro_prefijo_falla(self):
        with self.assertRaisesMessage(ValidationError, "empezar por 400"):
            crear_subcuenta("400", "Manual", codigo="41012345")

    def test_codigo_manual_con_longitud_incorrecta_falla(self):
        with self.assertRaisesMessage(ValidationError, "8 dígitos"):
            crear_subcuenta("400", "Manual", codigo="4001234")

    def test_codigo_manual_repetido_falla(self):
        crear_subcuenta("400", "Primero", codigo="40012345")
        with self.assertRaises(ValidationError):
            crear_subcuenta("400", "Segundo", codigo="40012345")

    def test_bases_no_validas(self):
        crear_subcuenta_directa("40000001", "Subcuenta existente")
        for base in ("40", "4", "999", "40000001"):
            with self.subTest(base=base), self.assertRaisesMessage(ValidationError, "3, 4 o 5 dígitos"):
                crear_subcuenta(base, "No vale")

    def test_nombre_vacio_falla(self):
        with self.assertRaises(ValidationError):
            crear_subcuenta("400", "   ")


class BuscarPorNifTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        cls.proveedor = crear_subcuenta("400", "Proveedor", nif=CIF_PROVEEDOR)
        cls.acreedor = crear_subcuenta("410", "Acreedor", nif=CIF_ACREEDOR)

    def test_encuentra_aunque_el_nif_venga_con_otro_formato(self):
        self.assertEqual(buscar_subcuenta_por_nif("es a-12345674", ["400", "410"]), self.proveedor)

    def test_solo_busca_en_las_bases_indicadas(self):
        self.assertIsNone(buscar_subcuenta_por_nif(CIF_ACREEDOR, ["400"]))
        self.assertEqual(buscar_subcuenta_por_nif(CIF_ACREEDOR, ["400", "410"]), self.acreedor)

    def test_sin_nif_devuelve_none(self):
        self.assertIsNone(buscar_subcuenta_por_nif("", ["400"]))