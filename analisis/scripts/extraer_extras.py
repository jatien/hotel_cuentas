"""Convierte la hoja de extras de personal (.numbers o .xlsx) en un CSV limpio.

La hoja del año (p. ej. «26») tiene una fila por persona y una columna por mes.
Las personas van en bloques separados por filas vacías, más o menos uno por
departamento. El departamento se propone a partir de la hoja «cash 26» (que
tiene encabezados Cocina / Bar / Comedor): cada bloque toma el departamento
que más se repite entre sus personas. Revisa la columna antes de importar.

Uso (desde la raíz del proyecto):
    python analisis/scripts/extraer_extras.py analisis/datos/2026/extras_personal.numbers analisis/datos/2026/extras.csv --anio 2026

Para leer .numbers hace falta `pip install numbers-parser`. Si no se puede
instalar, exporta el fichero desde Numbers a Excel (.xlsx): funciona igual.

Salida: anio;mes;persona;departamento;importe
"""

import argparse
import csv
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
DEPARTAMENTOS = ["Cocina", "Bar", "Comedor", "Recepción", "Pisos", "Mantenimiento", "Lavandería"]
CENT = Decimal("0.01")


def normaliza(texto) -> str:
    texto = "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                    if unicodedata.category(c) != "Mn")
    return " ".join(texto.lower().split())


DEPTOS = {normaliza(d): d for d in DEPARTAMENTOS}


# ------------------------------------------------------------------ lectura

def leer_hojas(ruta: Path) -> dict[str, list[list]]:
    """Todas las hojas como {nombre: filas}. En .numbers se usa la primera tabla de cada hoja."""
    if ruta.suffix.lower() == ".numbers":
        try:
            from numbers_parser import Document
        except ImportError:
            sys.exit("Para leer .numbers instala numbers-parser (pip install numbers-parser) "
                     "o exporta el fichero a .xlsx desde Numbers.")
        doc = Document(str(ruta))
        return {h.name: [list(f) for f in h.tables[0].rows(values_only=True)] for h in doc.sheets if h.tables}
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_path(str(ruta))
    return {n: wb.get_sheet_by_name(n).to_python() for n in wb.sheet_names}


def mes_de(celda) -> int | None:
    if isinstance(celda, (datetime, date)):
        return celda.month
    return MESES.get(normaliza(celda)[:3]) if isinstance(celda, str) and celda.strip() else None


