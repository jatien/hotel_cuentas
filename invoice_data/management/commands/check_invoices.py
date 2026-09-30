"""
Dry run of the extraction over a FOLDER of invoices. It never touches the
database: it prints one row per invoice, flags the ones that need
attention, and writes, next to the folder (never inside it):

    resultados_<carpeta>.csv    one row per invoice (opens in Spanish Excel)
    <carpeta>_json/             one JSON file per invoice (schema "factura/1")

Usage:
    python manage.py check_invoices muestras
    python manage.py check_invoices muestras --detail NOMBRE   full report of one invoice
    python manage.py check_invoices muestras --dump NOMBRE     text the parser sees
    python manage.py check_invoices muestras --dump-ocr NOMBRE text OCR reads from embedded images
    python manage.py check_invoices --ocr-check                is Tesseract ready?

States: OK, REVISAR (missing data or totals do not reconcile), OCR (scan or
photo: needs full-page OCR, use the import + process flow), ERROR (unreadable).
"""

import contextlib
import csv
import io
import json
import os
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from invoice_data.services import extractor as engine
from invoice_data.services.ingestion import IMAGE_EXTENSIONS

MIN_TEXT_CHARS = 40  # below this a PDF is treated as having no text layer
CSV_COLUMNS = [
    "archivo", "estado", "proveedor", "cif", "numero", "fecha", "lineas", "base", "iva",
    "total", "cuadra", "albaranes", "ocr_img", "motivos",
]


def ocr_status() -> str:
    """
    One line saying whether OCR of embedded images can run here, and if not
    exactly why: pytesseract missing, Tesseract not found (with the path
    tried), or the language data missing.
    """
    command = getattr(settings, "TESSERACT_CMD", None) or os.environ.get("TESSERACT_CMD")
    try:
        import pytesseract
    except ModuleNotFoundError:
        return "OCR: NO LISTO - falta el paquete pytesseract  ->  pip install pytesseract"
    if command:
        pytesseract.pytesseract.tesseract_cmd = command
    try:
        version = pytesseract.get_tesseract_version()
        languages = pytesseract.get_languages()
    except Exception as error:
        tried = command or "tesseract (buscado en el PATH)"
        return (f"OCR: NO LISTO - Tesseract no se encuentra ({type(error).__name__}); ruta probada: {tried}\n"
                "      Define TESSERACT_CMD en settings.py con la ruta completa a tesseract.exe")
    if engine.ocr_language() not in languages:
        return (f"OCR: NO LISTO - Tesseract {version} funciona pero falta el idioma '{engine.ocr_language()}' "
                f"(instalados: {', '.join(languages)}). Reinstala marcando Spanish en los datos de idioma.")
    return f"OCR: listo - Tesseract {version}, idiomas: {', '.join(languages)}"


def check_file(path: Path) -> dict:
    """
    Extracts one file and returns a flat row (fields, estado, reasons) plus
    the engine result under "_result" (None for scans, photos and failures).
    """
    row = {name: "" for name in CSV_COLUMNS}
    row.update(archivo=path.name, lineas=0, base=None, total=None, ocr_img=0, _result=None)

    if path.suffix.lower() in IMAGE_EXTENSIONS:
        row.update(estado="OCR", motivos="imagen: necesita OCR de página completa")
        return row
    try:
        text = engine.read_pdf_text(path)
    except Exception as error:
        row.update(estado="ERROR", motivos=f"no se pudo abrir: {error}")
        return row
    if len(text.strip()) < MIN_TEXT_CHARS:
        row.update(estado="OCR", motivos="sin capa de texto (escaneado): necesita OCR de página completa")
        return row
    try:
        result = engine.extract(path)
    except Exception as error:
        row.update(estado="ERROR", motivos=f"fallo al extraer: {error}")
        return row

    problems = engine.review_problems(result)
    albaranes = result.albaran_totals
    albaranes_ok = sum(1 for s, p in albaranes.values() if p is not None and abs(s - p) <= Decimal("0.01"))
    row.update(
        estado="OK" if not problems else "REVISAR",
        proveedor=result.proveedor_nombre or "", cif=result.proveedor_cif or "",
        numero=result.numero or "", fecha=result.fecha or "", lineas=len(result.lines),
        base=sum(result.bases.values()) if result.bases else None,
        iva="/".join(f"{rate.normalize():f}%" for rate in result.bases),
        total=result.total, cuadra="SI" if result.reconciled else "NO",
        albaranes=f"{albaranes_ok}/{len(albaranes)}" if albaranes else "",
        ocr_img=result.ocr_regions_used, motivos="; ".join(problems), _result=result,
    )
    return row


