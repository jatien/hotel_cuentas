"""Tests de la especificación funcional (fases 1 a 7) con datos ficticios."""
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from cuadro_cuentas import integridad
from cuadro_cuentas.consultas import (comparativa, contrapartidas, diario, extracto, sumas_y_saldos,
                                      sumas_y_saldos_periodo)
from cuadro_cuentas.cuentas_anuales import calcular_cuentas_anuales
from cuadro_cuentas.models import (Apunte, Asiento, CuentaContable, DatosEmpresa, Ejercicio, HistorialAsiento,
                                   Naturaleza, PreferenciaTabla, naturaleza_pgc)
from cuadro_cuentas.services import Linea, crear_asiento, puntear, rectificar_asiento, revertir_asiento

from .utils import D, crear_plan_minimo, crear_subcuenta_directa, ejercicio


class Base(TestCase):
    """Ejercicio 2026: apertura, una compra, una venta, un cobro y un pago."""

    @classmethod
    def setUpTestData(cls):
        crear_plan_minimo()
        for codigo, nombre in [("40000001", "Frutas Prueba SL"), ("60000001", "Compras alimentos"),
                               ("47200001", "IVA soportado"), ("43000001", "Agencia Prueba"),
                               ("70500001", "Alojamiento"), ("47700001", "IVA repercutido"),
                               ("57200001", "Banco Prueba"), ("12900001", "Resultado del ejercicio")]:
            crear_subcuenta_directa(codigo, nombre)
        cls.empresa = DatosEmpresa.objects.create(codigo="01", denominacion="Hotel Ficticio SL", nif="B00000000")
        cls.ej = ejercicio(2026)
        cls.apertura = crear_asiento(date(2026, 1, 1), "Apertura", [
            Linea("57200001", debe=D("1000")), Linea("12900001", haber=D("1000"))], tipo=Asiento.Tipo.APERTURA)
        cls.compra = crear_asiento(date(2026, 3, 2), "Factura Frutas F-1", [
            Linea("60000001", debe=D("100")), Linea("47200001", debe=D("10")), Linea("40000001", haber=D("110"))],
            numero_externo="F-1")
        cls.venta = crear_asiento(date(2026, 4, 1), "Factura habitación", [
            Linea("43000001", debe=D("1100")), Linea("70500001", haber=D("1000")), Linea("47700001", haber=D("100"))],
            numero_externo="V-1")
        cls.cobro = crear_asiento(date(2026, 5, 2), "Cobro agencia", [
            Linea("57200001", debe=D("1100")), Linea("43000001", haber=D("1100"))])
        cls.pago = crear_asiento(date(2026, 5, 3), "Pago Frutas", [
            Linea("40000001", debe=D("110")), Linea("57200001", haber=D("110"))])


# ===================== Fase 1: empresa, ejercicio y plan =====================

