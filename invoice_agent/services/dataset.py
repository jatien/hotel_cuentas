"""
Exportación del dataset de entrenamiento a partir de las facturas APROBADAS (y, con
include_pending, de las PENDIENTES DE APROBACIÓN).

Cada factura aprobada es un ejemplo verificado por una persona:
    entrada  = exactamente la petición que recibe el modelo en producción
               (prompt de sistema, texto de las páginas, imágenes de las páginas),
               construida con services.extraction.build_request
    salida   = el JSON del esquema ExtractionOutput con los valores APROBADOS
               (las correcciones del revisor, no lo que dijo el modelo)

Con include_pending entran también las facturas «Pendiente de aprobación»: las etiquetó el LLM
grande (profesor) o un JSON importado y pasaron TODAS las comprobaciones (sumas, impuestos, NIF,
fechas, duplicados), pero nadie las ha mirado. Cada ejemplo lleva "label_source": "human"
(aprobada) o "teacher" (pendiente), para poder medir aparte. Las de «Necesita revisión» nunca
entran: alguna comprobación falló y el JSON probablemente tiene un error.

Estructura de la carpeta exportada (se copia tal cual al pod de RunPod):

    dataset/
      manifest.json              versiones de prompt y esquema, recuento, opciones
      train.jsonl  val.jsonl     un ejemplo por línea (formato "messages" con imágenes)
      images/<id>_p<n>.jpg       imágenes de página referenciadas desde los .jsonl
      test/<id>.<pdf|png|...>    facturas de prueba (nunca se entrenan) + <id>.json con
                                 la verdad, en el formato de invoice_agent_evaluate

El reparto train/val/test es determinista (hash del identificador del grupo):
volver a exportar con más facturas no mueve las ya repartidas a otro conjunto,
y las facturas duplicadas (mismo proveedor y número) caen siempre juntas.

Los datos son REALES: la carpeta no debe subirse a git (.gitignore: dataset_*/)
y hay que borrarla del pod al terminar.
"""

import hashlib
import json
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.db.models import Q
from django.utils import timezone

from invoice_agent.models import (
    AccionRevision,
    Borrador,
    EstadoDocumento,
    EventoRevision,
)
from invoice_agent.prompts import PROMPT_VERSION
from invoice_agent.schemas import (
    FIELD_TO_COLUMN,
    HEADER_FIELDS,
    SCHEMA_VERSION,
    ExtractionOutput,
)
from invoice_agent.services.extraction import build_request
from invoice_agent.services.preparation import PreparationError, prepare
from invoice_agent.services.storage import path_for

DATASET_FORMAT = "factura-ia-sft/1"
AMOUNT_COLUMNS = {"subtotal_neto", "descuentos", "cargos", "total_impuestos", "total_retenciones", "total_bruto",
                  "importe_a_pagar"}


# --------------------------------------------------------------------------- #
# Formato de valores (igual que el que se pide al modelo en el prompt)
# --------------------------------------------------------------------------- #
def number_text(value: Decimal | None, places: int | None = None) -> str | None:
    """Decimal -> "1234.56" (importes con 2 decimales) o "10" / "2.5" (cantidades y tipos sin ceros sobrantes)."""
    if value is None:
        return None
    if places is not None:
        return f"{value.quantize(Decimal(1).scaleb(-places)):f}"
    return f"{value.normalize():f}"


def value_text(column: str, value) -> str | None:
    if value in (None, ""):
        return None
    if column in AMOUNT_COLUMNS:
        return number_text(value, 2)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# --------------------------------------------------------------------------- #
# Evidencias: página y texto donde aparece cada valor
# --------------------------------------------------------------------------- #
def _spanish_amount(value: Decimal) -> str:
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return ("-" if value < 0 else "") + ".".join(groups) + "," + frac


def search_candidates(column: str, value) -> list[str]:
    """Formas en que un valor puede estar impreso en la factura."""
    if value in (None, ""):
        return []
    if column in AMOUNT_COLUMNS and isinstance(value, Decimal):
        plain = f"{value:.2f}"
        return [_spanish_amount(value), plain.replace(".", ","), plain]
    if isinstance(value, date):
        return [
            value.strftime("%d/%m/%Y"), value.strftime("%d-%m-%Y"), value.strftime("%d.%m.%Y"),
            value.strftime("%d/%m/%y"), f"{value.day}/{value.month}/{value.year}", value.isoformat(),
        ]
    text = str(value).strip()
    return [text] if len(text) >= 2 else []