def mark_duplicates(rows: list[dict]) -> None:
    """The same supplier CIF + invoice number in two files is almost certainly the same invoice twice."""
    seen = defaultdict(list)
    for row in rows:
        if row["cif"] and row["numero"]:
            seen[(row["cif"], row["numero"])].append(row)
    for group in seen.values():
        if len(group) > 1:
            names = ", ".join(r["archivo"] for r in group)
            for row in group:
                if row["estado"] == "OK":
                    row["estado"] = "REVISAR"
                row["motivos"] = (row["motivos"] + "; " if row["motivos"] else "") + f"posible duplicada ({names})"


def table_lines(rows: list[dict]) -> list[str]:
    """The one-row-per-invoice table, fixed columns, as lines of text."""
    header = f"{'ESTADO':<8} {'ARCHIVO':<36} {'PROVEEDOR':<26} {'NUMERO':<14} {'FECHA':<11} {'LIN':>3} {'TOTAL':>10}"
    lines = [header, "-" * len(header)]
    for r in rows:
        total = "" if r["total"] is None else f"{r['total']:.2f}"
        lines.append(
            f"{r['estado'] + ('*' if r['ocr_img'] else ''):<8} {r['archivo'][:36]:<36} {r['proveedor'][:26]:<26} "
            f"{r['numero'][:14]:<14} {r['fecha']:<11} {r['lineas']:>3} {total:>10}"
        )
    return lines


def summary_lines(rows: list[dict]) -> list[str]:
    """Counts per estado, then the reasons for every invoice that is not OK."""
    counts = defaultdict(int)
    for r in rows:
        counts[r["estado"]] += 1
    lines = ["", f"TOTAL {len(rows)} facturas:  " + "   ".join(f"{k} {counts[k]}" for k in ("OK", "REVISAR", "OCR", "ERROR"))]
    if any(r["ocr_img"] for r in rows):
        lines.append("  (* = completada leyendo con OCR imágenes incrustadas en el PDF)")
    pending = [r for r in rows if r["estado"] != "OK"]
    if pending:
        lines.append("\nREQUIEREN ATENCIÓN:")
        for r in pending:
            lines.append(f"  [{r['estado']}] {r['archivo']}\n         {r['motivos']}")
    return lines


def write_csv(rows: list[dict], folder: Path) -> Path:
    """Saves the table as ';'-separated UTF-8 with BOM, decimal commas (opens directly in Spanish Excel)."""
    out = folder.parent / f"resultados_{folder.name}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({**r, "base": _es(r["base"]), "total": _es(r["total"])})
    return out


def _es(value) -> str:
    """A number with a decimal comma; empty for None."""
    return "" if value is None else str(value).replace(".", ",")


