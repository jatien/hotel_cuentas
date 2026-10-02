from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from cuadro_cuentas.models import Apunte, Asiento, CuentaContable, Ejercicio

from .utils import D, crear_plan_minimo, crear_subcuenta_directa, ejercicio


class CuentaContableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()

    def test_padre_se_asigna_por_el_prefijo_mas_largo(self):
        self.assertEqual(CuentaContable.objects.get(codigo="400").padre.codigo, "40")
        self.assertEqual(CuentaContable.objects.get(codigo="4000").padre.codigo, "400")
        self.assertIsNone(CuentaContable.objects.get(codigo="4").padre)

    def test_subcuenta_sin_padre_cuelga_de_la_cuenta_mas_concreta(self):
        # sin padre explícito (por ejemplo, creada desde el admin) se elige el prefijo más largo
        sub = CuentaContable.objects.create(codigo="40000009", nombre="Desde el admin", imputable=True)
        self.assertEqual(sub.padre.codigo, "4000")

    def test_nivel_y_es_subcuenta(self):
        self.assertEqual(CuentaContable.objects.get(codigo="6").nivel, "Grupo")
        self.assertEqual(CuentaContable.objects.get(codigo="62").nivel, "Subgrupo")
        self.assertEqual(CuentaContable.objects.get(codigo="76200").nivel, "Cuenta")
        sub = crear_subcuenta_directa("62800001", "Electricidad")
        self.assertEqual(sub.nivel, "Subcuenta")
        self.assertTrue(sub.es_subcuenta)

    def test_clean_rechaza_codigos_no_numericos(self):
        with self.assertRaises(ValidationError):
            CuentaContable(codigo="40A", nombre="X").full_clean()

    def test_clean_rechaza_longitudes_no_previstas(self):
        with self.assertRaises(ValidationError):
            CuentaContable(codigo="400001", nombre="Seis dígitos").full_clean()

    def test_clean_rechaza_padre_incoherente(self):
        cuenta = CuentaContable(codigo="41000001", nombre="X",
                                padre=CuentaContable.objects.get(codigo="400"))
        with self.assertRaises(ValidationError):
            cuenta.full_clean()

    def test_heredado_sube_hasta_la_cuenta_con_dato(self):
        sub = crear_subcuenta_directa("40000001", "Frutas Prueba")
        origen, texto = sub.heredado("definicion")
        self.assertEqual(origen.codigo, "400")
        self.assertIn("suministradores de mercancías", texto)

    def test_heredado_sin_dato_en_ninguna_cuenta(self):
        origen, texto = CuentaContable.objects.get(codigo="705").heredado("definicion")
        self.assertIsNone(origen)
        self.assertEqual(texto, "")


class EjercicioYAsientoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        cls.compras = crear_subcuenta_directa("60000001", "Compras cocina")
        cls.proveedor = crear_subcuenta_directa("40000001", "Proveedor prueba")

    def test_para_fecha_crea_el_año_natural_y_lo_reutiliza(self):
        e1 = Ejercicio.para_fecha(date(2026, 5, 3))
        e2 = Ejercicio.para_fecha(date(2026, 11, 30))
        self.assertEqual(e1, e2)
        self.assertEqual((e1.fecha_inicio, e1.fecha_fin), (date(2026, 1, 1), date(2026, 12, 31)))

    def test_numeracion_correlativa_por_ejercicio(self):
        a1 = Asiento.objects.create(fecha=date(2026, 1, 5), concepto="Uno")
        a2 = Asiento.objects.create(fecha=date(2026, 2, 5), concepto="Dos")
        b1 = Asiento.objects.create(fecha=date(2027, 1, 5), concepto="Otro año")
        self.assertEqual((a1.numero, a2.numero, b1.numero), (1, 2, 1))

    def test_no_se_valida_un_asiento_de_ejercicio_cerrado(self):
        ejercicio(2025, cerrado=True)
        with self.assertRaisesMessage(ValidationError, "cerrado"):
            Asiento(fecha=date(2025, 6, 1), concepto="Tarde").full_clean(exclude=["ejercicio", "numero"])

    def _asiento(self, *lineas):
        asiento = Asiento.objects.create(fecha=date(2026, 3, 1), concepto="Prueba")
        for cuenta, debe, haber in lineas:
            Apunte.objects.create(asiento=asiento, cuenta=cuenta, debe=D(debe), haber=D(haber))
        return asiento

    def test_cuadrado_y_contabilizar(self):
        asiento = self._asiento((self.compras, "100", "0"), (self.proveedor, "0", "100"))
        self.assertTrue(asiento.cuadrado)
        asiento.contabilizar()
        self.assertEqual(asiento.estado, Asiento.Estado.CONTABILIZADO)

    def test_contabilizar_descuadrado_falla(self):
        asiento = self._asiento((self.compras, "100", "0"), (self.proveedor, "0", "99"))
        with self.assertRaisesMessage(ValidationError, "no cuadra"):
            asiento.contabilizar()

    def test_contabilizar_con_una_sola_linea_falla(self):
        asiento = self._asiento((self.compras, "100", "0"))
        with self.assertRaisesMessage(ValidationError, "al menos dos"):
            asiento.contabilizar()


class ApunteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        cls.compras = crear_subcuenta_directa("60000001", "Compras cocina")
        cls.asiento = Asiento.objects.create(fecha=date(2026, 3, 1), concepto="Prueba")

    def test_debe_y_haber_a_la_vez_no_es_valido(self):
        apunte = Apunte(asiento=self.asiento, cuenta=self.compras, debe=D("1"), haber=D("1"))
        with self.assertRaises(ValidationError):
            apunte.full_clean(validate_constraints=False)

    def test_sin_importe_no_es_valido(self):
        apunte = Apunte(asiento=self.asiento, cuenta=self.compras)
        with self.assertRaises(ValidationError):
            apunte.full_clean(validate_constraints=False)

    def test_cuenta_no_imputable_no_es_valida(self):
        apunte = Apunte(asiento=self.asiento, cuenta=CuentaContable.objects.get(codigo="600"), debe=D("1"))
        with self.assertRaisesMessage(ValidationError, "no admite apuntes"):
            apunte.full_clean(validate_constraints=False)

    def test_la_base_de_datos_impide_debe_y_haber_a_la_vez(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Apunte.objects.create(asiento=self.asiento, cuenta=self.compras, debe=D("1"), haber=D("1"))