"""
Etapa A: recepción del fichero.

ingest_upload(uploaded_file, empresa_id, ctx) -> (Documento, created)

1. Comprueba acceso a la empresa (contexto del servidor, no el cliente).
2. Tamaño máximo, extensión, tipo MIME declarado y firma real del fichero
   (primeros bytes): un .pdf que no empieza por %PDF- se rechaza.
3. Copia a un temporal calculando el SHA-256 sin cargarlo entero en memoria.
4. Gancho de antivirus (MALWARE_SCANNER) y apertura de prueba (PDF cifrado
   o dañado, imagen "bomba" de descompresión, demasiadas páginas).
5. Si el mismo fichero ya existe en la empresa, devuelve ese documento (idempotente).
6. Guarda el original en almacenamiento privado y, en UNA transacción, crea
   el Documento, su Trabajo en cola y el evento de auditoría. Si la
   transacción falla, se borra el fichero guardado.

Nada caro (OCR, modelo) se hace aquí: eso lo hace el worker.
"""

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pypdfium2 as pdfium
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from PIL import Image

from invoice_agent import conf
from invoice_agent.models import (
    AccionRevision,
    Documento,
    Empresa,
    EventoRevision,
    TipoFuente,
    Trabajo,
)
from invoice_agent.services.access import AccessContext
from invoice_agent.services.errors import ServiceError
from invoice_agent.services.observability import log_event
from invoice_agent.services.storage import new_storage_key, private_storage


@dataclass(frozen=True)
class FileKind:
    mime: str
    tipo: str


SIGNATURES = [
    (b"%PDF-", FileKind("application/pdf", TipoFuente.PDF)),
    (b"\x89PNG\r\n\x1a\n", FileKind("image/png", TipoFuente.IMAGE)),
    (b"\xff\xd8\xff", FileKind("image/jpeg", TipoFuente.IMAGE)),
    (b"II*\x00", FileKind("image/tiff", TipoFuente.IMAGE)),
    (b"MM\x00*", FileKind("image/tiff", TipoFuente.IMAGE)),
]
EXTENSIONS = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}
# Tipos MIME que un navegador puede declarar para cada tipo real (algunos mandan octet-stream).
COMPATIBLE_DECLARED = {
    "application/pdf": {"application/pdf", "application/x-pdf"},
    "image/jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"},
}


class UploadRejected(ServiceError):
    status = 400


def sniff(head: bytes) -> FileKind | None:
    """Tipo real del fichero por su firma (magic bytes)."""
    for magic, kind in SIGNATURES:
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return FileKind("image/webp", TipoFuente.IMAGE)
    return None


def _copy_to_temp(uploaded_file, limit: int) -> tuple[Path, str, int]:
    sha = hashlib.sha256()
    size = 0
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".upload")
    try:
        for chunk in uploaded_file.chunks():
            size += len(chunk)
            if size > limit:
                raise UploadRejected("FILE_TOO_LARGE", f"El fichero supera el máximo de {limit // (1024 * 1024)} MB.", 413)
            sha.update(chunk)
            handle.write(chunk)
    except Exception:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise
    handle.close()
    return Path(handle.name), sha.hexdigest(), size


def _scan_for_malware(path: Path) -> None:
    scanner = conf.get("MALWARE_SCANNER")
    if not scanner:
        return
    try:
        import_string(scanner)(path)
    except Exception as error:  # el escáner decide; cualquier excepción = rechazar
        log_event("upload.malware_rejected", scanner=scanner, error=type(error).__name__)
        raise UploadRejected("MALWARE_DETECTED", "El análisis antivirus ha rechazado el fichero.", 422) from error


def inspect_file(path: Path, kind: FileKind) -> int:
    """Abre el fichero para comprobar que es válido; devuelve el número de páginas."""
    max_pages = int(conf.get("MAX_PAGES"))
    if kind.tipo == TipoFuente.PDF:
        try:
            pdf = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as error:
            raise UploadRejected("PDF_UNREADABLE", "El PDF está dañado o protegido con contraseña.", 422) from error
        try:
            pages = len(pdf)
        finally:
            pdf.close()
        if pages == 0:
            raise UploadRejected("PDF_EMPTY", "El PDF no tiene páginas.", 422)
        if pages > max_pages:
            raise UploadRejected("TOO_MANY_PAGES", f"El PDF tiene {pages} páginas; el máximo es {max_pages}.", 422)
        return pages

    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = int(conf.get("MAX_IMAGE_PIXELS"))
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            frames = getattr(image, "n_frames", 1)
    except Image.DecompressionBombError as error:
        raise UploadRejected("IMAGE_TOO_LARGE", "La imagen es demasiado grande.", 422) from error
    except Exception as error:
        raise UploadRejected("IMAGE_UNREADABLE", "La imagen está dañada o no se puede abrir.", 422) from error
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    if frames > max_pages:
        raise UploadRejected("TOO_MANY_PAGES", f"La imagen tiene {frames} páginas; el máximo es {max_pages}.", 422)
    return frames