def write_json_files(rows: list[dict], folder: Path) -> Path:
    """One JSON file per input file (scans and failures included), built from each row's FINAL state."""
    out = folder.parent / f"{folder.name}_json"
    out.mkdir(exist_ok=True)
    for r in rows:
        reasons = [m for m in r["motivos"].split("; ") if m]
        data = engine.result_to_dict(r["_result"], r["archivo"], r["estado"], reasons)
        (out / f"{r['archivo']}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def unparsed_suspects(text: str) -> list[str]:
    """Rows that look like products (words + 3 or more trailing numbers) but were refused; a missing line shows up here."""
    raw_lines = [line.strip() for line in text.split("\n")]
    iva_column = engine.has_iva_column(raw_lines)
    suspects = []
    for raw in raw_lines:
        tokens = raw.split()
        if not any(engine.re.search(r"[A-Za-zÁÉÍÓÚÑ]{2,}", t) for t in tokens):
            continue
        trailing = 0
        for token in reversed(tokens):
            if engine.NUMERIC_TOKEN.match(token):
                trailing += 1
            else:
                break
        if trailing >= 3 and engine.parse_line(raw, iva_column) is None and not engine.ALBARAN_RE.search(raw):
            suspects.append(raw)
    return suspects


class Command(BaseCommand):
    """Dry run over a folder: table + CSV + one JSON per invoice, without touching the database."""

    help = "Comprueba la extracción sobre una carpeta de facturas (CSV + un JSON por factura), sin tocar la base de datos."

    def add_arguments(self, parser):
        """Folder plus the diagnostic modes."""
        parser.add_argument("folder", nargs="?", default="muestras", help="Carpeta con las facturas (por defecto: muestras).")
        parser.add_argument("--detail", metavar="NOMBRE", help="Informe completo de una factura (parte del nombre).")
        parser.add_argument("--dump", metavar="NOMBRE", help="Texto que lee el programa de una factura.")
        parser.add_argument("--dump-ocr", metavar="NOMBRE", help="Texto que el OCR lee de las imágenes de una factura.")
        parser.add_argument("--ocr-check", action="store_true", help="Solo comprueba si el OCR está listo.")
        parser.add_argument("--max", type=int, default=90, help="Máximo de líneas con --dump (por defecto 90).")

    def _matches(self, folder: Path, name: str) -> list[Path]:
        """PDFs in the folder whose file name contains `name` (case-insensitive)."""
        found = [p for p in sorted(folder.rglob("*.pdf")) if name.lower() in p.name.lower()]
        if not found:
            self.stdout.write(f"Ningún PDF contiene '{name}' en {folder}")
        return found

    def handle(self, *args, **options):
        """Runs the requested mode."""
        self.stdout.write(f"version: extractor {engine.VERSION}")
        if options["ocr_check"]:
            self.stdout.write(ocr_status())
            return

        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"No existe la carpeta: {folder.resolve()}")

        if options["detail"]:
            for path in self._matches(folder, options["detail"]):
                text = engine.read_pdf_text(path)
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    engine.report(path, engine.extract(path))
                self.stdout.write(buffer.getvalue().rstrip())
                suspects = unparsed_suspects(text)
                if suspects:
                    self.stdout.write("\n  Filas con texto + 3 o más números que NO se aceptaron como producto:")
                    for line in suspects:
                        self.stdout.write(f"    | {line[:110]}")
            return

        if options["dump"]:
            for path in self._matches(folder, options["dump"]):
                lines = [l for l in engine.read_pdf_text(path).split("\n") if l.strip()]
                self.stdout.write("=" * 72)
                self.stdout.write(f"{path.name}  ({len(lines)} líneas; mostrando {min(len(lines), options['max'])})")
                for number, line in enumerate(lines[: options["max"]], start=1):
                    self.stdout.write(f"{number:>3} | {line.rstrip()[:160]}")
            return

        if options["dump_ocr"]:
            for path in self._matches(folder, options["dump_ocr"]):
                boxes = engine.large_image_boxes(path)
                regions, problem = engine.read_image_regions(path)
                self.stdout.write("=" * 72)
                self.stdout.write(f"{path.name}: {len(boxes)} imágenes grandes, {len(regions)} con texto legible")
                if problem:
                    self.stdout.write(f"  problema: {problem}")
                for region in regions:
                    self.stdout.write(f"--- página {region.page + 1}, altura (desde abajo) {region.bottom:.0f}-{region.top:.0f} pt ---")
                    self.stdout.write(region.text)
            return

        files = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".pdf" or p.suffix.lower() in IMAGE_EXTENSIONS)
        if not files:
            raise CommandError(f"No hay PDF ni imágenes en {folder.resolve()}")
        self.stdout.write(ocr_status() + "\n")
        rows = [check_file(path) for path in files]
        mark_duplicates(rows)
        for line in table_lines(rows) + summary_lines(rows):
            self.stdout.write(line)
        self.stdout.write(f"\nCSV guardado en:   {write_csv(rows, folder)}")
        self.stdout.write(f"JSON guardados en: {write_json_files(rows, folder)}  ({len(rows)} archivos)")
