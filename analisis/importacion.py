"""Importación de los ficheros de análisis a la base de datos.

Funciones de servicio reutilizables desde el comando `importar_analisis` y,
más adelante, desde una vista de subida de ficheros.

Patrón común: se lee y valida TODO el fichero antes de tocar la base de datos.
Si hay un solo error no se importa nada; si todo cuadra, se reemplazan los
datos en una transacción (delete + bulk_create).
"""

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction
from python_calamine import CalamineWorkbook

from .models import CompraProveedor, MovimientoAcreedor, Reserva

CENT = Decimal("0.01")
TOLERANCIA = Decimal("0.05")


class ErrorImportacion(Exception):
    """Error legible por el usuario; nunca debe llegar como página de error de Django."""


# ---------------------------------------------------------------- lectura

def leer_hoja(ruta: Path) -> list[list]:
    """Primera hoja de un .xlsx/.xls como lista de filas (fechas como date, números como float).

    Se usa python-calamine en vez de openpyxl porque algunos exports de la
    contabilidad tienen un styles.xml defectuoso que openpyxl no abre.
    """
    try:
        return CalamineWorkbook.from_path(str(ruta)).get_sheet_by_index(0).to_python()
    except Exception as e:
        raise ErrorImportacion(f"{ruta.name}: no se puede leer el fichero ({e})")


def a_decimal(valor) -> Decimal:
    if valor in ("", None):
        return Decimal("0")
    if isinstance(valor, (int, float)):
        return Decimal(repr(float(valor))).quantize(CENT)
    texto = str(valor).strip().replace("€", "").replace(" ", "")
    if "," in texto:                       # formato español: 1.234,56
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto).quantize(CENT)
    except InvalidOperation:
        raise ValueError(f"importe no válido: {valor!r}")


def a_fecha(valor) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"fecha no válida: {valor!r}")


def a_entero(valor, *, nulo=False):
    if valor in ("", None):
        if nulo:
            return None
        return 0
    try:
        return int(float(valor))
    except ValueError:
        raise ValueError(f"número entero no válido: {valor!r}")


def a_texto(valor) -> str:
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return " ".join(str(valor or "").split())


def _normaliza_cabecera(fila) -> list[str]:
    return [" ".join(str(c).split()) for c in fila]


def _reemplazar(modelo, objetos, filtro: dict) -> int:
    with transaction.atomic():
        borrados, _ = modelo.objects.filter(**filtro).delete()
        modelo.objects.bulk_create(objetos, batch_size=1000)
    return borrados


def _fallar_si_errores(nombre: str, errores: list[str]):
    if errores:
        detalle = "\n  ".join(errores[:25])
        extra = f"\n  ... y {len(errores) - 25} más" if len(errores) > 25 else ""
        raise ErrorImportacion(f"{nombre}: {len(errores)} errores, no se importa nada\n  {detalle}{extra}")


# ---------------------------------------------------------------- reservas

# El export repite nombres de columna («€ Estancias» facturado y pendiente),
# así que se lee por posición y se comprueba que la cabecera es la esperada.
CABECERA_RESERVAS = [
    "Reserva", "Fecha Reserva", "Agencia", "Age", "Tarifa", "NR", "Reserva Agencia",
    "Estado", "Nombre", "Grupo", "Entrada", "Salida", "Hab.", "Ad", "Ni", "Cu",
    "Depósito", "Oferta", "€ Estancias", "€ Extras", "€ Impuesto", "€ Total",
    "€ Estancias", "€ Extras", "€ Tasas", "€ Total", "Total Estancias",
    "Total Garantías", "Facturas",
]


def _separar_agencia(texto: str) -> tuple[str, str]:
    """«08/05 - SIDETOURS S.L.» -> («08/05», «SIDETOURS S.L.»)."""
    codigo, sep, nombre = texto.partition(" - ")
    return (codigo.strip(), nombre.strip()) if sep else ("", texto.strip())


