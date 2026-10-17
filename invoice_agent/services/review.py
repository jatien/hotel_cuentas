"""
Etapa F: revisión humana.

apply_patch()       corrige campos, líneas o impuestos y acepta avisos; cada
                    cambio queda en EventoRevision con el valor extraído, el
                    anterior y el nuevo. Tras corregir se revalida y el
                    documento pasa a needs_review o pending_approval.
approve()           aprueba (vuelve a validar justo antes); NO contabiliza.
reject()            rechaza.
request_reprocess() vuelve a poner el documento en cola sin crear otro borrador.

Todas las funciones reciben el AccessContext y comprueban la empresa.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from invoice_agent import conf
from invoice_agent.models import (
    AccionRevision,
    Borrador,
    BorradorImpuesto,
    BorradorLinea,
    Documento,
    EstadoDocumento,
    EstadoTrabajo,
    EventoRevision,
    Trabajo,
)
from invoice_agent.schemas import DecisionIn, DraftPatch, ReprocessIn
from invoice_agent.services import drafts, states
from invoice_agent.services.access import AccessContext, scoped
from invoice_agent.services.errors import Conflict, Forbidden, NotFound, ServiceError
from invoice_agent.services.observability import log_event


def get_document(ctx: AccessContext, public_id) -> Documento:
    documento = scoped(Documento.objects.select_related("empresa"), ctx).filter(public_id=public_id).first()
    if documento is None:
        raise NotFound("NOT_FOUND", "Documento no encontrado.")
    return documento


def get_draft(ctx: AccessContext, draft_id) -> Borrador:
    borrador = (
        scoped(Borrador.objects.select_related("documento", "empresa", "extraccion"), ctx)
        .filter(pk=draft_id)
        .first()
    )
    if borrador is None:
        raise NotFound("NOT_FOUND", "Borrador no encontrado.")
    return borrador


def _lock(ctx: AccessContext, borrador: Borrador) -> tuple[Borrador, Documento]:
    documento = Documento.objects.select_for_update().get(pk=borrador.documento_id)
    fresh = (
        Borrador.objects.select_for_update(of=("self",))
        .select_related("empresa", "extraccion")
        .get(pk=borrador.pk)
    )
    ctx.check_empresa(fresh.empresa_id)
    return fresh, documento


def _event(documento, borrador, ctx, accion, **fields):
    EventoRevision.objects.create(
        documento=documento,
        borrador=borrador,
        accion=accion,
        actor=ctx.user if ctx.user_id else None,
        actor_label=ctx.actor_label,
        **fields,
    )


def _lines_snapshot(rows, columns) -> list[dict]:
    return [{c: drafts.json_value(getattr(row, c)) for c in columns} for row in rows]


def _sync_state(documento: Documento, borrador: Borrador, ctx: AccessContext) -> None:
    pending = drafts.blockers(borrador)
    if pending and documento.estado == EstadoDocumento.PENDING_APPROVAL:
        states.transition(documento, EstadoDocumento.NEEDS_REVIEW, actor_label=ctx.actor_label, actor=ctx.user)
    elif not pending and documento.estado == EstadoDocumento.NEEDS_REVIEW:
        states.transition(documento, EstadoDocumento.PENDING_APPROVAL, actor_label=ctx.actor_label, actor=ctx.user)


def apply_patch(ctx: AccessContext, borrador: Borrador, patch: DraftPatch) -> Borrador:
    changes = patch.model_dump(exclude_unset=True)
    comentario = changes.pop("comentario", "")
    acknowledge = changes.pop("aceptar_avisos", None)
    new_lines = changes.pop("lineas", None)
    new_taxes = changes.pop("impuestos", None)

    with transaction.atomic():
        borrador, documento = _lock(ctx, borrador)
        if documento.estado not in states.EDITABLE:
            raise Conflict("NOT_EDITABLE", f"No se puede corregir un documento en estado «{documento.get_estado_display()}».")
        evidencias = borrador.extraccion.evidencias if borrador.extraccion else {}
        changed = False

        for column, value in changes.items():
            field = Borrador._meta.get_field(column)
            if value is None and field.get_internal_type() == "CharField":
                value = ""
            if isinstance(value, str):
                value = value.strip()[: field.max_length]
            old = getattr(borrador, column)
            if drafts.json_value(old) == drafts.json_value(value):
                continue
            if value is not None and drafts.fit(Borrador, column, value) is None:
                raise ServiceError("VALUE_OUT_OF_RANGE", f"Valor fuera de rango para «{drafts.column_label(column)}».", 422)
            setattr(borrador, column, value)
            name = drafts.COLUMN_TO_FIELD.get(column)
            _event(
                documento,
                borrador,
                ctx,
                AccionRevision.CORRECTED,
                campo=column,
                valor_extraido=(evidencias.get(name) or {}).get("extracted_value"),
                valor_anterior=drafts.json_value(old),
                valor_nuevo=drafts.json_value(value),
                comentario=comentario,
            )
            changed = True

        if new_lines is not None:
            old = _lines_snapshot(borrador.lineas.all(), drafts.LINE_COLUMNS)
            rows = [BorradorLinea(borrador=borrador, orden=i, **line) for i, line in enumerate(new_lines)]
            for row in rows:
                for column in drafts.LINE_COLUMNS:
                    if getattr(row, column) is not None and drafts.fit(BorradorLinea, column, getattr(row, column)) is None:
                        raise ServiceError("VALUE_OUT_OF_RANGE", f"Valor fuera de rango en una línea ({column}).", 422)
            new = _lines_snapshot(rows, drafts.LINE_COLUMNS)
            if old != new:
                borrador.lineas.all().delete()
                BorradorLinea.objects.bulk_create(rows)
                extracted = (
                    _lines_snapshot(borrador.extraccion.lineas.all(), drafts.LINE_COLUMNS) if borrador.extraccion else None
                )
                _event(documento, borrador, ctx, AccionRevision.CORRECTED, campo="lineas",
                       valor_extraido=extracted, valor_anterior=old, valor_nuevo=new, comentario=comentario)
                changed = True

        if new_taxes is not None:
            old = _lines_snapshot(borrador.impuestos.all(), drafts.TAX_COLUMNS)
            rows = [BorradorImpuesto(borrador=borrador, **tax) for tax in new_taxes]
            for row in rows:
                for column in ("porcentaje", "base", "cuota"):
                    if getattr(row, column) is not None and drafts.fit(BorradorImpuesto, column, getattr(row, column)) is None:
                        raise ServiceError("VALUE_OUT_OF_RANGE", f"Valor fuera de rango en un impuesto ({column}).", 422)
            new = _lines_snapshot(rows, drafts.TAX_COLUMNS)
            if old != new:
                borrador.impuestos.all().delete()
                BorradorImpuesto.objects.bulk_create(rows)
                extracted = (
                    _lines_snapshot(borrador.extraccion.impuestos.all(), drafts.TAX_COLUMNS) if borrador.extraccion else None
                )
                _event(documento, borrador, ctx, AccionRevision.CORRECTED, campo="impuestos",
                       valor_extraido=extracted, valor_anterior=old, valor_nuevo=new, comentario=comentario)
                changed = True

        if changed:
            borrador.corregido = True
            drafts.update_keys(borrador)
            borrador.save()
        issues = drafts.revalidate(borrador)

        if acknowledge:
            acknowledgeable = {i["code"] for i in issues if i["severity"] == "warning" and i["blocking"]}
            unknown = set(acknowledge) - acknowledgeable
            if unknown:
                raise ServiceError(
                    "CANNOT_ACKNOWLEDGE",
                    "Solo se pueden aceptar avisos de revisión vigentes; los errores hay que corregirlos.",
                    422,
                    sorted(unknown),
                )
            for code in sorted(set(acknowledge) - set(borrador.avisos_aceptados)):
                borrador.avisos_aceptados.append(code)
                _event(documento, borrador, ctx, AccionRevision.ACKNOWLEDGED, campo=code, comentario=comentario)
            borrador.save(update_fields=["avisos_aceptados", "actualizado"])

        _sync_state(documento, borrador, ctx)
    return borrador


def approve(ctx: AccessContext, borrador: Borrador, decision: DecisionIn) -> Borrador:
    if not ctx.can_approve:
        raise Forbidden("FORBIDDEN", "No tienes permiso para aprobar facturas.")
    with transaction.atomic():
        borrador, documento = _lock(ctx, borrador)
        if documento.estado != EstadoDocumento.PENDING_APPROVAL:
            raise Conflict("NOT_PENDING_APPROVAL", "Solo se aprueban documentos pendientes de aprobación.")
        drafts.revalidate(borrador)  # los duplicados pueden haber cambiado desde la última revisión
        pending = drafts.blockers(borrador)
        if pending:
            states.transition(documento, EstadoDocumento.NEEDS_REVIEW, actor_label=ctx.actor_label, actor=ctx.user)
            blocked = ServiceError("VALIDATION_BLOCKED", "La factura tiene incidencias pendientes.", 409, pending)
        else:
            blocked = None
            borrador.proveedor = _supplier_for(borrador)
            borrador.aprobado_en = timezone.now()
            borrador.aprobado_por = ctx.user if ctx.user_id else None
            borrador.aprobado_por_label = ctx.actor_label[:150]
            try:
                with transaction.atomic():
                    borrador.save()
            except IntegrityError as error:
                raise Conflict("DUPLICATE_APPROVED", "Ya hay una factura aprobada con el mismo proveedor y número.") from error
            states.transition(
                documento, EstadoDocumento.APPROVED, actor_label=ctx.actor_label, actor=ctx.user, comentario=decision.comentario
            )
            _event(documento, borrador, ctx, AccionRevision.APPROVED, comentario=decision.comentario)
    if blocked:
        raise blocked
    log_event("draft.approved", documento=documento.public_id, correlation_id=documento.correlation_id, actor=ctx.actor_label)
    return borrador


def _supplier_for(borrador: Borrador):
    """Proveedor de invoice_data (catálogo común de proveedores por CIF); se crea si no existe."""
    if not borrador.proveedor_nif_norm:
        return borrador.proveedor
    from invoice_data.services.pipeline import supplier_for

    return supplier_for(borrador.proveedor_nif_norm, borrador.proveedor_nombre)


def reject(ctx: AccessContext, borrador: Borrador, decision: DecisionIn) -> Borrador:
    if not ctx.can_approve:
        raise Forbidden("FORBIDDEN", "No tienes permiso para rechazar facturas.")
    with transaction.atomic():
        borrador, documento = _lock(ctx, borrador)
        if not states.can_transition(documento.estado, EstadoDocumento.REJECTED):
            raise Conflict("NOT_REJECTABLE", f"No se puede rechazar un documento en estado «{documento.get_estado_display()}».")
        borrador.rechazado_en = timezone.now()
        borrador.motivo_rechazo = decision.comentario
        borrador.save(update_fields=["rechazado_en", "motivo_rechazo", "actualizado"])
        states.transition(
            documento, EstadoDocumento.REJECTED, actor_label=ctx.actor_label, actor=ctx.user, comentario=decision.comentario
        )
        _event(documento, borrador, ctx, AccionRevision.REJECTED, comentario=decision.comentario)
    return borrador


def request_reprocess(ctx: AccessContext, documento: Documento, data: ReprocessIn) -> Documento:
    with transaction.atomic():
        documento = Documento.objects.select_for_update().get(pk=documento.pk)
        ctx.check_empresa(documento.empresa_id)
        if documento.estado not in states.REPROCESSABLE:
            raise Conflict("NOT_REPROCESSABLE", f"No se puede reprocesar un documento en estado «{documento.get_estado_display()}».")
        if not documento.storage_key:
            raise Conflict("ORIGINAL_DELETED", "El original se borró por la política de retención.")
        borrador = Borrador.objects.filter(documento=documento).first()
        if borrador is not None and borrador.corregido and not data.descartar_correcciones:
            raise Conflict(
                "HAS_CORRECTIONS",
                "El borrador tiene correcciones manuales; reprocesar las sustituiría. "
                "Confirma con descartar_correcciones=true.",
            )
        if Trabajo.objects.filter(documento=documento, estado__in=[EstadoTrabajo.PENDING, EstadoTrabajo.RUNNING]).exists():
            raise Conflict("ALREADY_QUEUED", "El documento ya está en cola.")
        if borrador is not None:
            borrador.rechazado_en, borrador.motivo_rechazo = None, ""
            borrador.save(update_fields=["rechazado_en", "motivo_rechazo", "actualizado"])
        Trabajo.objects.create(
            documento=documento, max_intentos=int(conf.get("JOB_MAX_ATTEMPTS")), ejecutar_desde=timezone.now()
        )
        _event(documento, borrador, ctx, AccionRevision.REPROCESS, comentario=data.comentario)
        states.transition(documento, EstadoDocumento.QUEUED, actor_label=ctx.actor_label, actor=ctx.user,
                          comentario=data.comentario)
    return documento
