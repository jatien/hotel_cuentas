"""Importación de los Excel de cajas.

Formato esperado (una hoja por mes, p. ej. «Mayo 26_Tabla 1»):

    dia | Bar | Comedor | chill | Total | (vacía) | (G, sin nombre) | Ecotasa | Menús | fras | Park | Parkvis
    1   | ... |
    ...
    31  |
    Total

- Las columnas se localizan por el nombre de la cabecera, no por la posición.
- Total (E) no se guarda: se recalcula y solo sirve para comprobar que la
  lectura cuadra (si no cuadra, aviso). La columna G (sin nombre) se ignora.
- Se lee y valida la hoja entera antes de tocar la base de datos; si hay un
  error no se importa nada de esa hoja.

Las funciones se usan desde la vista de importación y desde el comando
`importar_cajas`.
"""

import calendar
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction
from python_calamine import CalamineWorkbook

from .models import CAMPOS_IMPORTE, MESES, PUNTOS_VENTA, CajaDia, CajaMes

CENT = Decimal("0.01")
TOLERANCIA = Decimal("0.05")

# cabecera normalizada -> campo del modelo
CABECERAS = {
    "dia": "dia",
    "bar": "bar",
    "comedor": "comedor",
    "chill": "chill_out",
    "chillout": "chill_out",
    "total": "_total_ventas",
    "ecotasa": "ecotasa",
    "menus": "menus",
    "menu": "menus",
    "fras": "facturas",
    "facturas": "facturas",
    "park": "parking_efectivo",
    "parking": "parking_efectivo",
    "parkefectivo": "parking_efectivo",
    "parkingefectivo": "parking_efectivo",
    "parkvis": "parking_visa",
    "parkvisa": "parking_visa",
    "parkingvisa": "parking_visa",
}
OBLIGATORIAS = ["dia"] + [c for c, _ in PUNTOS_VENTA]

_MESES_NORMALIZADOS = {unicodedata.normalize("NFKD", n).lower(): num for num, n in MESES}


class ErrorImportacion(Exception):
    """Error legible por el usuario; nunca debe llegar como página de error de Django."""


# ---------------------------------------------------------------- utilidades

