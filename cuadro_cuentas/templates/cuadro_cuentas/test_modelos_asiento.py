import json
from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from cuadro_cuentas.modelos_asiento import crear_desde_modelo, previsualizar
from cuadro_cuentas.models import Asiento
from cuadro_cuentas.services import saldos_subcuentas

from .utils import D, crear_plan_minimo, crear_subcuenta_directa

SUBCUENTAS = [
    ("40000001", "Frutas Prueba SL", None),
    ("41000001", "Asesoría Prueba", None),
    ("60000001", "Compras alimentos", None),
    ("62300001", "Asesoría fiscal", None),
    ("62800001", "Electricidad", None),
    ("47200001", "IVA soportado", None),
    ("47700001", "IVA repercutido", None),
    ("47510001", "Retenciones IRPF", "4751"),
    ("47500001", "IVA a pagar", "4750"),
    ("47000001", "IVA a compensar", "4700"),
    ("43000001", "Agencia Prueba", None),
    ("70500001", "Alojamiento", None),
    ("57200001", "Banco Prueba", None),
    ("57000001", "Caja recepción", None),
    ("64000001", "Sueldos", None),
    ("64200001", "Seguridad Social empresa", None),
    ("47600001", "Seguridad Social", None),
    ("46500001", "Nóminas pendientes", None),
    ("12900001", "Resultado del ejercicio", None),
]


def lineas(asiento):
    """{cuenta: (debe, haber)} de un asiento guardado."""
    return {a.cuenta.codigo: (a.debe, a.haber) for a in asiento.apuntes.select_related("cuenta")}


