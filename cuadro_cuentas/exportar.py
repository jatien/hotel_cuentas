"""Exportación de tablas a CSV (separador «;» y coma decimal, como lo abre Excel en español)
y a Excel (.xlsx). Se exporta siempre el resultado completo del filtro, no la página visible."""
import csv
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from django.http import HttpResponse

FORMATOS = ("csv", "xlsx")


def formato_pedido(request):
    formato = request.GET.get("exportar", "")
    return formato if formato in FORMATOS else None


def _texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, Decimal):
        return f"{valor:.2f}".replace(".", ",")
    if isinstance(valor, (date, datetime)):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def exportar(formato, nombre, cabeceras, filas):
    """filas: iterable de listas con valores Python (Decimal, date, str...)."""
    if formato == "xlsx":
        return _xlsx(nombre, cabeceras, filas)
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}.csv"'
    respuesta.write("﻿")  # BOM: Excel reconoce los acentos
    escritor = csv.writer(respuesta, delimiter=";")
    escritor.writerow(cabeceras)
    for fila in filas:
        escritor.writerow([_texto(v) for v in fila])
    return respuesta


def _xlsx(nombre, cabeceras, filas):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    libro = Workbook()
    hoja = libro.active
    hoja.title = nombre[:31]
    hoja.append(cabeceras)
    for celda in hoja[1]:
        celda.font = Font(bold=True)
    for fila in filas:
        hoja.append([float(v) if isinstance(v, Decimal) else v for v in fila])
    for columna in hoja.iter_cols(min_row=2):
        for celda in columna:
            if isinstance(celda.value, float):
                celda.number_format = "#,##0.00"
            elif isinstance(celda.value, (date, datetime)):
                celda.number_format = "DD/MM/YYYY"
    hoja.freeze_panes = "A2"
    salida = BytesIO()
    libro.save(salida)
    respuesta = HttpResponse(salida.getvalue(),
                             content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}.xlsx"'
    return respuesta