class EmpresaYPlanTests(Base):
    def test_naturaleza_segun_el_pgc_y_configurable(self):
        self.assertEqual(naturaleza_pgc("57200001"), Naturaleza.ACTIVO)
        self.assertEqual(naturaleza_pgc("40000001"), Naturaleza.PASIVO)
        self.assertEqual(naturaleza_pgc("2816"), Naturaleza.CORRECTORA)
        self.assertEqual(naturaleza_pgc("129"), Naturaleza.PATRIMONIO_NETO)
        self.assertEqual(naturaleza_pgc("4750"), Naturaleza.PASIVO)
        self.assertEqual(naturaleza_pgc("47"), Naturaleza.MIXTA)
        self.assertEqual(naturaleza_pgc("62"), Naturaleza.GASTO)
        cuenta = CuentaContable.objects.get(codigo="57200001")
        self.assertEqual((cuenta.naturaleza_label, cuenta.grupo, cuenta.grupo_label),
                         ("Activo", "5", "Cuentas financieras"))
        cuenta.naturaleza = Naturaleza.MIXTA
        self.assertEqual(cuenta.naturaleza_efectiva, Naturaleza.MIXTA)

    def test_ejercicios_no_se_solapan(self):
        e = Ejercicio(anio=2027, fecha_inicio=date(2026, 6, 1), fecha_fin=date(2027, 5, 31))
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_ejercicio_nuevo_queda_ligado_a_la_empresa(self):
        e = Ejercicio.para_fecha(date(2027, 2, 1))
        self.assertEqual(e.empresa, self.empresa)

    def test_hoja_de_empresa_y_ejercicio_activo(self):
        url = reverse("cuadro_cuentas:empresa")
        r = self.client.post(url, {"accion": "ejercicio", "ej-anio": 2027, "ej-fecha_inicio": "2027-01-01",
                                   "ej-fecha_fin": "2027-12-31"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Ejercicio.objects.filter(anio=2027, empresa=self.empresa).exists())
        # el ejercicio elegido se aplica a las demás hojas
        self.client.get(url + "?ejercicio=2026")
        r = self.client.get(reverse("cuadro_cuentas:diario"))
        self.assertEqual(r.context["ejercicio"].anio, 2026)
        r = self.client.get(url)
        self.assertContains(r, "Hotel Ficticio SL")
        fila = next(e for e in r.context["ejercicios"] if e.anio == 2026)
        self.assertEqual((fila.n, fila.ultimo_numero, fila.ultimo_registro), (5, 5, date(2026, 5, 3)))

    def test_no_se_cierra_con_borradores(self):
        Asiento.objects.create(fecha=date(2026, 6, 1), concepto="Pendiente", ejercicio=self.ej)
        r = self.client.post(reverse("cuadro_cuentas:empresa"), {"accion": "cerrar", "id": self.ej.id}, follow=True)
        self.ej.refresh_from_db()
        self.assertFalse(self.ej.cerrado)
        self.assertContains(r, "borradores")

    def test_plan_de_cuentas_muestra_saldos_agregados(self):
        r = self.client.get(reverse("cuadro_cuentas:plan_cuentas") + "?ejercicio=2026")
        self.assertEqual(r.status_code, 200)
        cuentas = {}
        def recorrer(nodos):
            for n in nodos:
                cuentas[n["cuenta"].codigo] = n["cuenta"]
                recorrer(n["hijos"])
        recorrer(r.context["arbol"])
        # 1000 apertura + 1100 cobro - 110 pago
        self.assertEqual(cuentas["57200001"].saldo, D("1990"))
        self.assertEqual(cuentas["572"].saldo, D("1990"))
        self.assertEqual(cuentas["5"].saldo, D("1990"))
        self.assertContains(r, "Saldo deudor")

    def test_ficha_de_cuenta_con_naturaleza_y_superior(self):
        d = self.client.get(reverse("cuadro_cuentas:api_ficha_cuenta", args=["40000001"])).json()
        self.assertEqual((d["naturaleza"], d["superior"], d["grupo"]), ("Pasivo", "400", "4"))
        self.assertIn("cuenta=40000001", d["mayor"])


# ===================== Fase 2: asiento y contabilización =====================

class ContabilizacionTests(Base):
    def borrador(self, lineas, **campos):
        a = Asiento.objects.create(fecha=campos.pop("fecha", date(2026, 6, 1)), concepto=campos.pop("concepto", "Prueba"),
                                   ejercicio=self.ej, **campos)
        for i, (cuenta, debe, haber) in enumerate(lineas, 1):
            Apunte.objects.create(asiento=a, orden=i, cuenta=CuentaContable.objects.get(codigo=cuenta),
                                  debe=D(debe), haber=D(haber))
        return a

    def test_asiento_con_muchos_apuntes(self):
        lineas = [Linea("60000001", debe=D("10")) for _ in range(12)] + [Linea("40000001", haber=D("120"))]
        a = crear_asiento(date(2026, 6, 2), "Doce compras", lineas)
        self.assertEqual(a.apuntes.count(), 13)
        self.assertEqual(sorted(a.apuntes.values_list("orden", flat=True)), list(range(1, 14)))
        self.assertTrue(a.cuadrado)

    def test_validaciones_antes_de_contabilizar(self):
        a = self.borrador([("60000001", "100", "0")])
        self.assertIn("al menos dos apuntes", " ".join(a.errores_contabilizacion()))
        a = self.borrador([("60000001", "100", "0"), ("40000001", "0", "90")])
        errores = " ".join(a.errores_contabilizacion())
        self.assertIn("diferencia 10,00", errores)
        with self.assertRaises(ValidationError):
            a.contabilizar()
        a.refresh_from_db()
        self.assertEqual(a.estado, Asiento.Estado.BORRADOR)

    def test_cuenta_inactiva_no_se_contabiliza(self):
        CuentaContable.objects.filter(codigo="60000001").update(activa=False)
        a = self.borrador([("60000001", "100", "0"), ("40000001", "0", "100")])
        self.assertIn("inactiva", " ".join(a.errores_contabilizacion()))

    def test_contabilizar_guarda_fecha_usuario_e_historial(self):
        usuario = get_user_model().objects.create_user("contable", password="x")
        a = self.borrador([("60000001", "100", "0"), ("40000001", "0", "100")])
        a.contabilizar(usuario)
        a.refresh_from_db()
        self.assertEqual(a.estado, Asiento.Estado.CONTABILIZADO)
        self.assertIsNotNone(a.contabilizado_en)
        self.assertEqual(a.contabilizado_por, usuario)
        self.assertTrue(a.historial.filter(accion=HistorialAsiento.Accion.CONTABILIZAR, usuario=usuario).exists())

    def test_duplicado_por_numero_externo(self):
        a = self.borrador([("60000001", "100", "0"), ("47200001", "10", "0"), ("40000001", "0", "110")],
                          numero_externo="f-1")
        with self.assertRaises(ValidationError) as ctx:
            a.contabilizar()
        self.assertEqual(ctx.exception.code, "duplicado")
        a.contabilizar(permitir_duplicado=True)
        self.assertEqual(a.estado, Asiento.Estado.CONTABILIZADO)

    def test_duplicado_sin_numero_mismo_dia_concepto_y_cuentas(self):
        with self.assertRaises(ValidationError):
            crear_asiento(date(2026, 5, 3), "Pago Frutas", [
                Linea("40000001", debe=D("110")), Linea("57200001", haber=D("110"))])
        self.assertEqual(Asiento.objects.filter(concepto="Pago Frutas").count(), 1)   # atómico: nada a medias
        crear_asiento(date(2026, 5, 3), "Pago Frutas", [
            Linea("40000001", debe=D("110")), Linea("57200001", haber=D("110"))], permitir_duplicado=True)

    def test_anular_conserva_el_asiento_y_deja_de_sumar(self):
        with self.assertRaises(ValidationError):
            self.pago.anular("")
        self.pago.anular("Pago registrado por error")
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.estado, Asiento.Estado.ANULADO)
        self.assertEqual(self.pago.numero, 5)
        e = extracto(self.ej, "57200001")
        self.assertEqual(e.saldo_final, D("2100"))
        filas, _ = sumas_y_saldos(self.ej, "400")
        self.assertEqual(filas[0]["saldo"], D("-110"))
        with self.assertRaises(ValidationError):
            self.pago.contabilizar()

    def test_revertir_y_rectificar(self):
        reversion = revertir_asiento(self.compra, date(2026, 6, 30), motivo="Importe equivocado")
        self.assertEqual(reversion.revierte, self.compra)
        self.assertEqual(reversion.estado, Asiento.Estado.CONTABILIZADO)
        filas = {f["codigo"]: f["saldo"] for f in sumas_y_saldos(self.ej, "60")[0]}
        self.assertEqual(filas["60000001"], 0)   # efecto neto cero, los dos quedan en el diario
        with self.assertRaises(ValidationError):
            revertir_asiento(self.compra, date(2026, 6, 30))
        with self.assertRaises(ValidationError):
            self.compra.anular("ya revertido")
        _, copia = rectificar_asiento(self.venta, date(2026, 7, 1), motivo="Cliente equivocado")
        self.assertEqual((copia.estado, copia.rectifica), (Asiento.Estado.BORRADOR, self.venta))
        self.assertEqual(copia.totales(), (D("1100"), D("1100")))
        acciones = set(self.venta.historial.values_list("accion", flat=True))
        self.assertTrue({"revertir", "rectificar"} <= acciones)

    def test_no_se_revierte_la_apertura(self):
        with self.assertRaises(ValidationError):
            revertir_asiento(self.apertura, date(2026, 2, 1))

    def test_ficha_del_asiento_y_sus_acciones(self):
        a = self.borrador([("60000001", "50", "0"), ("40000001", "0", "50")], concepto="Compra pan")
        url = reverse("cuadro_cuentas:asiento", args=[a.id])
        r = self.client.get(url)
        self.assertContains(r, "Contabilizar")
        r = self.client.post(url, {"accion": "cabecera", "fecha": "2026-06-01", "titulo": "Pan",
                                   "concepto": "Compra pan", "numero_externo": "T-9",
                                   "tipo_documento": "factura_recibida", "fecha_documento": "2026-05-30"})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertEqual((a.titulo, a.numero_externo, a.fecha_documento), ("Pan", "T-9", date(2026, 5, 30)))
        self.client.post(url, {"accion": "contabilizar"})
        a.refresh_from_db()
        self.assertEqual(a.estado, Asiento.Estado.CONTABILIZADO)
        # ya no se modifica la cabecera
        self.client.post(url, {"accion": "cabecera", "fecha": "2026-06-02", "concepto": "Otro"})
        a.refresh_from_db()
        self.assertEqual(a.concepto, "Compra pan")
        r = self.client.post(url, {"accion": "revertir", "fecha": "2026-06-05", "motivo": "Error"})
        reversion = Asiento.objects.get(revierte=a)
        self.assertRedirects(r, reverse("cuadro_cuentas:asiento", args=[reversion.id]))
        r = self.client.get(url)
        self.assertContains(r, f"asiento {reversion.numero}")

    def test_documentos_adjuntos(self):
        url = reverse("cuadro_cuentas:asiento", args=[self.compra.id])
        with self.settings(MEDIA_ROOT=self._tmp()):
            r = self.client.post(url, {"accion": "adjuntar", "tipo": "factura_recibida", "numero": "F-1",
                                       "archivo": SimpleUploadedFile("f1.pdf", b"%PDF-1.4 prueba")})
            self.assertEqual(r.status_code, 302)
        adjunto = self.compra.adjuntos.get()
        self.assertEqual(adjunto.numero, "F-1")
        self.assertTrue(self.compra.historial.filter(accion="documento").exists())
        # se encuentra por el número del documento
        self.assertIn(self.compra, list(diario(self.ej, {"documento": "F-1"})))

    def _tmp(self):
        import tempfile
        return tempfile.mkdtemp()