def es_numero(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def vacia(fila) -> bool:
    return all(c in (None, "") or (isinstance(c, str) and not c.strip()) for c in fila)


# ------------------------------------------------------------------ departamentos

def departamentos_de_cash(filas) -> dict[str, str]:
    """{persona normalizada: departamento} según los encabezados de la hoja «cash»."""
    mapa, actual = {}, ""
    for f in filas:
        nombre = normaliza(f[0]) if f else ""
        if not nombre:
            actual = ""        # una fila sin nombre (subtotal) cierra la sección
            continue
        if nombre in DEPTOS:
            actual = DEPTOS[nombre]
        elif actual and nombre not in ("total", "totales"):
            mapa[nombre] = actual
    return mapa


# ------------------------------------------------------------------ extracción

def extraer(filas, anio: int, cash: dict[str, str]):
    # Cabecera: la primera fila con al menos tres meses reconocibles
    cab_idx, columnas = None, {}
    for i, f in enumerate(filas):
        meses = {j: mes_de(c) for j, c in enumerate(f) if j > 0 and mes_de(c)}
        if len(meses) >= 3:
            cab_idx, columnas = i, meses
            break
    if cab_idx is None:
        sys.exit("No se encuentra la fila de meses en la hoja.")

    registros, avisos, bloques, bloque = [], [], [], []
    vistos = Counter()
    fila_totales = None

    for n, f in enumerate(filas[cab_idx + 1:], start=cab_idx + 2):
        if vacia(f) or not str(f[0] or "").strip():
            # Fila sin nombre (aunque tenga fórmulas a 0): separa bloques
            if bloque:
                bloques.append(bloque)
                bloque = []
            continue
        nombre = " ".join(str(f[0] or "").split())
        if normaliza(nombre).startswith("totales"):
            fila_totales = f
            break
        if normaliza(nombre) == "total":
            if bloque:          # fila «TOTAL» intermedia: también separa bloques
                bloques.append(bloque)
                bloque = []
            continue

        vistos[nombre] += 1
        if vistos[nombre] > 1:
            avisos.append(f"fila {n}: «{nombre}» aparece repetido; se guarda como «{nombre} ({vistos[nombre]})»")
            nombre = f"{nombre} ({vistos[nombre]})"

        for j, mes in columnas.items():
            v = f[j] if j < len(f) else None
            if es_numero(v) and v != 0:
                registros.append({"anio": anio, "mes": mes, "persona": nombre,
                                  "importe": Decimal(repr(float(v))).quantize(CENT)})
            elif isinstance(v, str) and v.strip():
                avisos.append(f"fila {n} ({nombre}), mes {mes}: texto ignorado «{v.strip()[:60]}»")
        bloque.append(nombre)
    if bloque:
        bloques.append(bloque)

    # Departamento por bloque: el que más se repite según la hoja «cash»
    depto_de = {}
    for b in bloques:
        votos = Counter(cash[normaliza(p)] for p in b if normaliza(p) in cash)
        depto = votos.most_common(1)[0][0] if votos else ""
        for p in b:
            # «pisos» y similares: el propio nombre es un departamento
            depto_de[p] = DEPTOS.get(normaliza(p), depto)
    for r in registros:
        r["departamento"] = depto_de.get(r["persona"], "")

    # Control con la fila «totales» (solo en los meses en que tiene número)
    if fila_totales:
        for j, mes in columnas.items():
            v = fila_totales[j] if j < len(fila_totales) else None
            if es_numero(v) and v:
                suma = sum((r["importe"] for r in registros if r["mes"] == mes), Decimal("0"))
                if abs(suma - Decimal(repr(float(v)))) > Decimal("0.5"):
                    avisos.append(f"mes {mes}: la suma ({suma}) no coincide con la fila totales ({v})")
    return registros, avisos, bloques, depto_de


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fichero", type=Path, help=".numbers o .xlsx con las hojas de extras")
    parser.add_argument("salida", type=Path, help="CSV a generar")
    parser.add_argument("--anio", type=int, default=date.today().year)
    parser.add_argument("--hoja", help="Nombre de la hoja (por defecto, los dos últimos dígitos del año)")
    args = parser.parse_args()

    hojas = leer_hojas(args.fichero)
    nombre_hoja = args.hoja or str(args.anio)[-2:]
    if nombre_hoja not in hojas:
        print(f"No existe la hoja «{nombre_hoja}». Hojas: {', '.join(hojas)}")
        return 1
    cash = {}
    for nombre, filas in hojas.items():
        if normaliza(nombre).startswith("cash") and nombre_hoja in nombre:
            cash = departamentos_de_cash(filas)

    registros, avisos, bloques, depto_de = extraer(hojas[nombre_hoja], args.anio, cash)
    if not registros:
        print("No se ha encontrado ningún importe.")
        return 1

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    with args.salida.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["anio", "mes", "persona", "departamento", "importe"], delimiter=";")
        w.writeheader()
        for r in sorted(registros, key=lambda r: (r["mes"], r["persona"])):
            w.writerow({**r, "importe": f"{r['importe']:.2f}".replace(".", ",")})

    total = sum((r["importe"] for r in registros), Decimal("0"))
    print(f"OK: hoja «{nombre_hoja}» -> {len(registros)} importes, {len({r['persona'] for r in registros})} "
          f"personas, total {total:,.2f} € en {args.salida}")
    por_mes = Counter()
    for r in registros:
        por_mes[r["mes"]] += r["importe"]
    print("    Por mes:", ", ".join(f"{m}: {v:,.0f}" for m, v in sorted(por_mes.items())))
    print("    Departamento propuesto por bloque (revísalo en el CSV o luego en el admin):")
    for b in bloques:
        print(f"      {depto_de.get(b[0]) or 'sin asignar':<14} {', '.join(b)}")
    for a in avisos:
        print("    Aviso:", a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