class BaseModelos(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        for codigo, nombre, base in SUBCUENTAS:
            crear_subcuenta_directa(codigo, nombre, base=base)

    def compra(self, fecha=date(2026, 3, 2), base="100", iva="10", documento="F-1"):
        return crear_desde_modelo("factura_proveedor", fecha, {
            "proveedor": "40000001", "compra": "60000001", "base": base, "iva": iva,
            "cuenta_iva": "47200001", "departamento": "cocina"}, documento=documento)

    def venta(self, fecha=date(2026, 4, 1), base="1000"):
        return crear_desde_modelo("factura_cliente", fecha, {
            "cliente": "43000001", "ingreso": "70500001", "base": base, "iva": "10",
            "cuenta_iva": "47700001"})


class OperacionesTests(BaseModelos):
    def test_factura_de_proveedor_con_contrapartida_automatica(self):
        asiento = self.compra()
        self.assertEqual(lineas(asiento), {
            "60000001": (D("100.00"), 0), "47200001": (D("10.00"), 0), "40000001": (0, D("110.00"))})
        self.assertEqual(asiento.estado, Asiento.Estado.CONTABILIZADO)
        self.assertEqual((asiento.tipo, asiento.modelo, asiento.clase), ("operacion", "factura_proveedor", "Compuesto"))
        self.assertEqual(asiento.concepto, "Factura Frutas Prueba SL F-1")
        self.assertEqual(asiento.referencia_origen, "F-1")
        self.assertEqual(asiento.apuntes.get(cuenta__codigo="60000001").departamento, "cocina")
        self.assertEqual(asiento.apuntes.get(cuenta__codigo="60000001").contrapartida.codigo, "40000001")

    def test_factura_de_servicios_con_retencion(self):
        asiento = crear_desde_modelo("factura_acreedor", date(2026, 3, 5), {
            "acreedor": "41000001", "gasto": "62300001", "base": "1.000,00", "iva": "21",
            "cuenta_iva": "47200001", "retencion": "15", "cuenta_retencion": "47510001"})
        self.assertEqual(lineas(asiento), {
            "62300001": (D("1000.00"), 0), "47200001": (D("210.00"), 0),
            "47510001": (0, D("150.00")), "41000001": (0, D("1060.00"))})

    def test_retencion_sin_cuenta_de_retenciones_falla(self):
        with self.assertRaisesMessage(ValidationError, "cuenta de retenciones"):
            crear_desde_modelo("factura_acreedor", date(2026, 3, 5), {
                "acreedor": "41000001", "gasto": "62300001", "base": "100", "iva": "21",
                "cuenta_iva": "47200001", "retencion": "15"})

    def test_factura_sin_iva_es_asiento_simple(self):
        asiento = self.compra(iva="0")
        self.assertEqual(asiento.clase, "Simple")
        self.assertEqual(lineas(asiento), {"60000001": (D("100.00"), 0), "40000001": (0, D("100.00"))})

    def test_venta_al_contado_separa_base_e_iva(self):
        asiento = crear_desde_modelo("venta_contado", date(2026, 3, 6), {
            "tesoreria": "57000001", "ingreso": "70500001", "total": "100", "iva": "21",
            "cuenta_iva": "47700001"})
        self.assertEqual(lineas(asiento), {
            "57000001": (D("100.00"), 0), "70500001": (0, D("82.64")), "47700001": (0, D("17.36"))})

    def test_nomina(self):
        asiento = crear_desde_modelo("nomina", date(2026, 3, 31), {
            "bruto": "2000", "ss_empresa": "600", "irpf": "200", "ss_trabajador": "130",
            "sueldos": "64000001", "ss_cargo": "64200001", "cuenta_irpf": "47510001",
            "cuenta_ss": "47600001", "pendiente": "46500001"})
        self.assertEqual(lineas(asiento), {
            "64000001": (D("2000.00"), 0), "64200001": (D("600.00"), 0), "47510001": (0, D("200.00")),
            "47600001": (0, D("730.00")), "46500001": (0, D("1670.00"))})

    def test_nomina_con_retenciones_mayores_que_el_bruto_falla(self):
        with self.assertRaisesMessage(ValidationError, "mayores que el sueldo bruto"):
            crear_desde_modelo("nomina", date(2026, 3, 31), {
                "bruto": "100", "ss_empresa": "30", "irpf": "100", "sueldos": "64000001",
                "ss_cargo": "64200001", "cuenta_irpf": "47510001", "cuenta_ss": "47600001",
                "pendiente": "46500001"})

    def test_pago_y_cobro_son_asientos_simples(self):
        pago = crear_desde_modelo("pago", date(2026, 3, 10), {
            "tercero": "40000001", "tesoreria": "57200001", "importe": "110"})
        cobro = crear_desde_modelo("cobro", date(2026, 3, 11), {
            "tesoreria": "57200001", "cliente": "43000001", "importe": "50"})
        self.assertEqual(lineas(pago), {"40000001": (D("110.00"), 0), "57200001": (0, D("110.00"))})
        self.assertEqual(lineas(cobro), {"57200001": (D("50.00"), 0), "43000001": (0, D("50.00"))})
        self.assertEqual((pago.clase, cobro.clase), ("Simple", "Simple"))


class ValidacionTests(BaseModelos):
    def datos(self, **cambios):
        return {"proveedor": "40000001", "compra": "60000001", "base": "100", "iva": "10",
                "cuenta_iva": "47200001", **cambios}

    def test_falta_un_campo_obligatorio(self):
        with self.assertRaisesMessage(ValidationError, "Falta «Base imponible»"):
            previsualizar("factura_proveedor", date(2026, 3, 1), self.datos(base=""))

    def test_cuenta_de_otro_tipo(self):
        with self.assertRaisesMessage(ValidationError, "«Proveedor»: elige una subcuenta de 400"):
            previsualizar("factura_proveedor", date(2026, 3, 1), self.datos(proveedor="41000001"))

    def test_cuenta_del_plan_en_lugar_de_subcuenta(self):
        with self.assertRaises(ValidationError):
            previsualizar("factura_proveedor", date(2026, 3, 1), self.datos(proveedor="400"))

    def test_importe_cero_o_no_numerico(self):
        for base in ("0", "abc"):
            with self.subTest(base=base), self.assertRaises(ValidationError):
                previsualizar("factura_proveedor", date(2026, 3, 1), self.datos(base=base))

    def test_opcion_no_valida(self):
        with self.assertRaisesMessage(ValidationError, "opción no válida"):
            previsualizar("factura_proveedor", date(2026, 3, 1), self.datos(iva="7"))

    def test_modelo_desconocido(self):
        with self.assertRaisesMessage(ValidationError, "desconocido"):
            previsualizar("no_existe", date(2026, 3, 1), {})

    def test_previsualizar_no_guarda_nada(self):
        vista = previsualizar("factura_proveedor", date(2026, 3, 1), self.datos())
        self.assertFalse(Asiento.objects.exists())
        self.assertTrue(vista["cuadra"])
        self.assertEqual((vista["debe"], vista["haber"], vista["clase"]), ("110.00", "110.00", "Compuesto"))
        self.assertEqual(vista["lineas"][-1], {"cuenta": "40000001", "nombre": "Frutas Prueba SL",
                                               "debe": "0.00", "haber": "110.00"})

    def test_concepto_escrito_a_mano_sustituye_al_automatico(self):
        asiento = crear_desde_modelo("factura_proveedor", date(2026, 3, 1), self.datos(), concepto="Mi concepto")
        self.assertEqual(asiento.concepto, "Mi concepto")


class AjustesTests(BaseModelos):
    def test_liquidacion_de_iva_a_pagar(self):
        self.compra()   # 10 de IVA soportado
        self.venta()    # 100 de IVA repercutido
        asiento = crear_desde_modelo("liquidacion_iva", date(2026, 6, 30), {
            "a_pagar": "47500001", "a_compensar": "47000001"})
        self.assertEqual(asiento.tipo, "ajuste")
        self.assertEqual(lineas(asiento), {
            "47700001": (D("100.00"), 0), "47200001": (0, D("10.00")), "47500001": (0, D("90.00"))})
        with self.assertRaisesMessage(ValidationError, "No hay IVA pendiente"):
            previsualizar("liquidacion_iva", date(2026, 6, 30), {"a_pagar": "47500001", "a_compensar": "47000001"})

    def test_liquidacion_de_iva_a_compensar(self):
        self.compra()
        asiento = crear_desde_modelo("liquidacion_iva", date(2026, 6, 30), {
            "a_pagar": "47500001", "a_compensar": "47000001"})
        self.assertEqual(lineas(asiento), {"47000001": (D("10.00"), 0), "47200001": (0, D("10.00"))})

    def test_la_liquidacion_solo_tiene_en_cuenta_hasta_su_fecha(self):
        self.compra(fecha=date(2026, 3, 2))
        self.compra(fecha=date(2026, 8, 1), documento="F-2")  # del trimestre siguiente
        asiento = crear_desde_modelo("liquidacion_iva", date(2026, 6, 30), {
            "a_pagar": "47500001", "a_compensar": "47000001"})
        self.assertEqual(lineas(asiento)["47200001"], (0, D("10.00")))


class CierreYAperturaTests(BaseModelos):
    def ejercicio_completo(self):
        self.compra()                                             # gasto 100 + IVA 10
        self.venta()                                              # ingreso 1000 + IVA 100
        crear_desde_modelo("cobro", date(2026, 5, 2), {"tesoreria": "57200001", "cliente": "43000001", "importe": "1100"})
        crear_desde_modelo("pago", date(2026, 5, 3), {"tercero": "40000001", "tesoreria": "57200001", "importe": "110"})

    def test_ciclo_completo_regularizacion_cierre_y_apertura(self):
        self.ejercicio_completo()
        fin = date(2026, 12, 31)

        reg = crear_desde_modelo("regularizacion", fin, {"resultado": "12900001"})
        self.assertEqual(reg.tipo, "regularizacion")
        self.assertEqual(lineas(reg), {
            "70500001": (D("1000.00"), 0), "60000001": (0, D("100.00")), "12900001": (0, D("900.00"))})

        cierre = crear_desde_modelo("cierre", fin, {})
        self.assertEqual(lineas(cierre), {
            "57200001": (0, D("990.00")), "47200001": (0, D("10.00")),
            "47700001": (D("100.00"), 0), "12900001": (D("900.00"), 0)})
        ejercicio = cierre.ejercicio
        self.assertEqual(saldos_subcuentas(ejercicio), [])           # todo a cero
        self.assertEqual((reg.numero, cierre.numero), (5, 6))       # los últimos

        apertura = crear_desde_modelo("apertura", date(2027, 1, 1), {})
        self.assertEqual((apertura.tipo, apertura.numero, apertura.ejercicio.anio), ("apertura", 1, 2027))
        self.assertEqual(lineas(apertura), {
            "57200001": (D("990.00"), 0), "47200001": (D("10.00"), 0),
            "47700001": (0, D("100.00")), "12900001": (0, D("900.00"))})

    def test_regularizacion_con_perdidas_va_al_debe_del_129(self):
        self.compra()
        reg = crear_desde_modelo("regularizacion", date(2026, 12, 31), {"resultado": "12900001"})
        self.assertEqual(lineas(reg)["12900001"], (D("100.00"), 0))

    def test_cierre_sin_regularizar_falla(self):
        self.ejercicio_completo()
        with self.assertRaisesMessage(ValidationError, "regularización"):
            crear_desde_modelo("cierre", date(2026, 12, 31), {})

    def test_apertura_sin_cierre_anterior_falla(self):
        with self.assertRaisesMessage(ValidationError, "No hay asiento de cierre"):
            crear_desde_modelo("apertura", date(2027, 1, 1), {})

    def test_regularizacion_fuera_del_ultimo_dia_falla(self):
        self.compra()
        with self.assertRaisesMessage(ValidationError, "último día"):
            crear_desde_modelo("regularizacion", date(2026, 11, 30), {"resultado": "12900001"})

    def test_solo_una_regularizacion_por_ejercicio(self):
        self.compra()
        crear_desde_modelo("regularizacion", date(2026, 12, 31), {"resultado": "12900001"})
        self.compra(fecha=date(2026, 12, 31), documento="F-2")
        with self.assertRaisesMessage(ValidationError, "ya tiene asiento de regularización"):
            crear_desde_modelo("regularizacion", date(2026, 12, 31), {"resultado": "12900001"})

    def test_tras_el_cierre_no_se_admiten_mas_asientos(self):
        self.ejercicio_completo()
        crear_desde_modelo("regularizacion", date(2026, 12, 31), {"resultado": "12900001"})
        crear_desde_modelo("cierre", date(2026, 12, 31), {})
        with self.assertRaisesMessage(ValidationError, "ya tiene asiento de cierre"):
            self.compra(fecha=date(2026, 10, 1), documento="F-9")


class LibreYNumeracionTests(BaseModelos):
    def test_asiento_libre_queda_vacio_en_borrador(self):
        asiento = crear_desde_modelo("libre", date(2026, 3, 1), {"tipo": "ajuste"}, concepto="Periodificación")
        self.assertEqual((asiento.tipo, asiento.estado, asiento.apuntes.count()), ("ajuste", "borrador", 0))
        self.assertEqual(asiento.concepto, "Periodificación")

    def test_apertura_libre_fuera_del_primer_dia_falla(self):
        with self.assertRaisesMessage(ValidationError, "primer día"):
            crear_desde_modelo("libre", date(2026, 1, 2), {"tipo": "apertura"})

    def test_la_apertura_siempre_es_el_numero_1(self):
        self.compra(fecha=date(2026, 3, 2))
        self.venta(fecha=date(2026, 4, 1))
        apertura = crear_desde_modelo("libre", date(2026, 1, 1), {"tipo": "apertura"})
        orden = list(Asiento.objects.filter(ejercicio=apertura.ejercicio).order_by("numero")
                     .values_list("tipo", "fecha"))
        self.assertEqual(apertura.numero, 1)
        self.assertEqual(orden, [("apertura", date(2026, 1, 1)), ("operacion", date(2026, 3, 2)),
                                 ("operacion", date(2026, 4, 1))])

    def test_un_asiento_con_fecha_anterior_se_numera_en_su_sitio(self):
        a = self.compra(fecha=date(2026, 5, 1))
        b = self.compra(fecha=date(2026, 2, 1), documento="F-2")
        a.refresh_from_db()
        self.assertEqual((b.numero, a.numero), (1, 2))


class ApiModelosTests(BaseModelos):
    def post(self, nombre, clave, cuerpo):
        url = reverse(f"cuadro_cuentas:{nombre}", args=[clave])
        return self.client.post(url, json.dumps(cuerpo), content_type="application/json")

    def cuerpo(self, **datos):
        return {"fecha": "2026-03-02", "documento": "F-1", "datos": {
            "proveedor": "40000001", "compra": "60000001", "base": "100", "iva": "10",
            "cuenta_iva": "47200001", **datos}}

    def test_previsualizar(self):
        r = self.post("api_modelo_previsualizar", "factura_proveedor", self.cuerpo())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["haber"], "110.00")

    def test_previsualizar_con_error(self):
        r = self.post("api_modelo_previsualizar", "factura_proveedor", self.cuerpo(base=""))
        self.assertEqual(r.status_code, 400)
        self.assertIn("Base imponible", r.json()["error"])

    def test_crear(self):
        r = self.post("api_modelo_crear", "factura_proveedor", self.cuerpo())
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()["ejercicio"], r.json()["mes"]), (2026, 3))
        self.assertEqual(Asiento.objects.get().referencia_origen, "F-1")

    def test_el_libro_ofrece_solo_las_subcuentas_de_cada_campo(self):
        modelos = {m["clave"]: m for m in self.client.get(reverse("cuadro_cuentas:libro_diario")).context["modelos"]}
        proveedor = next(c for c in modelos["factura_proveedor"]["campos"] if c["nombre"] == "proveedor")
        self.assertEqual([c["codigo"] for c in proveedor["cuentas"]], ["40000001"])

    def test_borrar_un_asiento_renumera_los_demas(self):
        primero = self.compra(fecha=date(2026, 3, 1))
        primero.estado = "borrador"
        primero.save()
        segundo = self.compra(fecha=date(2026, 4, 1), documento="F-2")
        r = self.client.delete(reverse("cuadro_cuentas:api_asiento", args=[primero.id]))
        self.assertTrue(r.json()["renumerado"])
        segundo.refresh_from_db()
        self.assertEqual(segundo.numero, 1)