def importar_reservas(ruta: Path) -> dict:
    filas = leer_hoja(ruta)
    if not filas:
        raise ErrorImportacion(f"{ruta.name}: fichero vacío")
    cabecera = _normaliza_cabecera(filas[0][:len(CABECERA_RESERVAS)])
    if cabecera != CABECERA_RESERVAS:
        raise ErrorImportacion(f"{ruta.name}: la cabecera no es la del export de reservas.\n"
                               f"  Esperada: {CABECERA_RESERVAS}\n  Recibida: {cabecera}")

    reservas, errores, fila_total = [], [], None
    for n, f in enumerate(filas[1:], start=2):
        if str(f[0]).strip().lower() == "total":
            fila_total = f
            continue
        if f[0] in ("", None):
            continue
        try:
            codigo, agencia = _separar_agencia(a_texto(f[2]))
            reservas.append(Reserva(
                numero=a_entero(f[0]),
                fecha_reserva=a_fecha(f[1]),
                agencia_codigo=codigo,
                agencia=agencia,
                tarifa=a_texto(f[4]),
                no_reembolsable=bool(a_entero(f[5])),
                ref_agencia=a_texto(f[6]),
                estado=a_texto(f[7]),
                # f[8] Nombre del huésped: no se guarda, no hace falta para el análisis
                grupo=a_texto(f[9]),
                entrada=a_fecha(f[10]),
                salida=a_fecha(f[11]),
                habitaciones=a_entero(f[12]),
                adultos=a_entero(f[13]),
                ninos=a_entero(f[14]),
                cunas=a_entero(f[15]),
                deposito=a_decimal(f[16]),
                # f[17] Oferta: ruta de una imagen, se ignora
                fact_estancias=a_decimal(f[18]),
                fact_extras=a_decimal(f[19]),
                fact_impuesto=a_decimal(f[20]),
                fact_total=a_decimal(f[21]),
                pend_estancias=a_decimal(f[22]),
                pend_extras=a_decimal(f[23]),
                pend_tasas=a_decimal(f[24]),
                pend_total=a_decimal(f[25]),
                total_estancias=a_decimal(f[26]),
                facturas=a_texto(f[28])[:255],
            ))
        except (ValueError, IndexError) as e:
            errores.append(f"fila {n}: {e}")

    numeros = [r.numero for r in reservas]
    if len(numeros) != len(set(numeros)):
        errores.append("hay números de reserva repetidos en el fichero")

    # Control: la suma de «Total Estancias» debe coincidir con la fila Total del export
    if fila_total is not None and not errores:
        esperado = a_decimal(fila_total[26])
        calculado = sum((r.total_estancias for r in reservas), Decimal("0"))
        if abs(esperado - calculado) > TOLERANCIA:
            errores.append(f"Total Estancias no cuadra: fila Total {esperado}, suma {calculado}")
    _fallar_si_errores(ruta.name, errores)

    # Reemplaza solo las reservas que vienen en el fichero (por número):
    # así se pueden importar exports de periodos distintos sin perder nada.
    borradas = _reemplazar(Reserva, reservas, {"numero__in": numeros})
    return {"leidas": len(reservas), "reemplazadas": borradas,
            "control": fila_total is not None}


# ---------------------------------------------------------------- compras

COLUMNAS_COMPRAS = {
    "NUMERO": "numero", "FECHA": "fecha", "PROV.": "codigo_proveedor",
    "NOMBRE COMERCIAL": "proveedor", "N.I.F.": "nif", "BRUTO": "bruto",
    "DTO P.P.": "dto_pp", "POR.+GTOS.": "portes_gastos", "IVA/REC.": "iva",
    "I.R.P.F.": "irpf", "TOTAL": "total",
}
IMPORTES_COMPRAS = ("bruto", "dto_pp", "portes_gastos", "iva", "irpf", "total")


