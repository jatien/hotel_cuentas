"""
Etapa B: preparación del documento para el modelo.

Por página decide el método:
    native  el PDF tiene capa de texto suficiente -> se usa tal cual (barato y exacto)
    ocr     página escaneada o foto -> Tesseract (reutiliza invoice_data.services.ocr)
    vision  además del OCR, se adjunta la imagen reducida al modelo cuando el
            OCR es pobre (confianza baja, poco texto, página girada) y el
            proveedor admite imágenes. Solo esas páginas (minimización de datos).

Detecta páginas giradas (orientación de Tesseract) y de baja calidad y lo
deja en `problems`, que acaba como aviso de revisión. Conserva el número de
página de cada fragmento para que el modelo pueda citar la evidencia.
"""

import io
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from invoice_agent import conf
from invoice_agent.models import MetodoExtraccion, TipoFuente

logger = logging.getLogger("invoice_agent")

OCR_SCALE = 300 / 72  # ~300 ppp para OCR
MIN_OCR_CHARS = 40  # menos texto que esto en una página escaneada = lectura pobre


class PreparationError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class OcrEngine(Protocol):
    """Motor de OCR intercambiable (Tesseract en producción, uno falso en los tests)."""

    def read(self, image: Image.Image) -> tuple[str, float | None]:
        """Texto en orden de lectura y confianza media (0-100) o None."""

    def rotation(self, image: Image.Image) -> int | None:
        """Grados a girar para enderezar la página (0, 90, 180, 270) o None si no se sabe."""


class TesseractEngine:
    """Usa el OCR que ya existe en invoice_data (mismo idioma y configuración de Tesseract)."""

    def read(self, image):
        from invoice_data.services.ocr import ocr_pages

        text, words = ocr_pages([image])
        confidence = sum(w.confidence for w in words) / len(words) if words else None
        return text, confidence

    def rotation(self, image):
        import pytesseract

        from invoice_data.services.ocr import _configure_tesseract

        _configure_tesseract()
        try:
            info = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
            return int(info.get("rotate", 0)) % 360
        except Exception:  # OSD falla con poco texto; no es un error del documento
            return None


@dataclass
class PreparedPage:
    number: int
    method: str
    text: str
    chars: int
    ocr_confidence: float | None = None
    rotation: int | None = None
    problems: list[str] = field(default_factory=list)
    image_jpeg: bytes | None = None

    def metadata(self) -> dict:
        data = asdict(self)
        data.pop("text")
        data["image_sent"] = data.pop("image_jpeg") is not None
        return data


@dataclass
class PreparedDocument:
    pages: list[PreparedPage]
    method: str

    @property
    def images(self) -> list[tuple[int, bytes]]:
        return [(p.number, p.image_jpeg) for p in self.pages if p.image_jpeg]

    def metadata(self) -> dict:
        return {"method": self.method, "pages": [p.metadata() for p in self.pages]}

    def page_problems(self) -> list[dict]:
        return [{"page": p.number, "message": msg} for p in self.pages for msg in p.problems]


def _downscale_jpeg(image: Image.Image) -> bytes:
    limit = int(conf.get("VISION_MAX_SIDE_PX"))
    copy = image.convert("RGB")
    copy.thumbnail((limit, limit))
    buffer = io.BytesIO()
    copy.save(buffer, "JPEG", quality=80, optimize=True)
    return buffer.getvalue()


def _ocr_page(number: int, image: Image.Image, engine: OcrEngine, allow_vision: bool) -> PreparedPage:
    problems = []
    rotation = engine.rotation(image)
    if rotation:
        image = image.rotate(-rotation, expand=True)
        problems.append(f"página girada {rotation}°; se ha enderezado antes del OCR")
    text, confidence = engine.read(image)
    chars = len(text.strip())
    min_conf = float(conf.get("OCR_MIN_MEAN_CONFIDENCE"))
    poor = chars < MIN_OCR_CHARS or (confidence is not None and confidence < min_conf)
    if chars < MIN_OCR_CHARS:
        problems.append("muy poco texto legible (¿página en blanco, foto borrosa o manuscrita?)")
    elif confidence is not None and confidence < min_conf:
        problems.append(f"calidad de OCR baja (confianza media {confidence:.0f}/100)")
    width, height = image.size
    if min(width, height) < 800:
        problems.append(f"resolución baja ({width}×{height} px)")
        poor = True
    page = PreparedPage(number, MetodoExtraccion.OCR, text, chars, confidence, rotation, problems)
    if allow_vision and (poor or rotation):
        page.image_jpeg = _downscale_jpeg(image)
        page.method = MetodoExtraccion.VISION
    return page


def _summary_method(pages: list[PreparedPage]) -> str:
    methods = {p.method for p in pages}
    if len(methods) == 1:
        return methods.pop()
    return MetodoExtraccion.MIXED


def prepare(path: Path, tipo_fuente: str, engine: OcrEngine | None = None, vision_supported: bool = False) -> PreparedDocument:
    engine = engine or TesseractEngine()
    allow_vision = bool(conf.get("ALLOW_VISION")) and vision_supported
    min_native = int(conf.get("MIN_NATIVE_CHARS_PER_PAGE"))
    pages: list[PreparedPage] = []
    try:
        if tipo_fuente == TipoFuente.PDF:
            pdf = pdfium.PdfDocument(str(path))
            try:
                for index in range(len(pdf)):
                    page = pdf[index]
                    text = page.get_textpage().get_text_range()
                    if len(text.strip()) >= min_native:
                        pages.append(PreparedPage(index + 1, MetodoExtraccion.NATIVE, text, len(text.strip())))
                    else:
                        image = page.render(scale=OCR_SCALE).to_pil()
                        pages.append(_ocr_page(index + 1, image, engine, allow_vision))
            finally:
                pdf.close()
        else:
            with Image.open(path) as source:
                frames = getattr(source, "n_frames", 1)
                for index in range(frames):
                    source.seek(index)
                    image = ImageOps.exif_transpose(source.convert("RGB"))
                    pages.append(_ocr_page(index + 1, image, engine, allow_vision))
    except PreparationError:
        raise
    except FileNotFoundError as error:
        raise PreparationError("ORIGINAL_MISSING", "No se encuentra el fichero original.") from error
    except (pdfium.PdfiumError, OSError) as error:
        raise PreparationError("DOCUMENT_UNREADABLE", f"No se puede leer el documento: {type(error).__name__}") from error
    except Exception as error:
        # p. ej. Tesseract no instalado: puede arreglarse y reintentarse.
        logger.exception("invoice_agent preparation failed")
        raise PreparationError("PREPARATION_FAILED", f"Fallo preparando el documento: {type(error).__name__}", True) from error

    if not any(p.chars for p in pages) and not any(p.image_jpeg for p in pages):
        raise PreparationError("NO_CONTENT", "No se ha podido obtener texto del documento ni enviarlo como imagen.")
    return PreparedDocument(pages=pages, method=_summary_method(pages))
