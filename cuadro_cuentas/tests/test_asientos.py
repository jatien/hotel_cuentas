from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from cuadro_cuentas.models import Asiento
from cuadro_cuentas.services import Linea, asignar_contrapartidas, crear_asiento

from .utils import D, crear_plan_minimo, crear_subcuenta_directa

FECHA = date(2026, 3, 10)


class CrearAsientoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        crear_subcuenta_directa("60000001", "Compras cocina")
        crear_subcuenta_directa("47200001", "IVA soportado 10%")
        crear_subcuenta_directa("40000001", "Proveedor prueba")
        crear_subcuenta_directa("57200001", "Banco prueba")

    def factura(self, **kwargs):
        return crear_asiento(FECHA, "Factura de prueba", [
            Linea("60000001", debe=D("100.00")),
            Linea("47200001", debe=D("10.00")),
            Linea("40000001", haber=D("110.00")),
        ], **kwargs)

    def test_factura_cuadrada_queda_contabilizada(self):
        asiento = self.factura()
        self.assertEqual(asiento.estado, Asiento.Estado.CONTABILIZADO)
        self.assertEqual(asiento.numero, 1)
        self.assertEqual(asiento.totales(), (D("110.00"), D("110.00")))
        self.assertEqual(asiento.apuntes.count(), 3)

    def test_contrapartidas_automaticas(self):
        apuntes = {a.cuenta.codigo: a for a in self.factura().apuntes.select_related("cuenta", "contrapartida")}
        self.assertEqual(apuntes["60000001"].contrapartida.codigo, "40000001")
        self.assertEqual(apuntes["47200001"].contrapartida.codigo, "40000001")
        self.assertIsNone(apuntes["40000001"].contrapartida)  # varias cuentas al otro lado

    def test_asiento_de_dos_lineas_se_cruzan_las_contrapartidas(self):
        asiento = crear_asiento(FECHA, "Pago", [
            Linea("40000001", debe=D("110")), Linea("57200001", haber=D("110"))])
        apuntes = {a.cuenta.codigo: a.contrapartida.codigo for a in asiento.apuntes.select_related("cuenta", "contrapartida")}
        self.assertEqual(apuntes, {"40000001": "57200001", "57200001": "40000001"})

    def test_conserva_concepto(self):
        apunte = self.factura().apuntes.get(cuenta__codigo="60000001")
        self.assertEqual(apunte.concepto, "Factura de prueba")

    def test_sin_contabilizar_queda_en_borrador(self):
        self.assertEqual(self.factura(contabilizar=False).estado, Asiento.Estado.BORRADOR)

    def test_origen_y_referencia(self):
        asiento = self.factura(origen=Asiento.Origen.FACTURAS, referencia_origen="factura:7")
        self.assertEqual((asiento.origen, asiento.referencia_origen), ("facturas", "factura:7"))

    def test_descuadrado_falla_y_no_guarda_nada(self):
        with self.assertRaisesMessage(ValidationError, "no cuadra"):
            crear_asiento(FECHA, "Mal", [Linea("60000001", debe=D("100")), Linea("40000001", haber=D("99"))])
        self.assertFalse(Asiento.objects.exists())

    def test_cuenta_inexistente(self):
        with self.assertRaisesMessage(ValidationError, "inexistentes"):
            crear_asiento(FECHA, "Mal", [Linea("60099999", debe=D("1")), Linea("40000001", haber=D("1"))])

    def test_cuenta_no_imputable(self):
        with self.assertRaisesMessage(ValidationError, "no imputables"):
            crear_asiento(FECHA, "Mal", [Linea("600", debe=D("1")), Linea("40000001", haber=D("1"))])

    def test_una_sola_linea(self):
        with self.assertRaisesMessage(ValidationError, "al menos dos"):
            crear_asiento(FECHA, "Mal", [Linea("60000001", debe=D("1"))])

    def test_linea_con_debe_y_haber(self):
        with self.assertRaisesMessage(ValidationError, "solo en Debe o solo en Haber"):
            crear_asiento(FECHA, "Mal", [
                Linea("60000001", debe=D("1"), haber=D("1")), Linea("40000001", haber=D("0"))])

    def test_recalcular_contrapartidas_tras_cambiar_una_cuenta(self):
        asiento = crear_asiento(FECHA, "Pago", [
            Linea("40000001", debe=D("50")), Linea("57200001", haber=D("50"))])
        pago = asiento.apuntes.get(cuenta__codigo="57200001")
        pago.cuenta = crear_subcuenta_directa("57200002", "Otro banco")
        pago.save()
        asignar_contrapartidas(asiento, recalcular=True)
        proveedor = asiento.apuntes.get(cuenta__codigo="40000001")
        self.assertEqual(proveedor.contrapartida.codigo, "57200002")