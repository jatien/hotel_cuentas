"""Tests de la app analisis. Todos los datos son ficticios."""

import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from .importacion import (ErrorImportacion, _departamento_de_fichero, importar_acreedores,
                          importar_departamentos_albaranes, importar_extras, importar_terceros)
from .models import (AsignacionDepartamento, ApunteIva, CompraProveedor, DepartamentoAlbaran, ExtraPersonal,
                     FacturaAcreedor, GastoAdicional, Reserva, Tercero)
from .resumen import ACREEDORES, SIN_ASIGNAR, calcular_resumen, normalizar_departamento

HOY = date(2026, 6, 15)


def reserva(numero, entrada, salida, estancias="0", tasas="0", extras="0", habitaciones=1, agencia="AGENCIA A"):
    return Reserva.objects.create(
        numero=numero, fecha_reserva=date(2026, 1, 1), agencia=agencia, estado="Salida",
        entrada=entrada, salida=salida, habitaciones=habitaciones,
        fact_estancias=D(estancias), fact_impuesto=D(tasas), fact_extras=D(extras),
        total_estancias=D(estancias),
    )


class RepartoIngresosTests(TestCase):
    def test_reparte_por_noches_entre_meses(self):
        # 4 noches: 30/4, 1/5, 2/5, 3/5 -> 1 noche en abril y 3 en mayo
        reserva(1, date(2026, 4, 30), date(2026, 5, 4), estancias="400")
        r = calcular_resumen(2026, hoy=HOY, con_iva=True)
        self.assertEqual(r["mensual"]["estancias"][3], D("100"))
        self.assertEqual(r["mensual"]["estancias"][4], D("300"))
        self.assertEqual(r["totales"]["noches_hab"], 4)

    def test_las_tasas_se_suman_al_ingreso(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 3), estancias="200", tasas="8", extras="12")
        t = calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]
        self.assertEqual(t["ingresos"], D("220"))
        self.assertEqual(t["tasas"], D("8"))
        self.assertEqual(t["adr"], D("100"))      # el ADR solo usa estancias

    def test_sin_iva_quita_el_10_por_ciento_salvo_a_las_tasas(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 3), estancias="220", tasas="8", extras="11")
        t = calcular_resumen(2026, hoy=HOY)["totales"]
        self.assertAlmostEqual(float(t["estancias"]), 200, places=6)
        self.assertAlmostEqual(float(t["extras"]), 10, places=6)
        self.assertEqual(t["tasas"], D("8"))

    def test_noches_futuras_son_previsto(self):
        reserva(1, date(2026, 6, 14), date(2026, 6, 18), estancias="400")   # 14 y 15 realizadas
        sin = calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]
        con = calcular_resumen(2026, incluir_previsto=True, hoy=HOY, con_iva=True)["totales"]
        self.assertEqual(sin["ingresos"], D("200"))
        self.assertEqual(sin["previsto"], D("200"))
        self.assertEqual(con["ingresos"], D("400"))

    def test_reserva_que_cruza_el_ano_solo_cuenta_sus_noches(self):
        reserva(1, date(2025, 12, 30), date(2026, 1, 2), estancias="300")  # 1 de 3 noches en 2026
        self.assertEqual(calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]["ingresos"], D("100"))