def normaliza(texto) -> str:
    """'Menús ' -> 'menus'; 'Chill out' -> 'chillout'."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def a_decimal(valor) -> Decimal:
    if valor in ("", None):
        return Decimal("0")
    if isinstance(valor, bool):
        raise ValueError(f"importe no válido: {valor!r}")
    if isinstance(valor, (int, float)):
        return Decimal(repr(float(valor))).quantize(CENT)
    texto = str(valor).strip().replace("€", "").replace(" ", "")
    if texto in ("", "-"):
        return Decimal("0")
    if "," in texto:                       # formato español: 1.234,56
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto).quantize(CENT)
    except InvalidOperation:
        raise ValueError(f"importe no válido: {valor!r}")


def mes_de_hoja(nombre_hoja: str) -> int | None:
    """Número de mes si el nombre de la hoja empieza por un mes ('abril 26_Tabla 1' -> 4)."""
    n = normaliza(nombre_hoja)
    for nombre_mes, num in _MESES_NORMALIZADOS.items():
        if n.startswith(nombre_mes):
            return num
    return None


def anio_de_texto(texto: str) -> int | None:
    """Año a partir de '26' o '2026' en un nombre de hoja o de fichero."""
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", texto)
    if m:
        return int(m.group(1))
    m = re.search(r"(?<!\d)(\d{2})(?!\d)", texto)
    if m:
        return 2000 + int(m.group(1))
    return None


# ---------------------------------------------------------------- lectura de una hoja

def _localiza_columnas(filas: list[list]) -> tuple[int, dict[str, int]]:
    """Devuelve (índice de la fila de cabecera, {campo: columna})."""
    for i, fila in enumerate(filas[:15]):
        cabeceras = [normaliza(c) for c in fila]
        if "dia" not in cabeceras:
            continue
        columnas = {}
        for j, cab in enumerate(cabeceras):
            campo = CABECERAS.get(cab)
            if campo and campo not in columnas:
                columnas[campo] = j
        faltan = [c for c in OBLIGATORIAS if c not in columnas]
        if faltan:
            raise ErrorImportacion(f"faltan columnas en la cabecera: {', '.join(faltan)}")
        return i, columnas
    raise ErrorImportacion("no se encuentra la fila de cabecera (una celda con «dia»)")


def leer_hoja(filas: list[list], anio: int, mes: int) -> tuple[dict[int, dict], list[str]]:
    """Lee las filas de una hoja de cajas.

    Devuelve ({día: {campo: Decimal}}, avisos). Lanza ErrorImportacion con todos
    los errores encontrados si alguna fila no se puede leer.
    """
    fila_cab, columnas = _localiza_columnas(filas)
    dias_mes = calendar.monthrange(anio, mes)[1]
    faltan_opcionales = [c for c in CAMPOS_IMPORTE if c not in columnas]

    dias: dict[int, dict] = {}
    errores: list[str] = []
    avisos: list[str] = []
    fila_total = None

    def celda(fila, campo):
        j = columnas.get(campo)
        return fila[j] if j is not None and j < len(fila) else None

    for n, fila in enumerate(filas[fila_cab + 1:], start=fila_cab + 2):   # n = fila de Excel
        valor_dia = celda(fila, "dia")
        if normaliza(valor_dia) == "total":
            fila_total = fila
            break
        if valor_dia in ("", None):
            continue
        try:
            dia = int(float(valor_dia))
        except (TypeError, ValueError):
            errores.append(f"fila {n}: día no válido ({valor_dia!r})")
            continue

        importes = {}
        for campo in CAMPOS_IMPORTE:
            try:
                importes[campo] = a_decimal(celda(fila, campo))
            except ValueError as e:
                errores.append(f"fila {n}, {campo}: {e}")

        if not 1 <= dia <= 31:
            errores.append(f"fila {n}: día fuera de rango ({dia})")
            continue
        if dia > dias_mes:
            if any(importes.values()):
                errores.append(f"fila {n}: el día {dia} no existe en el mes y tiene importes")
            continue
        if dia in dias:
            errores.append(f"fila {n}: el día {dia} está repetido")
            continue
        dias[dia] = importes

        # Comprobación contra el Total (bar + comedor + chill) del propio Excel (solo aviso)
        if len(importes) == len(CAMPOS_IMPORTE) and "_total_ventas" in columnas:
            try:
                en_excel = a_decimal(celda(fila, "_total_ventas"))
            except ValueError:
                en_excel = None
            calculado = sum((importes[c] for c, _ in PUNTOS_VENTA), Decimal("0"))
            if en_excel is not None and abs(en_excel - calculado) > TOLERANCIA:
                avisos.append(f"día {dia}: Total del Excel {en_excel} ≠ suma {calculado}")

    if errores:
        detalle = "\n".join(errores[:25])
        extra = f"\n... y {len(errores) - 25} más" if len(errores) > 25 else ""
        raise ErrorImportacion(f"{len(errores)} errores, no se importa nada:\n{detalle}{extra}")

    if fila_total is not None and dias:
        for campo in CAMPOS_IMPORTE:
            try:
                en_excel = a_decimal(celda(fila_total, campo))
            except ValueError:
                continue
            suma = sum((d[campo] for d in dias.values()), Decimal("0"))
            if campo in columnas and abs(en_excel - suma) > TOLERANCIA:
                avisos.append(f"fila Total, {campo}: Excel {en_excel} ≠ suma de días {suma}")

    if faltan_opcionales and dias:
        avisos.append("columnas no encontradas (se ponen a 0): " + ", ".join(faltan_opcionales))
    return dias, avisos


# ---------------------------------------------------------------- guardado

def guardar_mes(anio: int, mes: int, dias: dict[int, dict], origen: str = "") -> CajaMes:
    """Reemplaza los días del mes por los leídos (delete + bulk_create en una transacción).

    Se crean todas las filas del mes, también las de días sin datos, para que
    el mes quede completo al editarlo a mano.
    """
    with transaction.atomic():
        caja_mes, _ = CajaMes.objects.get_or_create(anio=anio, mes=mes)
        if origen:
            caja_mes.origen = origen[:200]
            caja_mes.save(update_fields=["origen", "actualizado"])
        caja_mes.dias.all().delete()
        CajaDia.objects.bulk_create([
            CajaDia(caja_mes=caja_mes, dia=d, **dias.get(d, {}))
            for d in range(1, caja_mes.dias_del_mes + 1)
        ])
    return caja_mes


# ---------------------------------------------------------------- libro completo

def abrir_libro(archivo) -> CalamineWorkbook:
    """Acepta una ruta o un fichero subido (UploadedFile / file-like)."""
    try:
        if isinstance(archivo, (str, Path)):
            return CalamineWorkbook.from_path(str(archivo))
        archivo.seek(0)
        return CalamineWorkbook.from_filelike(io.BytesIO(archivo.read()))
    except Exception as e:
        raise ErrorImportacion(f"no se puede leer el fichero ({e})")


def hojas_mensuales(libro: CalamineWorkbook) -> list[tuple[str, int]]:
    """[(nombre de hoja, mes)] de las hojas cuyo nombre empieza por un mes."""
    return [(h, m) for h in libro.sheet_names if (m := mes_de_hoja(h))]


def importar_libro(archivo, nombre_fichero: str, anio: int | None = None, mes: int | None = None,
                   reemplazar: bool = False) -> list[dict]:
    """Importa un mes (si se indica `mes`) o todas las hojas mensuales con datos.

    Devuelve una lista de resultados, uno por hoja:
    {hoja, anio, mes, estado ('importado'|'omitido'|'vacio'|'error'), dias, avisos, error}
    Si `reemplazar` es False, los meses que ya existen en la base de datos se omiten.
    """
    libro = abrir_libro(archivo)
    hojas = hojas_mensuales(libro)
    if mes:
        hojas = [(h, m) for h, m in hojas if m == mes]
        if not hojas:
            nombres = ", ".join(libro.sheet_names) or "ninguna"
            raise ErrorImportacion(
                f"no hay ninguna hoja de {dict(MESES)[mes]} en {nombre_fichero} (hojas: {nombres})"
            )
    elif not hojas:
        raise ErrorImportacion(f"{nombre_fichero}: ninguna hoja tiene nombre de mes («Mayo 26», «abril 26»...)")

    resultados = []
    for hoja, mes_hoja in hojas:
        anio_hoja = anio or anio_de_texto(hoja) or anio_de_texto(Path(nombre_fichero).stem)
        res = {"hoja": hoja, "anio": anio_hoja, "mes": mes_hoja, "estado": "", "dias": 0,
               "avisos": [], "error": ""}
        resultados.append(res)
        if not anio_hoja:
            res.update(estado="error", error="no se puede deducir el año; indícalo en el formulario")
            continue
        try:
            filas = libro.get_sheet_by_name(hoja).to_python()
            dias, avisos = leer_hoja(filas, anio_hoja, mes_hoja)
        except ErrorImportacion as e:
            res.update(estado="error", error=str(e))
            continue
        except Exception as e:  # hoja corrupta u otro fallo de lectura
            res.update(estado="error", error=f"no se puede leer la hoja ({e})")
            continue

        con_datos = sum(1 for d in dias.values() if any(d.values()))
        res.update(avisos=avisos, dias=con_datos)
        if not con_datos:
            res["estado"] = "vacio"
            continue
        if not reemplazar and CajaMes.objects.filter(anio=anio_hoja, mes=mes_hoja).exists():
            res["estado"] = "omitido"
            continue
        guardar_mes(anio_hoja, mes_hoja, dias, origen=f"{nombre_fichero} · {hoja}")
        res["estado"] = "importado"
    return resultados
