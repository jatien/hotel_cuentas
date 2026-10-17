"""Tests de la app cajas. Todos los datos son ficticios."""

import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .importacion import ErrorImportacion, a_decimal, anio_de_texto, guardar_mes, importar_libro, leer_hoja, mes_de_hoja
from .models import CajaDia, CajaMes
from .resumen import resumen_anual

CABECERA = ["dia", "Bar", "Comedor", "chill", "Total", "", "", "Ecotasa", "Menús", "fras", "Park", "Parkvis"]


def fila(dia, bar="", comedor="", chill="", eco="", menus="", fras="", park="", parkvis="", total=None, g=None):
    num = lambda v: v if v != "" else 0  # noqa: E731
    if total is None:
        total = num(bar) + num(comedor) + num(chill)
    if g is None:
        g = num(eco) + num(menus) + num(fras) + num(park)
    return [dia, bar, comedor, chill, total, "", g, eco, menus, fras, park, parkvis]


def hoja_basica(dias_mes=30):
    filas = [CABECERA, fila(1, 100.5, 50, "", 10, "", 5, 2, 4), fila(2, 200, "", 30.25, "", 25, "", "", 8)]
    filas += [fila(d) for d in range(3, 32)]
    filas.append(["Total", 300.5, 50, 30.25, 380.75, 0, 42, 10, 25, 5, 2, 12])
    return filas


