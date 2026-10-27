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
VISION_SCALE = 200 / 72  # ~200 ppp; luego se reduce a VISION_MAX_SIDE_PX
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
    image_only: bool = False  # el modelo recibe SOLO las imágenes de las páginas (sin texto)

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


def _ocr_page(number: int, image: Image.Image, engine: OcrEngine, allow_vision: bool, always: bool = False) -> PreparedPage:
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
    if allow_vision and (always or poor or rotation):
        page.image_jpeg = _downscale_jpeg(image)
        page.method = MetodoExtraccion.VISION
    return page


def _summary_method(pages: list[PreparedPage]) -> str:
    methods = {p.method for p in pages}
    if len(methods) == 1:
        return methods.pop()
    return MetodoExtraccion.MIXED


VISION_MODES = ("image_only", "auto", "always", "never")


def prepare(
    path: Path,
    tipo_fuente: str,
    engine: OcrEngine | None = None,
    vision_supported: bool = False,
    vision_mode: str | None = None,
) -> PreparedDocument:
    """
    vision_mode (por defecto settings VISION_MODE):
        image_only  (por defecto) el modelo de visión lee las páginas como imágenes y no recibe texto.
                No usa Tesseract. El texto nativo de los PDF digitales se guarda solo para buscar
                evidencias, no se envía.
        auto    imagen solo de las páginas con OCR pobre o giradas (mínimo de datos al proveedor)
        always  imagen de TODAS las páginas, también las de texto nativo. Es el modo para un
                modelo de visión afinado: debe ver en producción lo mismo que vio al entrenar.
        never   solo texto
    """
    vision_mode = vision_mode or conf.get("VISION_MODE")
    if vision_mode not in VISION_MODES:
        raise PreparationError("BAD_CONFIG", f"VISION_MODE no válido: {vision_mode!r}")
    if vision_mode == "image_only":
        if not (vision_supported and conf.get("ALLOW_VISION")):
            raise PreparationError(
                "VISION_REQUIRED",
                "VISION_MODE=image_only necesita un modelo con visión (SUPPORTS_VISION y ALLOW_VISION). "
                "Para un modelo solo de texto usa VISION_MODE=auto (requiere Tesseract).",
            )
        return _prepare_images_only(path, tipo_fuente)
    engine = engine or TesseractEngine()
    allow_vision = bool(conf.get("ALLOW_VISION")) and vision_supported and vision_mode != "never"
    always = allow_vision and vision_mode == "always"
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
                        prepared = PreparedPage(index + 1, MetodoExtraccion.NATIVE, text, len(text.strip()))
                        if always:
                            prepared.image_jpeg = _downscale_jpeg(page.render(scale=VISION_SCALE).to_pil())
                            prepared.method = MetodoExtraccion.VISION
                        pages.append(prepared)
                    else:
                        image = page.render(scale=OCR_SCALE).to_pil()
                        pages.append(_ocr_page(index + 1, image, engine, allow_vision, always))
            finally:
                pdf.close()
        else:
            with Image.open(path) as source:
                frames = getattr(source, "n_frames", 1)
                for index in range(frames):
                    source.seek(index)
                    image = ImageOps.exif_transpose(source.convert("RGB"))
                    pages.append(_ocr_page(index + 1, image, engine, allow_vision, always))
    except PreparationError:
        raise
    except Exception as error:
        if _is_tesseract_missing(error):
            # TesseractNotFoundError hereda de OSError: sin esto se confundiría con un PDF dañado
            # y el documento quedaría como error definitivo. Instalarlo lo arregla: se reintenta.
            raise PreparationError(
                "OCR_NOT_INSTALLED",
                "Tesseract no está instalado o TESSERACT_CMD apunta a otro sitio "
                "(Linux: sudo apt install tesseract-ocr tesseract-ocr-spa).",
                True,
            ) from error
        if isinstance(error, FileNotFoundError):
            raise PreparationError("ORIGINAL_MISSING", "No se encuentra el fichero original.") from error
        if isinstance(error, (pdfium.PdfiumError, OSError)):
            raise PreparationError("DOCUMENT_UNREADABLE", f"No se puede leer el documento: {type(error).__name__}") from error
        # p. ej. un fallo de Tesseract: puede arreglarse y reintentarse.
        logger.exception("invoice_agent preparation failed")
        raise PreparationError("PREPARATION_FAILED", f"Fallo preparando el documento: {type(error).__name__}", True) from error

    if not any(p.chars for p in pages) and not any(p.image_jpeg for p in pages):
        raise PreparationError("NO_CONTENT", "No se ha podido obtener texto del documento ni enviarlo como imagen.")
    return PreparedDocument(pages=pages, method=_summary_method(pages))


def _image_page(number: int, image: Image.Image, native_text: str = "") -> PreparedPage:
    problems = []
    width, height = image.size
    if min(width, height) < 800:
        problems.append(f"resolución baja ({width}×{height} px)")
    text = native_text.strip()
    page = PreparedPage(number, MetodoExtraccion.VISION, text, len(text), problems=problems)
    page.image_jpeg = _downscale_jpeg(image)
    return page


def _prepare_images_only(path: Path, tipo_fuente: str) -> PreparedDocument:
    """Cada página como imagen, sin OCR. Mismo tamaño de imagen que en los demás modos."""
    pages: list[PreparedPage] = []
    try:
        if tipo_fuente == TipoFuente.PDF:
            pdf = pdfium.PdfDocument(str(path))
            try:
                for index in range(len(pdf)):
                    page = pdf[index]
                    native = page.get_textpage().get_text_range()
                    pages.append(_image_page(index + 1, page.render(scale=VISION_SCALE).to_pil(), native))
            finally:
                pdf.close()
        else:
            with Image.open(path) as source:
                for index in range(getattr(source, "n_frames", 1)):
                    source.seek(index)
                    pages.append(_image_page(index + 1, ImageOps.exif_transpose(source.convert("RGB"))))
    except FileNotFoundError as error:
        raise PreparationError("ORIGINAL_MISSING", "No se encuentra el fichero original.") from error
    except (pdfium.PdfiumError, OSError) as error:
        raise PreparationError("DOCUMENT_UNREADABLE", f"No se puede leer el documento: {type(error).__name__}") from error
    if not pages:
        raise PreparationError("NO_CONTENT", "El documento no tiene páginas.")
    return PreparedDocument(pages=pages, method=MetodoExtraccion.VISION, image_only=True)


def _is_tesseract_missing(error: Exception) -> bool:
    try:
        from pytesseract import TesseractNotFoundError
    except ImportError:
        return False
    return isinstance(error, TesseractNotFoundError)