def validate_name_and_type(filename: str, declared_type: str | None, head: bytes) -> FileKind:
    extension = Path(filename or "").suffix.lower()
    if extension not in EXTENSIONS:
        raise UploadRejected(
            "UNSUPPORTED_TYPE", "Formato no admitido: sube un PDF o una imagen (PNG, JPG, TIFF, WEBP).", 415
        )
    kind = sniff(head)
    if kind is None or kind.mime != EXTENSIONS[extension]:
        raise UploadRejected("SIGNATURE_MISMATCH", "El contenido del fichero no corresponde a su extensión.", 415)
    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared and declared != "application/octet-stream":
        allowed = COMPATIBLE_DECLARED.get(kind.mime, {kind.mime})
        if declared not in allowed:
            raise UploadRejected("MIME_MISMATCH", "El tipo declarado del fichero no coincide con su contenido.", 415)
    return kind


def ingest_upload(uploaded_file, empresa_id: int, ctx: AccessContext) -> tuple[Documento, bool]:
    try:
        empresa_id = int(empresa_id)
    except (TypeError, ValueError) as error:
        raise UploadRejected("EMPRESA_REQUIRED", "Indica la empresa que recibe la factura.") from error
    if empresa_id not in ctx.empresa_ids:
        # Mismo mensaje exista o no la empresa: no revela empresas ajenas.
        raise ServiceError("EMPRESA_NOT_FOUND", "Empresa no encontrada.", 404)
    empresa = Empresa.objects.get(pk=empresa_id, activa=True)

    limit = int(conf.get("MAX_UPLOAD_BYTES"))
    if uploaded_file.size is not None and uploaded_file.size > limit:
        raise UploadRejected("FILE_TOO_LARGE", f"El fichero supera el máximo de {limit // (1024 * 1024)} MB.", 413)
    if not uploaded_file.size:
        raise UploadRejected("EMPTY_FILE", "El fichero está vacío.")

    uploaded_file.seek(0)
    head = uploaded_file.read(16)
    uploaded_file.seek(0)
    filename = Path(uploaded_file.name or "factura").name[:255]
    kind = validate_name_and_type(filename, getattr(uploaded_file, "content_type", None), head)

    temp_path, sha256, size = _copy_to_temp(uploaded_file, limit)
    try:
        existing = Documento.objects.filter(empresa=empresa, sha256=sha256).first()
        if existing:
            log_event("upload.duplicate_hash", documento=existing.public_id, empresa=empresa.pk)
            return existing, False
        _scan_for_malware(temp_path)
        pages = inspect_file(temp_path, kind)

        storage = private_storage()
        key = new_storage_key(empresa.pk, Path(filename).suffix.lower())
        with open(temp_path, "rb") as handle:
            key = storage.save(key, handle)
        try:
            with transaction.atomic():
                retention = conf.get("RETENTION_DAYS")
                documento = Documento.objects.create(
                    empresa=empresa,
                    storage_key=key,
                    original_filename=filename,
                    sha256=sha256,
                    mime_type=kind.mime,
                    size_bytes=size,
                    tipo_fuente=kind.tipo,
                    page_count=pages,
                    subido_por=ctx.user if ctx.user_id else None,
                    subido_desde=ctx.actor_label[:100],
                    borrar_despues_de=(timezone.localdate() + timedelta(days=int(retention))) if retention else None,
                )
                Trabajo.objects.create(
                    documento=documento,
                    max_intentos=int(conf.get("JOB_MAX_ATTEMPTS")),
                    ejecutar_desde=timezone.now(),
                )
                EventoRevision.objects.create(
                    documento=documento,
                    accion=AccionRevision.UPLOADED,
                    estado_nuevo=documento.estado,
                    actor=ctx.user if ctx.user_id else None,
                    actor_label=ctx.actor_label,
                )
        except IntegrityError:
            # Otra petición subió el mismo fichero a la vez: gana la primera.
            storage.delete(key)
            existing = Documento.objects.filter(empresa=empresa, sha256=sha256).first()
            if existing is None:
                raise
            return existing, False
        except Exception:
            storage.delete(key)
            raise
    finally:
        temp_path.unlink(missing_ok=True)

    log_event(
        "upload.accepted",
        documento=documento.public_id,
        correlation_id=documento.correlation_id,
        empresa=empresa.pk,
        tipo=kind.tipo,
        pages=pages,
        size=size,
    )
    return documento, True
