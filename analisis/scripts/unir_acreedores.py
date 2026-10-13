"""Une las facturas de los mayores de acreedores (un .xlsx por acreedor) en un único CSV.

Cada .xlsx es el mayor de la cuenta 410 de un acreedor, exportado desde la
contabilidad, con las columnas:
    Fecha | Documento | Concepto | Contrapartida | Debe | Haber | Saldo | Asiento | Linea

Uso (desde la raíz del proyecto):
    python analisis/scripts/unir_acreedores.py analisis/datos/2026/acreedores analisis/datos/2026/acreedores.csv

Solo se exportan las facturas recibidas (concepto «S/FRA ...»); los pagos,
cobros y aperturas se leen para validar pero no se escriben.

El script es independiente de Django: solo lee, valida y escribe el CSV.
Comprueba que el saldo acumulado de cada fichero cuadra línea a línea (con
todas las filas), para detectar filas perdidas o mal leídas antes de importar.
"""

import argparse
import csv
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from python_calamine import CalamineWorkbook

CABECERA_ESPERADA = ["Fecha", "Documento", "Concepto", "Contrapartida",
                     "Debe", "Haber", "Saldo", "Asiento", "Linea"]

COLUMNAS_CSV = ["fichero", "acreedor", "fecha", "documento", "cuenta", "importe",
                "asiento", "linea"]

# Prefijo del concepto -> tipo de movimiento
PREFIJOS = [
    ("S/FRA", "factura"),     # «su factura»: factura recibida del acreedor
    ("N/PAGO", "pago"),
    ("COBRO", "cobro"),
    ("APERTURA", "apertura"),
]

CENT = Decimal("0.01")


def a_decimal(valor) -> Decimal:
    if valor in ("", None):
        return Decimal("0")
    if isinstance(valor, (int, float)):
        return Decimal(repr(float(valor))).quantize(CENT)
    texto = str(valor).strip().replace(".", "").replace(",", ".")
    return Decimal(texto).quantize(CENT)


def a_fecha(valor) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return datetime.strptime(str(valor).strip(), "%d/%m/%Y").date()


def a_entero(valor) -> str:
    if valor in ("", None):
        return ""
    return str(int(float(valor)))


def texto(valor) -> str:
    """Documento y contrapartida a veces llegan como número (622000001.0)."""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor or "").strip()


def clasificar(concepto: str) -> tuple[str, str]:
    """Devuelve (tipo, nombre del acreedor sin el prefijo)."""
    for prefijo, tipo in PREFIJOS:
        if concepto.upper().startswith(prefijo):
            nombre = concepto[len(prefijo):].strip() if tipo in ("factura", "pago", "cobro") else ""
            return tipo, nombre
    return "otro", ""


def fmt(d: Decimal) -> str:
    """Decimal con coma, para que el CSV se abra bien en Excel en español."""
    return f"{d:.2f}".replace(".", ",")


def leer_fichero(ruta: Path) -> tuple[list[dict], list[str]]:
    filas = CalamineWorkbook.from_path(str(ruta)).get_sheet_by_index(0).to_python()
    if not filas:
        return [], [f"{ruta.name}: hoja vacía"]
    cabecera = [str(c).strip() for c in filas[0][:len(CABECERA_ESPERADA)]]
    if cabecera != CABECERA_ESPERADA:
        return [], [f"{ruta.name}: cabecera inesperada {cabecera}"]

    slug = ruta.stem.removeprefix("acr_").rsplit("_", 1)[0]   # acr_iberdrola_2026 -> iberdrola
    movimientos, errores = [], []
    saldo_calc = Decimal("0")

    for n, fila in enumerate(filas[1:], start=2):
        if all(c in ("", None) for c in fila[:9]):
            continue
        try:
            fecha = a_fecha(fila[0])
            debe, haber, saldo = a_decimal(fila[4]), a_decimal(fila[5]), a_decimal(fila[6])
        except (ValueError, ArithmeticError) as e:
            errores.append(f"{ruta.name} fila {n}: {e}")
            continue

        saldo_calc += debe - haber
        if abs(saldo_calc - saldo) > CENT:
            errores.append(f"{ruta.name} fila {n}: el saldo no cuadra "
                           f"(calculado {saldo_calc}, fichero {saldo})")
            saldo_calc = saldo      # resincroniza para no arrastrar el error

        concepto = str(fila[2]).strip()
        tipo, nombre = clasificar(concepto)
        if tipo != "factura":
            continue                # pagos, cobros y aperturas: solo cuentan para el saldo

        movimientos.append({
            "fichero": slug,
            "acreedor": nombre or slug,
            "fecha": fecha,
            "documento": texto(fila[1]),
            "cuenta": texto(fila[3]),
            "importe": haber - debe,    # positivo = factura, negativo = abono
            "asiento": a_entero(fila[7]),
            "linea": a_entero(fila[8]),
        })
    return movimientos, errores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("carpeta", type=Path, help="Carpeta con los acr_*.xlsx")
    parser.add_argument("salida", type=Path, help="Ruta del CSV a generar")
    args = parser.parse_args()

    ficheros = sorted(args.carpeta.glob("*.xlsx"))
    if not ficheros:
        print(f"No hay ficheros .xlsx en {args.carpeta}")
        return 1

    todos, errores = [], []
    for ruta in ficheros:
        if ruta.name.startswith("~$"):      # temporales de Excel abierto
            continue
        movimientos, errs = leer_fichero(ruta)
        todos.extend(movimientos)
        errores.extend(errs)

    if errores:
        print(f"{len(errores)} errores, no se genera el CSV:")
        for e in errores:
            print("  ", e)
        return 1

    todos.sort(key=lambda m: (m["fecha"], m["fichero"], m["asiento"], m["linea"]))
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    with args.salida.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_CSV, delimiter=";")
        w.writeheader()
        for m in todos:
            w.writerow({**m,
                        "fecha": m["fecha"].strftime("%d/%m/%Y"),
                        "importe": fmt(m["importe"])})

    # Resumen por pantalla
    por_grupo: dict[str, Decimal] = {}
    for m in todos:
        g = m["cuenta"][:3] or "???"
        por_grupo[g] = por_grupo.get(g, Decimal("0")) + m["importe"]

    print(f"OK: {len(ficheros)} ficheros -> {len(todos)} facturas en {args.salida}")
    print("    Importe por cuenta (total factura, IVA incluido):")
    for g in sorted(por_grupo):
        print(f"      {g}  {por_grupo[g]:>14,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
