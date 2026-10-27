"""
Etiquetas (un <nombre>.json por factura, esquema factura-ia/1) escritas desde la base de datos.

Es el camino de vuelta de `invoice_agent_import --labels`: las facturas que etiquetó el LLM grande
(profesor) o que corrigió una persona se guardan como JSON junto a sus PDF (p. ej. en la carpeta
de Google Drive), para que la carpeta PDF + JSON sea siempre el conjunto completo y se pueda
reconstruir el dataset en cualquier equipo.

Qué JSON se escribe:
- sin correcciones: la salida original del modelo (o del JSON importado), tal cual
- con correcciones de una persona: los valores revisados (build_target), que sustituyen al JSON
  anterior aunque ya exista en la carpeta
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from invoice_agent.models import Borrador, EstadoDocumento
from invoice_agent.schemas import ExtractionOutput
from invoice_agent.services.dataset import _corrected_columns, build_target

TRUSTED_STATES = (EstadoDocumento.APPROVED, EstadoDocumento.PENDING_APPROVAL)


@dataclass
class LabelsReport:
    folder: Path
    written: list = field(default_factory=list)
    replaced: list = field(default_factory=list)
    kept: int = 0


def label_for(borrador: Borrador) -> tuple[dict, bool]:
    """(JSON de la factura, True si lleva correcciones de una persona)."""
    extraccion = borrador.extraccion
    corrected = bool(_corrected_columns(borrador))
    if extraccion is not None and extraccion.salida_modelo and not corrected:
        try:
            return ExtractionOutput.model_validate(extraccion.salida_modelo).model_dump(mode="json"), False
        except ValidationError:
            pass  # salida antigua con otro esquema: se reconstruye desde el borrador
    return build_target(borrador, []), corrected


def export_labels(folder: Path, *, states=TRUSTED_STATES, empresa_ids=None) -> LabelsReport:
    """
    Escribe <nombre original>.json en `folder` para cada factura en `states`.

    No pisa un JSON que ya exista (es la etiqueta original del usuario), salvo que la factura tenga
    correcciones de una persona: entonces la versión revisada sustituye a la anterior.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    report = LabelsReport(folder)
    borradores = (
        Borrador.objects.filter(documento__estado__in=states)
        .select_related("documento", "extraccion")
        .order_by("pk")
    )
    if empresa_ids is not None:
        borradores = borradores.filter(empresa_id__in=empresa_ids)
    used: set[str] = set()
    for borrador in borradores:
        documento = borrador.documento
        name = f"{Path(documento.original_filename).stem}.json"
        if name in used:  # dos facturas con el mismo nombre en carpetas distintas
            name = f"{Path(documento.original_filename).stem}__{str(documento.public_id)[:8]}.json"
        used.add(name)
        target = folder / name
        label, corrected = label_for(borrador)
        text = json.dumps(label, ensure_ascii=False, indent=2)
        if target.exists() and (not corrected or target.read_text(encoding="utf-8") == text):
            report.kept += 1
            continue
        (report.replaced if target.exists() else report.written).append(name)
        target.write_text(text, encoding="utf-8")
    return report