# ===================== Fase 3: diario, mayor, contrapartidas y punteo =====================

class DiarioMayorTests(Base):
    def test_contrapartida_unica_y_multiple(self):
        lineas = list(self.compra.apuntes.select_related("cuenta"))
        c = contrapartidas(lineas)
        proveedor = next(a for a in lineas if a.cuenta.codigo == "40000001")
        compra = next(a for a in lineas if a.cuenta.codigo == "60000001")
        self.assertTrue(c[proveedor.id].multiple)          # 600 y 472: no se elige una arbitraria
        self.assertEqual(c[proveedor.id].texto, "Varias (2)")
        self.assertEqual(c[compra.id].unica, ("40000001", "Frutas Prueba SL"))

    def test_cada_apunte_en_el_diario_y_en_el_mayor(self):
        apuntes = Apunte.objects.filter(asiento__ejercicio=self.ej)
        en_diario = sum(a.n_apuntes for a in diario(self.ej, {}))
        en_mayor = sum(len(extracto(self.ej, c).lineas)
                       for c in set(apuntes.values_list("cuenta__codigo", flat=True)))
        self.assertEqual(en_diario, apuntes.count())
        self.assertEqual(en_mayor, apuntes.count())

    def test_filtros_del_diario(self):
        def numeros(**f):
            return [a.numero for a in diario(self.ej, f)]
        self.assertEqual(numeros(cuenta="572"), [1, 4, 5])
        self.assertEqual(numeros(importe=D("1100")), [3, 4])
        self.assertEqual(numeros(documento="V-1"), [3])
        self.assertEqual(numeros(concepto="frutas"), [2, 5])
        self.assertEqual(numeros(desde=date(2026, 4, 1), hasta=date(2026, 5, 2)), [3, 4])
        self.assertEqual(numeros(numero=2), [2])
        apunte = self.cobro.apuntes.first()
        puntear([apunte], "punteado")
        self.assertEqual(numeros(punteo="punteado"), [4])
        self.assertEqual(len(numeros(punteo="sin")), 5)

    def test_paginas_del_diario(self):
        base = reverse("cuadro_cuentas:diario")
        r = self.client.get(base + "?ejercicio=2026")
        self.assertContains(r, "Asiento 1")
        self.assertContains(r, "Varias (2)")
        self.assertEqual(r.context["total_debe"], r.context["total_haber"])
        r = self.client.get(base + "?ejercicio=2026&vista=resumen")
        self.assertContains(r, 'data-tabla="diario-resumen"')
        r = self.client.get(base + "?ejercicio=2026&vista=resumen&exportar=csv")
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        contenido = r.content.decode("utf-8-sig")
        self.assertIn("Asiento;Fecha;Número", contenido)
        self.assertIn("1100,00", contenido)
        r = self.client.get(base + "?ejercicio=2026&exportar=xlsx")
        self.assertIn("spreadsheetml", r["Content-Type"])

    def test_el_punteo_no_cambia_importes(self):
        apunte = self.cobro.apuntes.get(cuenta__codigo="57200001")
        antes = (apunte.debe, apunte.haber, apunte.cuenta_id, apunte.asiento_id, self.cobro.fecha)
        r = self.client.post(reverse("cuadro_cuentas:api_punteo"), json.dumps({"ids": [apunte.id], "estado": "punteado"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        apunte.refresh_from_db()
        self.assertEqual(apunte.punteo, "punteado")
        self.assertEqual((apunte.debe, apunte.haber, apunte.cuenta_id, apunte.asiento_id,
                          apunte.asiento.fecha), antes)
        self.assertTrue(self.cobro.historial.filter(accion="punteo").exists())
        # el punteo manual no marca conciliación bancaria
        r = self.client.post(reverse("cuadro_cuentas:api_punteo"), json.dumps({"ids": [apunte.id], "estado": "conciliado"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_mayor_con_columnas_y_filtro_de_punteo(self):
        apunte = self.cobro.apuntes.get(cuenta__codigo="57200001")
        puntear([apunte], "punteado")
        url = reverse("cuadro_cuentas:extractos") + "?ejercicio=2026&cuenta=57200001"
        r = self.client.get(url)
        for columna in ("Asiento", "Apunte", "Número", "Documento", "Contrapartida", "Punteo"):
            self.assertContains(r, f">{columna}</th>")
        r = self.client.get(url + "&punteo=punteado")
        ext = r.context["extracto"]
        self.assertEqual(len(ext.lineas), 1)
        self.assertEqual(ext.saldo_final, D("1990"))       # el filtro no cambia los totales
        r = self.client.get(url + "&exportar=csv")
        self.assertIn("Saldo inicial", r.content.decode("utf-8-sig"))


# ===================== Fase 4: sumas y saldos e integridad =====================

class SumasYComprobacionTests(Base):
    def test_columnas_anterior_periodo_acumulado(self):
        filas, totales, ok = sumas_y_saldos_periodo(self.ej, date(2026, 5, 1), date(2026, 5, 31))
        banco = next(f for f in filas if f.codigo == "57200001")
        self.assertEqual((banco.anterior_debe, banco.anterior_haber), (D("1000"), 0))     # apertura
        self.assertEqual((banco.periodo_debe, banco.periodo_haber), (D("1100"), D("110")))
        self.assertEqual((banco.acumulado_debe, banco.acumulado_haber), (D("2100"), D("110")))
        self.assertEqual((banco.saldo_deudor, banco.saldo_acreedor), (D("1990"), 0))
        self.assertTrue(all(ok.values()))
        self.assertEqual(totales["periodo_debe"], D("1210"))

    def test_niveles_suman_una_vez(self):
        sub, t8, _ = sumas_y_saldos_periodo(self.ej)
        grupos, t1, _ = sumas_y_saldos_periodo(self.ej, nivel=1)
        self.assertEqual([f.codigo for f in grupos], ["1", "4", "5", "6", "7"])
        # agrupar compensa saldos dentro del grupo, pero el neto total es el mismo
        self.assertEqual(t1["saldo_deudor"] - t1["saldo_acreedor"], t8["saldo_deudor"] - t8["saldo_acreedor"])
        self.assertEqual(t1["periodo_debe"], t8["periodo_debe"])
        cuatro = next(f for f in grupos if f.codigo == "4")
        self.assertEqual(cuatro.saldo, sum((f.saldo for f in sub if f.codigo.startswith("4")), D("0")))
        self.assertEqual(cuatro.nombre, "Acreedores y deudores por operaciones comerciales")

    def test_rango_sin_movimiento_y_sin_apertura(self):
        filas, _, _ = sumas_y_saldos_periodo(self.ej, nivel=3, cuenta_desde="4", cuenta_hasta="47")
        self.assertEqual([f.codigo for f in filas], ["400", "430", "472", "477"])
        filas, _, _ = sumas_y_saldos_periodo(self.ej, nivel=3, cuenta_desde="6", cuenta_hasta="6",
                                             sin_movimiento=True)
        self.assertIn("626", [f.codigo for f in filas])
        filas, _, _ = sumas_y_saldos_periodo(self.ej, incluir_apertura=False)
        self.assertNotIn("12900001", [f.codigo for f in filas])

    def test_borradores_no_suman_salvo_que_se_pida(self):
        a = Asiento.objects.create(fecha=date(2026, 6, 1), concepto="Borrador", ejercicio=self.ej)
        Apunte.objects.create(asiento=a, cuenta=CuentaContable.objects.get(codigo="60000001"), debe=D("5"))
        _, t, _ = sumas_y_saldos_periodo(self.ej)
        _, t2, _ = sumas_y_saldos_periodo(self.ej, solo_contabilizados=False)
        self.assertEqual(t2["periodo_debe"] - t["periodo_debe"], D("5"))

    def test_sumas_coinciden_con_el_mayor(self):
        filas, _, _ = sumas_y_saldos_periodo(self.ej, date(2026, 3, 1), date(2026, 12, 31))
        for f in filas:
            e = extracto(self.ej, f.codigo, date(2026, 3, 1), date(2026, 12, 31), solo_contabilizados=True)
            self.assertEqual(f.saldo, e.saldo_final, f.codigo)

    def test_pagina_de_sumas_y_saldos(self):
        url = reverse("cuadro_cuentas:sumas_saldos")
        r = self.client.get(url + "?ejercicio=2026")
        self.assertContains(r, "Debe anterior")
        self.assertTrue(r.context["form"].cleaned_data["apertura"])
        r = self.client.get(url + "?ejercicio=2026&nivel=3&exportar=xlsx")
        self.assertIn("spreadsheetml", r["Content-Type"])
        r = self.client.get(url + "?ejercicio=2026&nivel=3&exportar=csv")
        self.assertIn("TOTALES", r.content.decode("utf-8-sig"))

    def test_comprobacion_sin_errores(self):
        controles = {c.clave: c for c in integridad.comprobar(self.ej)}
        self.assertEqual(controles["cuadre"].estado, integridad.OK)
        self.assertEqual(controles["totales"].estado, integridad.OK)
        self.assertEqual(controles["numeracion"].estado, integridad.OK)
        r = self.client.get(reverse("cuadro_cuentas:comprobacion") + "?ejercicio=2026")
        self.assertContains(r, "El diario, el mayor y las sumas y saldos")

    def test_comprobacion_detecta_descuadres(self):
        # un descuadre que solo puede venir de tocar la base de datos por fuera
        Apunte.objects.filter(asiento=self.pago, haber__gt=0).update(haber=D("100"))
        controles = {c.clave: c for c in integridad.comprobar(self.ej)}
        self.assertEqual(controles["cuadre"].estado, integridad.ERROR)
        self.assertIn("Asiento 5", controles["cuadre"].detalle[0][0])
        self.assertEqual(controles["totales"].estado, integridad.ERROR)

    def test_comprobacion_avisa_de_doble_correccion(self):
        reversion = revertir_asiento(self.pago, date(2026, 6, 1))
        Asiento.objects.filter(pk=self.pago.pk).update(estado=Asiento.Estado.ANULADO)
        controles = {c.clave: c for c in integridad.comprobar(self.ej)}
        self.assertEqual(controles["reversiones"].estado, integridad.ERROR)
        self.assertIn(str(reversion.numero), controles["reversiones"].detalle[0][0])


# ===================== Fase 5: balance de situación, PyG y comparativa =====================

class BalancesTests(Base):
    def test_anulados_fuera_de_las_cuentas_anuales(self):
        antes = calcular_cuentas_anuales(self.ej, "normal")["pyg"].valores
        self.venta.anular("Factura emitida por error")
        despues = calcular_cuentas_anuales(self.ej, "normal")["pyg"].valores
        self.assertNotEqual(antes, despues)

    def test_comparativa_entre_ejercicios(self):
        ej27 = ejercicio(2027)
        crear_asiento(date(2027, 2, 1), "Venta 2027", [
            Linea("57200001", debe=D("500")), Linea("70500001", haber=D("500"))])
        filas = {f["codigo"]: f for f in comparativa(ej27, self.ej, nivel=3)}
        self.assertEqual(filas["705"]["actual"], D("-500"))
        self.assertEqual(filas["705"]["anterior"], D("-1000"))
        self.assertEqual(filas["705"]["variacion"], D("500"))
        self.assertEqual(filas["705"]["porcentaje"], D("50.0"))
        r = self.client.get(reverse("cuadro_cuentas:comparativa") + "?ejercicio=2027&anterior=2026&nivel=3")
        self.assertContains(r, "Comparativa de ejercicios")

    def test_partidas_enlazan_al_mayor(self):
        r = self.client.get(reverse("cuadro_cuentas:cuentas_anuales") + "?ejercicio=2026")
        self.assertContains(r, "cuenta=57200001&solo_contabilizados=1")


# ===================== Fase 6: preferencias de tablas =====================

class PreferenciasTests(TestCase):
    def test_sin_sesion_se_guardan_en_el_navegador(self):
        r = self.client.get(reverse("cuadro_cuentas:api_preferencias", args=["diario"]))
        self.assertEqual(r.json(), {"columnas": None, "servidor": False})

    def test_por_usuario(self):
        usuario = get_user_model().objects.create_user("ana", password="x")
        self.client.force_login(usuario)
        url = reverse("cuadro_cuentas:api_preferencias", args=["diario"])
        self.client.post(url, json.dumps({"ocultas": ["documento"], "anchos": {"concepto": "300px"}}),
                         content_type="application/json")
        self.assertEqual(PreferenciaTabla.objects.get(usuario=usuario).columnas["ocultas"], ["documento"])
        self.assertEqual(self.client.get(url).json()["columnas"]["anchos"]["concepto"], "300px")


# ===================== Fase 7: estilo centralizado y navegación =====================

@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class EstiloTests(Base):
    def test_todas_las_hojas_comparten_tema_y_navegacion(self):
        for nombre in ("empresa", "plan_cuentas", "libro_diario", "diario", "extractos", "buscador",
                       "hojas", "sumas_saldos", "comprobacion", "cuentas_anuales", "comparativa"):
            r = self.client.get(reverse(f"cuadro_cuentas:{nombre}"))
            self.assertEqual(r.status_code, 200, nombre)
            self.assertContains(r, "cuadro_cuentas/tema.css")
            self.assertContains(r, 'class="nav-lateral"')

    def test_los_colores_solo_estan_en_el_tema(self):
        from pathlib import Path
        carpeta = Path(__file__).resolve().parent.parent / "static" / "cuadro_cuentas"
        for css in carpeta.glob("*.css"):
            if css.name != "tema.css":
                self.assertNotIn(":root {", css.read_text(encoding="utf-8"), css.name)