def find_evidence(candidates: list[str], pages: list[tuple[int, str]]) -> tuple[int | None, str | None]:
    """Primera línea de alguna página que contiene alguno de los textos; (None, None) si no aparece."""
    for candidate in candidates:
        needle = candidate.lower()
        for number, text in pages:
            for line in text.splitlines():
                if needle in line.lower():
                    return number, " ".join(line.split())[:120]
    return None, None


# --------------------------------------------------------------------------- #
# Salida esperada (target)
# --------------------------------------------------------------------------- #
def _corrected_columns(borrador: Borrador) -> set[str]:
    return set(
        EventoRevision.objects.filter(borrador=borrador, accion=AccionRevision.CORRECTED).values_list("campo", flat=True)
    )


def build_target(borrador: Borrador, pages: list[tuple[int, str]]) -> dict:
    """El JSON correcto para esta factura, validado contra ExtractionOutput."""
    extraccion = borrador.extraccion
    evidencias = (extraccion.evidencias if extraccion else {}) or {}
    corrected = _corrected_columns(borrador)
    output: dict = {}

    for name in HEADER_FIELDS:
        column = FIELD_TO_COLUMN[name]
        if column == "cliente_nif":
            raw = extraccion.cliente_nif if extraccion else ""
        else:
            raw = getattr(borrador, column)
        text = value_text(column, raw)
        if text is None:
            output[name] = {"status": "absent", "value": None, "confidence": "high",
                            "evidence": {"page": None, "text": None}}
            continue
        ev = evidencias.get(name) or {}
        if column not in corrected and ev.get("state") == "present" and ev.get("page"):
            page, snippet = ev.get("page"), ev.get("text")
        else:
            page, snippet = find_evidence(search_candidates(column, raw), pages)
        output[name] = {"status": "present", "value": text, "confidence": "high",
                        "evidence": {"page": page, "text": snippet}}

    lines = []
    for line in borrador.lineas.all():
        page, snippet = find_evidence(search_candidates("descripcion", (line.descripcion or "")[:40]), pages)
        lines.append({
            "description": line.descripcion or None,
            "quantity": number_text(line.cantidad),
            "unit_price": number_text(line.precio_unitario),
            "discount": number_text(line.descuento, 2),
            "tax_rate": number_text(line.tipo_impositivo),
            "line_subtotal": number_text(line.subtotal, 2),
            "line_total": number_text(line.total, 2),
            "unreadable_fields": [],
            "page": page,
            "source_text": snippet,
        })
    taxes = [
        {
            "kind": tax.tipo,
            "rate": number_text(tax.porcentaje),
            "base": number_text(tax.base, 2),
            "amount": number_text(tax.cuota, 2),
            "page": None,
            "source_text": None,
        }
        for tax in borrador.impuestos.all()
    ]
    document_type = ((extraccion.salida_modelo or {}).get("document_type") if extraccion else None) or "invoice"
    output.update(document_type=document_type, line_items=lines, tax_breakdown=taxes, notes=[])
    return ExtractionOutput.model_validate(output).model_dump(mode="json")


def truth_for_evaluation(target: dict, borrador: Borrador, duplicate_group: str | None) -> dict:
    """La verdad en el formato de evaluation/runner.py (para el conjunto de prueba)."""
    return {
        "fields": {name: target[name]["value"] for name in HEADER_FIELDS},
        "lines": len(target["line_items"]),
        "taxes": [{k: t[k] for k in ("kind", "rate", "base", "amount")} for t in target["tax_breakdown"]],
        "reconciles": True,  # aprobada: sin errores de conciliación pendientes
        "duplicate_group": duplicate_group,
        "empresa": {"nombre": borrador.empresa.nombre, "nif": borrador.empresa.nif},
    }


# --------------------------------------------------------------------------- #
# Reparto y exportación
# --------------------------------------------------------------------------- #
def split_for(key: str, val: float, test: float) -> str:
    bucket = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if bucket < test:
        return "test"
    if bucket < test + val:
        return "val"
    return "train"


def _group_key(borrador: Borrador, by: str) -> str:
    if by == "supplier" and borrador.proveedor_nif_norm:
        return f"supplier:{borrador.proveedor_nif_norm}"
    if borrador.proveedor_nif_norm and borrador.numero_norm:
        return f"invoice:{borrador.proveedor_nif_norm}:{borrador.numero_norm}"  # duplicados juntos
    return f"doc:{borrador.documento.sha256}"


def _message_content(request) -> list[dict]:
    """Mismo orden que providers/openai_compat.py: texto, y por cada página «Imagen de la página N:» + imagen."""
    content = [{"type": "text", "text": request.user_text}]
    for page, image_path in request.images:
        content.append({"type": "text", "text": f"Imagen de la página {page}:"})
        content.append({"type": "image", "image": image_path})
    return content


