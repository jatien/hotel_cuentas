from datetime import date

from copilot_pms.models import EstadoLlamada
from copilot_pms.tests.utils import ContabilidadBase


class SaldoCuentaTests(ContabilidadBase):
    def test_saldo_oficial_excluye_borradores_y_avisa(self):
        r = self.ejecutar("saldo_cuenta", cuenta="572", hasta="2026-06-30")
        self.assertEqual(r.estado, EstadoLlamada.OK)
        d = r.datos
        self.assertEqual(d["entidad"]["denominacion"], "Hotel Prueba S.L.")
        self.assertEqual(d["moneda"], "EUR")
        self.assertEqual(d["ejercicio"], 2026)
        self.assertEqual(d["saldo_apertura"]["neto"], "1000.00")
        self.assertEqual(d["movimiento_periodo"]["debe"], "500.00")
        self.assertEqual(d["saldo_final"]["importe"], "1500.00")
        self.assertEqual(d["saldo_final"]["naturaleza"], "deudor")
        self.assertTrue(any("borrador" in a for a in r.avisos))
        self.assertTrue(any("no está cerrado" in a for a in r.avisos))
        self.assertIn("/contabilidad/extractos/?", d["enlace_extracto"])

    def test_incluir_borradores(self):
        r = self.ejecutar("saldo_cuenta", cuenta="57200001", hasta="2026-06-30", solo_contabilizados=False)
        self.assertEqual(r.datos["saldo_final"]["neto"], "1300.00")
        self.assertEqual(r.datos["cuenta"]["nombre"], "Banco Demo c/c")
        self.assertFalse(r.datos["cuenta"]["agrupa_subcuentas"])

    def test_periodo_parcial_mueve_lo_anterior_a_la_apertura(self):
        r = self.ejecutar("saldo_cuenta", cuenta="572", desde="2026-04-01", hasta="2026-06-30")
        self.assertEqual(r.datos["saldo_apertura"]["neto"], "1500.00")
        self.assertEqual(r.datos["movimiento_periodo"]["neto"], "0.00")
        self.assertEqual(r.datos["saldo_final"]["neto"], "1500.00")

    def test_saldo_acreedor(self):
        r = self.ejecutar("saldo_cuenta", cuenta="705", hasta="2026-12-31")
        self.assertEqual(r.datos["saldo_final"]["naturaleza"], "acreedor")
        self.assertEqual(r.datos["saldo_final"]["importe"], "500.00")
        self.assertEqual(r.datos["saldo_final"]["neto"], "-500.00")

    def test_fecha_por_defecto_es_hoy(self):
        r = self.ejecutar("saldo_cuenta", cuenta="572")
        self.assertEqual(r.datos["periodo"]["hasta"], "2026-10-22")

    def test_periodo_de_varios_ejercicios_pide_aclaracion(self):
        r = self.ejecutar("saldo_cuenta", cuenta="572", desde="2025-12-01", hasta="2026-02-01")
        self.assertEqual(r.estado, EstadoLlamada.ACLARAR)
        self.assertEqual(r.codigo, "PERIODO_VARIOS_EJERCICIOS")
        self.assertEqual(r.datos, {})

    def test_sin_ejercicio_no_inventa(self):
        r = self.ejecutar("saldo_cuenta", cuenta="572", hasta="2024-05-01")
        self.assertEqual(r.estado, EstadoLlamada.NO_DISPONIBLE)
        self.assertEqual(r.codigo, "EJERCICIO_NO_EXISTE")
        self.assertEqual(r.datos["ejercicios_existentes"], [2026])

    def test_cuenta_inexistente(self):
        r = self.ejecutar("saldo_cuenta", cuenta="999")
        self.assertEqual(r.estado, EstadoLlamada.NO_DISPONIBLE)
        self.assertEqual(r.codigo, "CUENTA_NO_EXISTE")

    def test_argumentos_no_validos(self):
        for args in ({"cuenta": "banco"}, {"cuenta": "572", "extra": 1}, {"cuenta": "572", "hasta": "ayer"}):
            r = self.ejecutar("saldo_cuenta", **args)
            self.assertEqual(r.estado, EstadoLlamada.INVALIDO, args)


class OtrasConsultasTests(ContabilidadBase):
    def test_buscar_cuentas_por_nombre_y_nif(self):
        r = self.ejecutar("buscar_cuentas", texto="banco demo")
        self.assertEqual([c["codigo"] for c in r.datos["cuentas"]], ["57200001"])
        r = self.ejecutar("buscar_cuentas", texto="A12345674")
        self.assertEqual(r.datos["cuentas"][0]["codigo"], "40000001")
        r = self.ejecutar("buscar_cuentas", texto="no existe nada así")
        self.assertEqual(r.estado, EstadoLlamada.VACIO)

    def test_sumas_y_saldos_cuadra(self):
        r = self.ejecutar("balance_sumas_saldos", ejercicio=2026)
        self.assertTrue(r.datos["cuadra"])
        self.assertEqual(r.datos["totales"]["debe"], "1500.00")
        codigos = [f["codigo"] for f in r.datos["filas"]]
        self.assertEqual(codigos, ["40000001", "57200001", "70500001"])

    def test_extracto_con_limite(self):
        r = self.ejecutar("extracto_cuenta", cuenta="572", hasta="2026-12-31", limite=1)
        self.assertEqual(len(r.datos["movimientos"]), 1)
        self.assertEqual(r.datos["movimientos"][0]["concepto"], "Venta habitaciones")
        self.assertEqual(r.datos["saldo_final"]["neto"], "1500.00")
        self.assertTrue(any("últimos 1 de 2" in a for a in r.avisos))

    def test_cuentas_anuales_pyg(self):
        r = self.ejecutar("cuentas_anuales", ejercicio=2026, estado="pyg", detalle=0)
        self.assertEqual(r.estado, EstadoLlamada.OK)
        partidas = {p["ref"]: p for s in r.datos["secciones"] for p in s["partidas"]}
        self.assertEqual(partidas["1"]["actual"], "500.00")  # importe neto de la cifra de negocios
        self.assertEqual(partidas["A5"]["actual"], "500.00")  # resultado del ejercicio
        self.assertTrue(any("borrador" in a for a in r.avisos))

    def test_cuentas_anuales_sin_ejercicio(self):
        r = self.ejecutar("cuentas_anuales", ejercicio=2019, estado="balance")
        self.assertEqual(r.estado, EstadoLlamada.NO_DISPONIBLE)

    def test_herramienta_desconocida(self):
        r = self.ejecutar("reservar_habitacion", habitacion="101")
        self.assertEqual(r.estado, EstadoLlamada.DESCONOCIDA)

    def test_fechas_de_hoy_en_contexto(self):
        from copilot_pms.tests.utils import ctx

        r = self.ejecutar("saldo_cuenta", ctx(hoy=date(2026, 2, 1)), cuenta="572")
        self.assertEqual(r.datos["saldo_final"]["neto"], "1000.00")
