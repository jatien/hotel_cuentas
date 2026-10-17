"""Conversión de modelos a los esquemas de respuesta de la API (Pydantic)."""

from invoice_agent.models import Borrador, Documento, EventoRevision
from invoice_agent.schemas import (
    DocumentOut,
    DraftOut,
    EventOut,
    ExtractionOut,
    FieldEvidenceOut,
    IssueOut,
    LineOut,
    TaxOut,
)


def document_out(documento: Documento, duplicado: bool = False) -> DocumentOut:
    borrador = Borrador.objects.filter(documento=documento).only("pk").first()
    return DocumentOut(
        id=str(documento.public_id),
        empresa_id=documento.empresa_id,
        empresa=documento.empresa.nombre,
        filename=documento.original_filename,
        mime_type=documento.mime_type,
        size_bytes=documento.size_bytes,
        sha256=documento.sha256,
        page_count=documento.page_count,
        estado=documento.estado,
        estado_label=documento.get_estado_display(),
        error_code=documento.error_code,
        error_message=documento.error_message,
        borrador_id=borrador.pk if borrador else None,
        creado=documento.creado,
        duplicado=duplicado,
    )


def issues_out(borrador_or_issues, acknowledged=()) -> list[IssueOut]:
    issues = borrador_or_issues
    if isinstance(borrador_or_issues, Borrador):
        issues, acknowledged = borrador_or_issues.incidencias, borrador_or_issues.avisos_aceptados
    return [
        IssueOut(
            code=i["code"],
            severity=i["severity"],
            blocking=i["blocking"],
            field=i.get("field"),
            message=i["message"],
            details=i.get("details"),
            acknowledged=i["code"] in acknowledged and i["severity"] == "warning",
        )
        for i in issues or []
    ]


def draft_out(borrador: Borrador) -> DraftOut:
    data = {f: getattr(borrador, f) for f in DraftOut.model_fields if hasattr(borrador, f)}
    data.update(
        id=borrador.pk,
        estado=borrador.documento.estado,
        lineas=[LineOut.model_validate(line) for line in borrador.lineas.all()],
        impuestos=[TaxOut.model_validate(tax) for tax in borrador.impuestos.all()],
        incidencias=issues_out(borrador),
        aprobado_por=borrador.aprobado_por_label,
    )
    return DraftOut(**data)


def extraction_out(documento: Documento) -> ExtractionOut:
    extraccion = documento.extracciones.filter(actual=True).select_related("intento").first()
    borrador = Borrador.objects.filter(documento=documento).first()
    evidencias = {}
    if extraccion:
        for name, ev in (extraccion.evidencias or {}).items():
            evidencias[name] = FieldEvidenceOut(
                state=ev.get("state", ""),
                confidence=ev.get("confidence"),
                page=ev.get("page"),
                text=ev.get("text"),
                extracted_value=ev.get("extracted_value") if ev.get("extracted_value") is not None else ev.get("raw"),
            )
    return ExtractionOut(
        documento=document_out(documento),
        extraccion_id=extraccion.pk if extraccion else None,
        modelo=extraccion.intento.modelo if extraccion else None,
        prompt_version=extraccion.intento.prompt_version if extraccion else None,
        schema_version=extraccion.intento.schema_version if extraccion else None,
        metodo=extraccion.intento.metodo if extraccion else None,
        evidencias=evidencias,
        validacion=issues_out((extraccion.validacion or {}).get("issues", [])) if extraccion else [],
        borrador=draft_out(borrador) if borrador else None,
    )


def event_out(evento: EventoRevision) -> EventOut:
    return EventOut(
        accion=evento.accion,
        campo=evento.campo,
        valor_extraido=evento.valor_extraido,
        valor_anterior=evento.valor_anterior,
        valor_nuevo=evento.valor_nuevo,
        estado_anterior=evento.estado_anterior,
        estado_nuevo=evento.estado_nuevo,
        comentario=evento.comentario,
        actor=evento.actor_label,
        creado=evento.creado,
    )