def importar_compras(ruta: Path) -> dict:
    """Importa el «Diario de compras» (albaranes «A» y facturas «F»)."""
    filas = leer_hoja(ruta)

    # La cabecera se repite en cada página del informe: se busca la primera
    idx = None
    for f in filas:
        cab = _normaliza_cabecera(f)
        if "NUMERO" in cab and "BRUTO" in cab:
            idx = {campo: cab.index(col) for col, campo in COLUMNAS_COMPRAS.items() if col in cab}
            break
    if idx is None:
        raise ErrorImportacion(f"{ruta.name}: no se encuentra la cabecera del diario de compras")
    faltan = [col for col, campo in COLUMNAS_COMPRAS.items() if campo not in idx]
    if faltan:
        raise ErrorImportacion(f"{ruta.name}: faltan columnas {', '.join(faltan)}")

    compras, errores, total_general = [], [], None
    for n, f in enumerate(filas, start=1):
        marca = a_texto(f[0]).upper()
        if marca == "TOTAL GENERAL:":
            total_general = f
            continue
        if marca not in ("A", "F"):
            continue                       # títulos, cabeceras, subtotales, cuadro de IVA
        try:
            datos = {campo: a_decimal(f[idx[campo]]) for campo in IMPORTES_COMPRAS}
            compra = CompraProveedor(
                tipo=marca,
                numero=a_texto(f[idx["numero"]]).lstrip(" -"),
                fecha=a_fecha(f[idx["fecha"]]),
                codigo_proveedor=a_entero(f[idx["codigo_proveedor"]]),
                proveedor=a_texto(f[idx["proveedor"]]),
                nif=a_texto(f[idx["nif"]]),
                **datos,
            )
        except (ValueError, IndexError) as e:
            errores.append(f"fila {n}: {e}")
            continue
        calculado = compra.base + compra.iva - compra.irpf
        if abs(calculado - compra.total) > CENT:
            errores.append(f"fila {n}: el total {compra.total} no cuadra con base+IVA-IRPF ({calculado})")
        compras.append(compra)

    if not compras:
        errores.append("no se ha encontrado ninguna línea de albarán o factura")

    # Control: las sumas deben coincidir con la fila TOTAL GENERAL del informe
    if total_general is not None and not errores:
        for campo in ("bruto", "iva", "total"):
            esperado = a_decimal(total_general[idx[campo]])
            calculado = sum((getattr(c, campo) for c in compras), Decimal("0"))
            if abs(esperado - calculado) > TOLERANCIA:
                errores.append(f"{campo} no cuadra: TOTAL GENERAL {esperado}, suma {calculado}")
    _fallar_si_errores(ruta.name, errores)

    años = sorted({c.fecha.year for c in compras})
    borradas = _reemplazar(CompraProveedor, compras, {"fecha__year__in": años})
    return {"leidas": len(compras), "reemplazadas": borradas, "años": años,
            "control": total_general is not None}


# ---------------------------------------------------------------- acreedores

COLUMNAS_ACREEDORES = ["fichero", "acreedor", "fecha", "documento", "tipo", "concepto",
                       "contrapartida", "debe", "haber", "asiento", "linea"]


def importar_acreedores(ruta: Path) -> dict:
    """Importa el CSV generado por scripts/unir_acreedores.py."""
    try:
        with ruta.open(encoding="utf-8-sig", newline="") as fh:
            filas = list(csv.DictReader(fh, delimiter=";"))
    except (OSError, UnicodeDecodeError) as e:
        raise ErrorImportacion(f"{ruta.name}: no se puede leer ({e})")
    if not filas:
        raise ErrorImportacion(f"{ruta.name}: fichero vacío")
    faltan = [c for c in COLUMNAS_ACREEDORES if c not in filas[0]]
    if faltan:
        raise ErrorImportacion(f"{ruta.name}: faltan columnas {', '.join(faltan)}")

    tipos = set(MovimientoAcreedor.Tipo.values)
    movimientos, errores = [], []
    for n, f in enumerate(filas, start=2):
        try:
            if f["tipo"] not in tipos:
                raise ValueError(f"tipo desconocido: {f['tipo']!r}")
            movimientos.append(MovimientoAcreedor(
                fichero=f["fichero"].strip(),
                acreedor=f["acreedor"].strip(),
                fecha=a_fecha(f["fecha"]),
                documento=f["documento"].strip(),
                tipo=f["tipo"],
                concepto=f["concepto"].strip()[:255],
                contrapartida=f["contrapartida"].strip(),
                debe=a_decimal(f["debe"]),
                haber=a_decimal(f["haber"]),
                asiento=a_entero(f["asiento"], nulo=True),
                linea=a_entero(f["linea"], nulo=True),
            ))
        except ValueError as e:
            errores.append(f"fila {n}: {e}")
    _fallar_si_errores(ruta.name, errores)

    años = sorted({m.fecha.year for m in movimientos})
    borrados = _reemplazar(MovimientoAcreedor, movimientos, {"fecha__year__in": años})
    return {"leidas": len(movimientos), "reemplazadas": borrados, "años": años}


IMPORTADORES = {
    "reservas": importar_reservas,
    "compras": importar_compras,
    "acreedores": importar_acreedores,
}