class UtilidadesTests(TestCase):
    def test_a_decimal(self):
        self.assertEqual(a_decimal(161.45), Decimal("161.45"))
        self.assertEqual(a_decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(a_decimal(""), Decimal("0"))
        with self.assertRaises(ValueError):
            a_decimal("abc")

    def test_mes_y_anio_de_hoja(self):
        self.assertEqual(mes_de_hoja("abril 26_Tabla 1"), 4)
        self.assertEqual(mes_de_hoja("Septiembre 26_Tabla 1"), 9)
        self.assertIsNone(mes_de_hoja("cuadre cajas_Tabla 1"))
        self.assertEqual(anio_de_texto("Mayo 26_Tabla 1"), 2026)
        self.assertEqual(anio_de_texto("cajas_2025"), 2025)


class LeerHojaTests(TestCase):
    def test_lee_importes_y_sin_avisos(self):
        dias, avisos = leer_hoja(hoja_basica(), 2026, 4)
        self.assertEqual(avisos, [])
        self.assertEqual(len(dias), 30)   # el 31 de abril se ignora porque está vacío
        self.assertEqual(dias[1]["bar"], Decimal("100.50"))
        self.assertEqual(dias[2]["chill_out"], Decimal("30.25"))
        self.assertEqual(dias[2]["menus"], Decimal("25"))
        self.assertEqual(dias[1]["facturas"], Decimal("5"))
        self.assertEqual(dias[1]["parking_efectivo"], Decimal("2"))
        self.assertEqual(dias[2]["parking_visa"], Decimal("8"))

    def test_aviso_si_columna_g_no_cuadra(self):
        filas = hoja_basica()
        filas[1] = fila(1, 100.5, 50, "", 10, "", 5, 2, 4, g=999)
        _, avisos = leer_hoja(filas, 2026, 4)
        self.assertTrue(any("columna G" in a for a in avisos))

    def test_error_si_importe_no_valido(self):
        filas = hoja_basica()
        filas[1] = fila(1, "mucho", total=0)
        with self.assertRaises(ErrorImportacion):
            leer_hoja(filas, 2026, 4)

    def test_error_si_dia_inexistente_con_datos(self):
        filas = hoja_basica()
        filas[31] = fila(31, 10)          # 31 de abril con importe
        with self.assertRaises(ErrorImportacion):
            leer_hoja(filas, 2026, 4)

    def test_error_sin_cabecera(self):
        with self.assertRaises(ErrorImportacion):
            leer_hoja([["a", "b"], [1, 2]], 2026, 4)


class GuardarYResumenTests(TestCase):
    def test_guardar_crea_todos_los_dias_y_reemplaza(self):
        dias, _ = leer_hoja(hoja_basica(), 2026, 4)
        cm = guardar_mes(2026, 4, dias, origen="prueba.xlsx · abril")
        self.assertEqual(cm.dias.count(), 30)
        guardar_mes(2026, 4, {1: {"bar": Decimal("1")}})
        self.assertEqual(CajaMes.objects.count(), 1)
        self.assertEqual(CajaDia.objects.get(caja_mes=cm, dia=1).bar, Decimal("1"))
        self.assertEqual(CajaDia.objects.get(caja_mes=cm, dia=2).bar, Decimal("0"))

    def test_resumen_anual(self):
        dias, _ = leer_hoja(hoja_basica(), 2026, 4)
        guardar_mes(2026, 4, dias)
        guardar_mes(2026, 5, {1: {"bar": Decimal("10"), "ecotasa": Decimal("1"), "parking_visa": Decimal("3")}})
        r = resumen_anual(2026)
        self.assertEqual([f["mes"] for f in r["filas"]], [4, 5])
        self.assertEqual(r["filas"][0]["total_ventas"], Decimal("380.75"))
        self.assertEqual(r["filas"][0]["total_cobros"], Decimal("42"))   # parking visa fuera
        self.assertEqual(r["total"]["bar"], Decimal("310.50"))
        self.assertEqual(r["total"]["parking_visa"], Decimal("15"))


def _xlsx(hojas: dict[str, list[list]]) -> bytes:
    try:
        import openpyxl
    except ImportError:  # pragma: no cover
        return b""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for nombre, filas in hojas.items():
        ws = wb.create_sheet(nombre)
        for f in filas:
            ws.append([None if v == "" else v for v in f])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ImportarLibroTests(TestCase):
    def setUp(self):
        self.contenido = _xlsx({
            "abril 26_Tabla 1": hoja_basica(),
            "Mayo 26_Tabla 1": [CABECERA, fila(1, 10, 20, 30)],
            "Junio 26_Tabla 1": [CABECERA] + [fila(d) for d in range(1, 31)],
            "cuadre cajas_Tabla 1": [["CAJA", "día"], [500, ""]],
        })
        if not self.contenido:
            self.skipTest("openpyxl no instalado")

    def _subido(self):
        return SimpleUploadedFile("cajas_26.xlsx", self.contenido)

    def test_importa_todas_las_hojas_mensuales(self):
        res = importar_libro(self._subido(), "cajas_26.xlsx")
        estados = {r["mes"]: r["estado"] for r in res}
        self.assertEqual(estados, {4: "importado", 5: "importado", 6: "vacio"})
        self.assertEqual(CajaMes.objects.get(anio=2026, mes=5).dias.count(), 31)

    def test_un_solo_mes_y_no_reemplaza_sin_permiso(self):
        importar_libro(self._subido(), "cajas_26.xlsx", mes=5)
        self.assertEqual(list(CajaMes.objects.values_list("mes", flat=True)), [5])
        dia = CajaDia.objects.get(caja_mes__mes=5, dia=1)
        dia.bar = Decimal("999")
        dia.save()
        res = importar_libro(self._subido(), "cajas_26.xlsx", mes=5)
        self.assertEqual(res[0]["estado"], "omitido")
        self.assertEqual(CajaDia.objects.get(caja_mes__mes=5, dia=1).bar, Decimal("999"))
        importar_libro(self._subido(), "cajas_26.xlsx", mes=5, reemplazar=True)
        self.assertEqual(CajaDia.objects.get(caja_mes__mes=5, dia=1).bar, Decimal("10"))

    def test_mes_sin_hoja(self):
        with self.assertRaises(ErrorImportacion):
            importar_libro(self._subido(), "cajas_26.xlsx", mes=12)

    def test_vista_importar(self):
        r = self.client.post(reverse("cajas:importar"), {"archivo": self._subido(), "mes": "4", "anio": ""})
        self.assertRedirects(r, reverse("cajas:detalle", args=[2026, 4]))


class VistasTests(TestCase):
    def setUp(self):
        dias, _ = leer_hoja(hoja_basica(), 2026, 4)
        guardar_mes(2026, 4, dias)

    def test_resumen_y_detalle(self):
        r = self.client.get(reverse("cajas:resumen"))
        self.assertContains(r, "Abril")
        self.assertContains(r, "380,75 €")
        r = self.client.get(reverse("cajas:detalle", args=[2026, 4]))
        self.assertContains(r, "Chill out")
        self.assertContains(r, "42,00 €")

    def test_nuevo_mes(self):
        r = self.client.post(reverse("cajas:nuevo"), {"anio": 2026, "mes": 2})
        self.assertRedirects(r, reverse("cajas:editar", args=[2026, 2]))
        self.assertEqual(CajaMes.objects.get(anio=2026, mes=2).dias.count(), 28)

    def test_editar_acepta_coma_decimal(self):
        cm = CajaMes.objects.get(anio=2026, mes=4)
        r = self.client.get(reverse("cajas:editar", args=[2026, 4]))
        self.assertEqual(r.status_code, 200)
        datos = {"form-TOTAL_FORMS": "30", "form-INITIAL_FORMS": "30", "notas": "prueba"}
        for i, d in enumerate(cm.dias.all()):
            datos[f"form-{i}-id"] = d.id
        datos["form-0-bar"] = "1.234,56"
        datos["form-1-ecotasa"] = "7.5"
        r = self.client.post(reverse("cajas:editar", args=[2026, 4]), datos)
        self.assertRedirects(r, reverse("cajas:detalle", args=[2026, 4]))
        self.assertEqual(cm.dias.get(dia=1).bar, Decimal("1234.56"))
        self.assertEqual(cm.dias.get(dia=2).ecotasa, Decimal("7.50"))
        self.assertEqual(cm.dias.get(dia=1).comedor, Decimal("0"))   # casilla vacía = 0

    def test_editar_importe_no_valido(self):
        cm = CajaMes.objects.get(anio=2026, mes=4)
        datos = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-0-id": cm.dias.get(dia=1).id,
                 "form-0-bar": "abc"}
        r = self.client.post(reverse("cajas:editar", args=[2026, 4]), datos)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="abc"')

    def test_borrar(self):
        self.client.post(reverse("cajas:borrar", args=[2026, 4]))
        self.assertFalse(CajaMes.objects.exists())
