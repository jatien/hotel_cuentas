"""
Generators of fictional test documents (no real invoice is used anywhere).

    make_invoice_pdf   a digital PDF that reconciles (or not, on request)
    make_scanned_pdf   a PDF holding only a picture (no text layer)
    make_image         a plain image file
"""

from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

# One consistent fictional invoice: two lines, IVA 21 %, base 100, cuota 21, total 121.
INVOICE_TEXT = [
    "FACTURA",
    "Nº Factura: X-2026/15   Fecha: 17/02/26",
    "Suministros Ejemplo, S.L.   CIF: B12345674",
    "Cliente: HOTEL DEMO S.L.   NIF: B99999999",
    "REFERENCIA DESCRIPCION UDS. PRECIO NETO",
    "AB1234 TORNILLO GALVANIZADO 10,00 2,5000 25,00",
    "AB5678 TUERCA ACERO 5,00 15,0000 75,00",
    "BASE 100,00 CUOTA 21,00 TOTAL 121,00",
]


def invoice_text(total: str = "121,00") -> str:
    """The fictional invoice as plain text; `total` can be altered to make it NOT reconcile."""
    return "\n".join(INVOICE_TEXT).replace("TOTAL 121,00", f"TOTAL {total}")


def make_invoice_pdf(path: Path, total: str = "121,00", extra_line: str = "", numero: str = "X-2026/15") -> Path:
    """
    Writes the fictional invoice as a real digital PDF (selectable text) and returns its path.
    The PDF is deterministic (invariant=1: no timestamps), so the same arguments always give
    the same bytes and therefore the same checksum, which the duplicate check relies on.
    """
    pdf = canvas.Canvas(str(path), invariant=1)
    y = 800
    for line in invoice_text(total).replace("X-2026/15", numero).split("\n"):
        pdf.drawString(50, y, line)
        y -= 18
    if extra_line:
        pdf.drawString(50, y, extra_line)
    pdf.save()
    return path


def make_scanned_pdf(path: Path) -> Path:
    """A PDF that is only a raster picture: it has no text layer, like a scan."""
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).text((50, 50), "picture only, not selectable", fill="black")
    image.save(path, "PDF")
    return path


def make_image(path: Path, text: str = "FACTURA FOTO PRUEBA") -> Path:
    """A plain image file (like a phone photo), with a little text drawn on it."""
    image = Image.new("RGB", (600, 200), "white")
    ImageDraw.Draw(image).text((20, 80), text, fill="black")
    image.save(path)
    return path