@dataclass
class ExportReport:
    folder: Path
    counts: dict = field(default_factory=lambda: {"train": 0, "val": 0, "test": 0})
    skipped: list = field(default_factory=list)
    label_sources: Counter = field(default_factory=Counter)


def label_source(borrador: Borrador) -> str:
    """«human» si una persona aprobó la factura; «teacher» si es la etiqueta del LLM grande sin revisar."""
    return "human" if borrador.documento.estado == EstadoDocumento.APPROVED else "teacher"


def exportable(include_pending: bool = False):
    """Borradores que sirven como ejemplo de entrenamiento."""
    condition = Q(documento__estado=EstadoDocumento.APPROVED, aprobado_en__isnull=False)
    if include_pending:
        condition |= Q(documento__estado=EstadoDocumento.PENDING_APPROVAL, extraccion__isnull=False)
    return Borrador.objects.filter(condition).select_related("documento", "empresa", "extraccion").order_by("pk")


def export_dataset(
    folder: Path,
    *,
    empresa_ids=None,
    val: float = 0.1,
    test: float = 0.15,
    split_by: str = "invoice",
    ocr_engine=None,
    overwrite: bool = False,
    include_pending: bool = False,
) -> ExportReport:
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        if not overwrite:
            raise FileExistsError(f"La carpeta {folder} no está vacía (usa --overwrite).")
        shutil.rmtree(folder)
    (folder / "images").mkdir(parents=True)
    (folder / "test").mkdir()
    report = ExportReport(folder)

    borradores = exportable(include_pending)
    if empresa_ids is not None:
        borradores = borradores.filter(empresa_id__in=empresa_ids)

    handles = {name: open(folder / f"{name}.jsonl", "w", encoding="utf-8") for name in ("train", "val")}
    try:
        for borrador in borradores:
            documento = borrador.documento
            case_id = str(documento.public_id)
            source = path_for(documento.storage_key) if documento.storage_key else None
            if source is None or not source.is_file():
                report.skipped.append({"id": case_id, "motivo": "original borrado (retención)"})
                continue
            group = _group_key(borrador, split_by)
            split = split_for(group, val, test)
            try:
                prepared = prepare(source, documento.tipo_fuente, ocr_engine, vision_supported=True, vision_mode="always")
            except PreparationError as error:
                report.skipped.append({"id": case_id, "motivo": error.message})
                continue
            pages_text = [(p.number, p.text) for p in prepared.pages]
            target = build_target(borrador, pages_text)
            source_kind = label_source(borrador)

            if split == "test":
                shutil.copy(source, folder / "test" / f"{case_id}{source.suffix.lower()}")
                duplicate = group if group.startswith("invoice:") else None
                truth = truth_for_evaluation(target, borrador, duplicate)
                truth["label_source"] = source_kind
                (folder / "test" / f"{case_id}.json").write_text(
                    json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            else:
                image_refs = []
                for page, jpeg in prepared.images:
                    relative = f"images/{case_id}_p{page}.jpg"
                    (folder / relative).write_bytes(jpeg)
                    image_refs.append((page, relative))
                request = build_request(prepared, borrador.empresa.nombre, borrador.empresa.nif, with_images=False)
                request.images = image_refs
                record = {
                    "id": case_id,
                    "label_source": source_kind,
                    "messages": [
                        {"role": "system", "content": [{"type": "text", "text": request.system}]},
                        {"role": "user", "content": _message_content(request)},
                        {"role": "assistant", "content": [
                            {"type": "text", "text": json.dumps(target, ensure_ascii=False, separators=(",", ":"))}
                        ]},
                    ],
                }
                handles[split].write(json.dumps(record, ensure_ascii=False) + "\n")
            report.counts[split] += 1
            report.label_sources[source_kind] += 1
    finally:
        for handle in handles.values():
            handle.close()

    from invoice_agent import conf
    from invoice_agent.schemas import strict_json_schema

    manifest = {
        "format": DATASET_FORMAT,
        "created": timezone.now().isoformat(),
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "vision_mode": "always",
        "vision_max_side_px": conf.get("VISION_MAX_SIDE_PX"),
        "split_by": split_by,
        "fractions": {"val": val, "test": test},
        "counts": report.counts,
        "include_pending": include_pending,
        "label_sources": dict(report.label_sources),
        "skipped": report.skipped,
        "output_schema": strict_json_schema(ExtractionOutput),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