class GastosTests(TestCase):
    def setUp(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 11), estancias="10000")
        CompraProveedor.objects.create(tipo="A", numero="1", fecha=date(2026, 5, 3), codigo_proveedor=7,
                                       proveedor="PROVEEDOR X", bruto=D("1000"), iva=D("100"), total=D("1100"))
        FacturaAcreedor.objects.create(fichero="luz", acreedor="LUZ SA", fecha=date(2026, 5, 5),
                                       cuenta="628000001", importe=D("500"))
        FacturaAcreedor.objects.create(fichero="obra", acreedor="OBRAS SL", fecha=date(2026, 5, 6),
                                       cuenta="212000001", importe=D("9000"))
        AsignacionDepartamento.objects.create(origen="proveedor", clave="7", nombre="PROVEEDOR X",
                                              departamento="Cocina")

    def test_beneficio_excluye_inversiones(self):
        t = calcular_resumen(2026, hoy=HOY, con_iva=True)["totales"]
        self.assertEqual(t["proveedores"], D("1100"))   # total con IVA
        self.assertEqual(t["acreedores"], D("500"))
        self.assertEqual(t["inversiones"], D("9000"))
        self.assertEqual(t["beneficio"], D("10000") - D("1600"))

    def test_sin_iva_usa_la_base_y_el_libro_de_iva(self):
        # IVA de la factura de luz en el libro de soportado (mismo asiento y documento)
        FacturaAcreedor.objects.filter(fichero="luz").update(asiento=10, documento="L1", importe=D("121"))
        ApunteIva.objects.create(libro="soportado", fecha=date(2026, 5, 5), documento="L1", asiento=10,
                                 concepto="S/FRA LUZ SA", cuenta="410000001", cuota=D("21"))
        t = calcular_resumen(2026, hoy=HOY)["totales"]
        self.assertEqual(t["proveedores"], D("1000"))   # base del diario
        self.assertEqual(t["acreedores"], D("100"))     # 121 - 21

    def test_inversion_del_sujeto_pasivo_no_descuenta_iva(self):
        FacturaAcreedor.objects.filter(fichero="luz").update(asiento=11, documento="B1")
        for libro in ("soportado", "repercutido"):
            ApunteIva.objects.create(libro=libro, fecha=date(2026, 5, 5), documento="B1", asiento=11,
                                     concepto="S/FRA EXTRANJERA", cuenta="410000002", cuota=D("105"))
        self.assertEqual(calcular_resumen(2026, hoy=HOY)["totales"]["acreedores"], D("500"))

    def test_departamentos(self):
        deps = {d["nombre"]: d for d in calcular_resumen(2026, hoy=HOY)["departamentos"]}
        self.assertEqual(deps["Cocina"]["proveedores"], D("1000"))   # base sin IVA
        self.assertEqual(deps[ACREEDORES]["acreedores"], D("500"))    # acreedores: bloque sin departamento

    def test_filtro_de_mes(self):
        self.assertEqual(calcular_resumen(2026, mes=4, hoy=HOY)["totales"]["gastos"], D("0"))
        self.assertEqual(calcular_resumen(2026, mes=5, hoy=HOY)["totales"]["gastos"], D("1500"))  # base 1000 + 500

    def test_bar_y_comedor_trimestral_se_reparte(self):
        ApunteIva.objects.create(libro="repercutido", fecha=date(2026, 6, 30), documento="3006",
                                 concepto="N/FRA BAR", cuenta="430000000", cuota=D("300"))
        r = calcular_resumen(2026, hoy=HOY)
        self.assertEqual(r["totales"]["bar"], D("3000"))          # base = cuota / 10 %
        self.assertEqual(r["mensual"]["bar"][3], D("1000"))        # abril, mayo y junio
        self.assertEqual(r["mensual"]["bar"][5], D("1000"))

    def test_extras_de_personal_por_departamento(self):
        ExtraPersonal.objects.create(anio=2026, mes=5, persona="Persona A", importe=D("400"))
        AsignacionDepartamento.objects.create(origen="personal", clave="Persona A", nombre="Persona A",
                                              departamento="Bar")
        r = calcular_resumen(2026, hoy=HOY)
        self.assertEqual(r["totales"]["extras_personal"], D("400"))
        deps = {d["nombre"]: d for d in r["departamentos"]}
        self.assertEqual(deps["Bar"]["nominas"], D("400"))

    def test_vista(self):
        resp = self.client.get(reverse("analisis:resumen"), {"anio": 2026, "mes": 5, "iva": "con"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Análisis de beneficios")
        self.assertContains(resp, "datos-graficos")


class NormalizarDepartamentoTests(TestCase):
    def test_variantes(self):
        self.assertEqual(normalizar_departamento("COCINA "), "Cocina")
        self.assertEqual(normalizar_departamento("recepcion"), "Recepción")
        self.assertEqual(normalizar_departamento(""), SIN_ASIGNAR)
        self.assertEqual(normalizar_departamento("pisos"), "Pisos")


class ImportarAcreedoresTests(TestCase):
    def _csv(self, contenido):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig")
        f.write(contenido)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return Path(f.name)

    def test_importa_y_crea_asignaciones(self):
        ruta = self._csv("fichero;acreedor;fecha;documento;cuenta;importe;asiento;linea\n"
                         "luz;LUZ SA;05/05/2026;F1;628000001;1.210,50;10;1\n"
                         "luz;LUZ SA;06/05/2026;A1;628000001;-10,50;11;1\n")
        r = importar_acreedores(ruta)
        self.assertEqual(r["leidas"], 2)
        self.assertEqual(sum(f.importe for f in FacturaAcreedor.objects.all()), D("1200"))
        self.assertTrue(AsignacionDepartamento.objects.filter(origen="acreedor", clave="luz").exists())
        # Reimportar reemplaza, no duplica
        importar_acreedores(ruta)
        self.assertEqual(FacturaAcreedor.objects.count(), 2)

    def test_csv_antiguo_da_error_claro(self):
        ruta = self._csv("fichero;acreedor;fecha;documento;tipo;concepto;contrapartida;debe;haber;asiento;linea\n")
        with self.assertRaises(ErrorImportacion):
            importar_acreedores(ruta)


class ImportarExtrasTests(TestCase):
    def test_importa_y_propone_departamento(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig")
        f.write("anio;mes;persona;departamento;importe\n2026;5;Persona A;Cocina;400,00\n2026;6;Persona A;Cocina;250,50\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        r = importar_extras(Path(f.name))
        self.assertEqual(r["leidas"], 2)
        a = AsignacionDepartamento.objects.get(origen="personal", clave="Persona A")
        self.assertEqual(a.departamento, "Cocina")
        # Un cambio hecho en el admin no se pisa al reimportar
        a.departamento = "Bar"
        a.save()
        importar_extras(Path(f.name))
        a.refresh_from_db()
        self.assertEqual(a.departamento, "Bar")


class GastoAdicionalYCanalesTests(TestCase):
    def test_alquiler_se_reparte_y_respeta_el_previsto(self):
        GastoAdicional.objects.create(concepto="Alquiler", cuenta="621", base=D("1200"), iva=D("252"),
                                      desde=date(2026, 1, 1), hasta=date(2026, 12, 31))
        hoy = date(2026, 6, 15)
        r = calcular_resumen(2026, hoy=hoy)
        self.assertAlmostEqual(float(r["totales"]["acreedores"]), 1200 * 181 / 365, places=2)  # ene–jun
        con = calcular_resumen(2026, hoy=hoy, incluir_previsto=True)
        self.assertAlmostEqual(float(con["totales"]["acreedores"]), 1200, places=2)
        self.assertEqual(con["cuentas_gasto"][0]["cuenta"], "621")

    def test_gasto_adicional_quita_el_aviso_de_su_iva(self):
        ApunteIva.objects.create(libro="soportado", fecha=date(2026, 5, 31), documento="1", asiento=99,
                                 concepto="S/FRA ARRENDADOR", cuenta="410000036", cuota=D("2100"))
        avisos = calcular_resumen(2026, hoy=HOY)["avisos"]
        self.assertTrue(any("ARRENDADOR" in a for a in avisos))
        GastoAdicional.objects.create(concepto="Alquiler", base=D("10000"), iva=D("2100"),
                                      desde=date(2026, 1, 1), hasta=date(2026, 12, 31), asiento_iva=99)
        avisos = calcular_resumen(2026, hoy=HOY)["avisos"]
        self.assertFalse(any("ARRENDADOR" in a for a in avisos))

    def test_peso_por_canal_principal(self):
        reserva(1, date(2026, 5, 1), date(2026, 5, 2), estancias="300", agencia="BOOKING")
        reserva(2, date(2026, 5, 1), date(2026, 5, 2), estancias="100", agencia="111 Expedia Group Way W")
        reserva(3, date(2026, 5, 1), date(2026, 5, 2), estancias="100", agencia="Expedia Lodging Partner Services")
        reserva(4, date(2026, 5, 1), date(2026, 5, 2), estancias="999", agencia="AGENCIA PEQUEÑA")
        p = calcular_resumen(2026, hoy=HOY)["pesos_canales"]
        self.assertAlmostEqual(p["ingresos"]["Booking"]["pesos"][4], 60.0)
        self.assertAlmostEqual(p["ingresos"]["Expedia"]["pesos"][4], 40.0)   # las dos de Expedia juntas
        self.assertIsNone(p["ingresos"]["Booking"]["pesos"][3])             # abril sin reservas
        self.assertEqual(p["noches"]["Expedia"]["total"], 2)


class DepartamentosYCategoriasTests(TestCase):
    def setUp(self):
        for numero, cod, fecha in (("1", 7, date(2026, 5, 3)), ("2", 7, date(2026, 8, 3)), ("3", 8, date(2026, 5, 4))):
            CompraProveedor.objects.create(tipo="A", numero=numero, fecha=fecha, codigo_proveedor=cod,
                                           proveedor=f"PROVEEDOR {cod}", bruto=D("100"), total=D("110"))
        # El albarán 1 está en el listado de Cocina; el 2 es posterior (sin listado)
        DepartamentoAlbaran.objects.create(tipo="A", numero="1", codigo_proveedor=7, fecha=date(2026, 5, 3),
                                           total=D("110"), departamento="Cocina")

    def test_departamento_por_albaran_y_habitual(self):
        deps = {d["nombre"]: d for d in calcular_resumen(2026, hoy=HOY, incluir_previsto=True)["departamentos"]}
        self.assertEqual(deps["Cocina"]["proveedores"], D("200"))      # albarán 1 + albarán 2 (habitual)
        self.assertEqual(deps[SIN_ASIGNAR]["proveedores"], D("100"))   # proveedor 8 sin listado ni asignación

    def test_categoria_y_fichas_incompletas(self):
        Tercero.objects.create(cuenta="400000007", nombre="PROVEEDOR 7", tipo="proveedor", categoria="Comida")
        r = calcular_resumen(2026, hoy=date(2026, 12, 31))
        cats = {c["nombre"]: c for c in r["categorias"]}
        self.assertEqual(cats["Comida"]["proveedores"], D("200"))
        self.assertEqual(cats["Otros"]["proveedores"], D("100"))
        incompletos = {f["nombre"] for f in r["revision"]["incompletos"]}
        self.assertEqual(incompletos, {"PROVEEDOR 8"})

    def test_acreedor_se_relaciona_por_cuenta_del_iva_o_por_nombre(self):
        Tercero.objects.create(cuenta="410000217", nombre="Iberdrola Clientes SAU", tipo="acreedor", categoria="Luz")
        Tercero.objects.create(cuenta="410000099", nombre="BOOKING.COM BV", tipo="acreedor", categoria="Agencias")
        FacturaAcreedor.objects.create(fichero="iberdrola", acreedor="Otra razón social", fecha=date(2026, 5, 5),
                                       documento="F1", asiento=10, cuenta="628000001", importe=D("121"))
        ApunteIva.objects.create(libro="soportado", fecha=date(2026, 5, 5), documento="F1", asiento=10,
                                 concepto="S/FRA X", cuenta="410000217", cuota=D("21"))
        FacturaAcreedor.objects.create(fichero="booking", acreedor="BOOKING.COM B.V.", fecha=date(2026, 5, 6),
                                       cuenta="629000004", importe=D("50"))
        cats = {c["nombre"]: c for c in calcular_resumen(2026, hoy=HOY)["categorias"]}
        self.assertEqual(cats["Luz"]["acreedores"], D("100"))
        self.assertEqual(cats["Agencias"]["acreedores"], D("50"))

    def test_vistas(self):
        resp = self.client.get(reverse("analisis:resumen"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'class="avisos"')          # los avisos ya no van en el panel
        self.assertContains(resp, "Total 5 canales")
        self.assertEqual(self.client.get(reverse("analisis:revision")).status_code, 200)


class ImportarDepartamentosYTercerosTests(TestCase):
    CABECERA = ",NUMERO,FECHA,PROV.,NOMBRE COMERCIAL,N.I.F.,BRUTO,DTO P.P.,POR.+GTOS.,IVA/REC.,I.R.P.F.,TOTAL,,C.D.\n"

    def test_nombre_de_fichero(self):
        self.assertEqual(_departamento_de_fichero("albaranes_cocina_.csv"), "Cocina")
        self.assertEqual(_departamento_de_fichero("albaranes_bar_.csv"), "Bar")
        self.assertEqual(_departamento_de_fichero("albaranes_almacencentral_.csv"), "Almacén central")
        self.assertEqual(_departamento_de_fichero("albaranes_lavanderia_.csv"), "Lavandería")
        self.assertIsNone(_departamento_de_fichero("albaranes_facturas.csv"))

    def test_importa_carpeta(self):
        carpeta = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in carpeta.iterdir()] and carpeta.rmdir())
        (carpeta / "albaranes_cocina_.csv").write_text(
            self.CABECERA + 'A, - 100,03.05.2026,7,PROVEEDOR X,B000,"1.000,50","0,00","0,00","100,05","0,00","1.100,55",NOR,\n',
            encoding="utf-8")
        (carpeta / "albaranes_facturas.csv").write_text(self.CABECERA, encoding="utf-8")
        r = importar_departamentos_albaranes(carpeta)
        self.assertEqual(r["leidas"], 1)
        d = DepartamentoAlbaran.objects.get()
        self.assertEqual((d.numero, d.codigo_proveedor, d.departamento, d.total), ("100", 7, "Cocina", D("1100.55")))

    def test_importa_terceros(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        f.write('400000001,PROVEEDOR UNO,B1,9,0,4000,,Comida,"Cocina, Bar"\n410000002,ACREEDOR DOS,,9,0,4100,,,\n')
        f.close()
        self.addCleanup(Path(f.name).unlink)
        importar_terceros(Path(f.name))
        uno, dos = Tercero.objects.get(cuenta="400000001"), Tercero.objects.get(cuenta="410000002")
        self.assertEqual((uno.tipo, uno.categoria, uno.departamentos), ("proveedor", "Comida", "Cocina, Bar"))
        self.assertEqual((dos.tipo, dos.categoria), ("acreedor", ""))


class AgrupacionYCorreccionesTests(TestCase):
    def test_almacen_y_lavanderia_van_a_pisos(self):
        self.assertEqual(normalizar_departamento("Lavandería"), "Pisos")
        self.assertEqual(normalizar_departamento("ALMACEN CENTRAL"), "Pisos")
        CompraProveedor.objects.create(tipo="A", numero="9", fecha=date(2026, 5, 3), codigo_proveedor=5,
                                       proveedor="P", bruto=D("80"), total=D("88"))
        DepartamentoAlbaran.objects.create(tipo="A", numero="9", codigo_proveedor=5, fecha=date(2026, 5, 3),
                                           total=D("88"), departamento="Lavandería")
        deps = {d["nombre"]: d for d in calcular_resumen(2026, hoy=HOY)["departamentos"]}
        self.assertEqual(deps["Pisos"]["proveedores"], D("80"))
        self.assertNotIn("Lavandería", deps)

    def test_reimportar_terceros_conserva_categoria_manual(self):
        Tercero.objects.update_or_create(cuenta="410000777", defaults={
            "nombre": "ACREEDOR X", "tipo": "acreedor", "categoria": "Agua"})
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        f.write("410000777,ACREEDOR X SL,,9,0,4100,,,\n400000001,PROVEEDOR,,9,0,4000,,Comida,\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        importar_terceros(Path(f.name))
        t = Tercero.objects.get(cuenta="410000777")
        self.assertEqual((t.nombre, t.categoria), ("ACREEDOR X SL", "Agua"))

    def test_migracion_de_categorias_aplicada(self):
        self.assertEqual(Tercero.objects.get(cuenta="410000195").categoria, "Agua")


class IngresosDeCajasTests(TestCase):
    def _caja(self, mes, **importes):
        from cajas.models import CajaDia, CajaMes
        cm = CajaMes.objects.create(anio=2026, mes=mes)
        CajaDia.objects.create(caja_mes=cm, dia=1, **{k: D(v) for k, v in importes.items()})

    def test_bar_incluye_chill_out_y_no_suma_ecotasa_ni_facturas(self):
        self._caja(5, bar="1100", chill_out="220", comedor="550", menus="110",
                   parking_efectivo="60.5", parking_visa="60.5", ecotasa="99", facturas="999")
        r = calcular_resumen(2026, hoy=HOY, con_iva=True)
        t = r["totales"]
        self.assertEqual(r["fuente_restauracion"], "cajas")
        self.assertEqual((t["bar"], t["comedor"], t["menus"], t["parking"]),
                         (D("1320"), D("550"), D("110"), D("121")))
        self.assertEqual(t["ingresos"], D("2101"))           # sin ecotasa ni facturas de caja
        sin = calcular_resumen(2026, hoy=HOY)["totales"]
        self.assertAlmostEqual(float(sin["bar"]), 1200, places=6)      # / 1,10
        self.assertAlmostEqual(float(sin["parking"]), 100, places=6)   # / 1,21

    def test_menus_cuentan_como_ingreso_del_comedor(self):
        self._caja(5, comedor="110", menus="55")
        deps = {d["nombre"]: d for d in calcular_resumen(2026, hoy=HOY, con_iva=True)["departamentos"]}
        self.assertEqual(deps["Comedor"]["ingresos"], D("165"))

    def test_sin_cajas_se_usa_la_estimacion_del_iva(self):
        ApunteIva.objects.create(libro="repercutido", fecha=date(2026, 6, 30), documento="3006",
                                 concepto="N/FRA BAR", cuenta="430000000", cuota=D("300"))
        r = calcular_resumen(2026, hoy=HOY)
        self.assertEqual(r["fuente_restauracion"], "iva")
        self.assertEqual(r["totales"]["bar"], D("3000"))
