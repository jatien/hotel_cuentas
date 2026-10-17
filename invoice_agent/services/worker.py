"""
Worker de la cola en base de datos (bandeja de salida transaccional).

claim_next()  toma el siguiente trabajo pendiente con SELECT ... FOR UPDATE
              SKIP LOCKED (varios workers no cogen el mismo), respeta
              MAX_CONCURRENT_JOBS y recupera trabajos "running" cuyo
              arrendamiento (JOB_LEASE_SECONDS) caducó porque el worker murió.
run_claimed() prepara + extrae FUERA de transacción (lento) y guarda el
              resultado en UNA transacción, comprobando antes que el trabajo
              sigue siendo suyo: una entrega repetida o un worker zombi no
              crean extracciones ni borradores duplicados.

Fallos: transitorio -> se reprograma con espera creciente hasta
max_intentos; definitivo o agotado -> trabajo "dead" y documento "failed"
(cola de fallidos visible en la pantalla y en la API).
"""

import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from invoice_agent import conf
from invoice_agent.models import (
    AccionRevision,
    Borrador,
    Documento,
    EstadoDocumento,
    EstadoTrabajo,
    EventoRevision,
    Extraccion,
    ExtraccionImpuesto,
    ExtraccionLinea,
    Intento,
    ResultadoIntento,
    Trabajo,
)
from invoice_agent.providers import get_provider
from invoice_agent.schemas import FIELD_TO_COLUMN
from invoice_agent.services import drafts, states
from invoice_agent.services.extraction import ExtractionFailed, run_extraction
from invoice_agent.services.normalize import from_model_output
from invoice_agent.services.observability import log_event
from invoice_agent.services.preparation import PreparationError, prepare
from invoice_agent.services.storage import path_for

CLAIM_LOCK_KEY = 0x1A_F0_0D  # clave del bloqueo consultivo de PostgreSQL para el límite de concurrencia
WORKER_LABEL = "worker"


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


@dataclass
class Claimed:
    trabajo: Trabajo
    intento: Intento
    worker_id: str
    started: float


def claim_next(worker_id: str) -> Claimed | None:
    now = timezone.now()
    lease = timedelta(seconds=int(conf.get("JOB_LEASE_SECONDS")))
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CLAIM_LOCK_KEY])
        running = Trabajo.objects.filter(estado=EstadoTrabajo.RUNNING, bloqueado_en__gte=now - lease).count()
        if running >= int(conf.get("MAX_CONCURRENT_JOBS")):
            return None
        trabajo = (
            Trabajo.objects.select_for_update(skip_locked=True)
            .filter(
                Q(estado=EstadoTrabajo.PENDING, ejecutar_desde__lte=now)
                | Q(estado=EstadoTrabajo.RUNNING, bloqueado_en__lt=now - lease)
            )
            .order_by("ejecutar_desde", "id")
            .first()
        )
        if trabajo is None:
            return None
        recovered = trabajo.estado == EstadoTrabajo.RUNNING
        documento = Documento.objects.select_for_update().get(pk=trabajo.documento_id)

        if trabajo.intentos >= trabajo.max_intentos:
            # Un worker murió tantas veces con este trabajo que ya no se reintenta.
            _finish_dead(trabajo, documento, "MAX_ATTEMPTS", "Se agotaron los intentos de procesado.")
            return None

        trabajo.estado = EstadoTrabajo.RUNNING
        trabajo.intentos += 1
        trabajo.bloqueado_en = now
        trabajo.bloqueado_por = worker_id
        trabajo.save(update_fields=["estado", "intentos", "bloqueado_en", "bloqueado_por"])

        if documento.estado == EstadoDocumento.QUEUED:
            states.transition(documento, EstadoDocumento.PROCESSING, actor_label=WORKER_LABEL)
        elif not (recovered and documento.estado == EstadoDocumento.PROCESSING):
            # El documento ya no espera procesado (p. ej. se aprobó por otra vía): trabajo obsoleto.
            trabajo.estado, trabajo.terminado = EstadoTrabajo.DONE, now
            trabajo.ultimo_error = f"Obsoleto: documento en estado {documento.estado}"
            trabajo.save(update_fields=["estado", "terminado", "ultimo_error"])
            return None

        numero = Intento.objects.filter(documento=documento).count() + 1
        intento = Intento.objects.create(documento=documento, trabajo=trabajo, numero=numero)
    log_event(
        "job.claimed",
        trabajo=trabajo.pk,
        documento=documento.public_id,
        correlation_id=documento.correlation_id,
        intento=intento.numero,
        recovered=recovered,
        worker=worker_id,
    )
    return Claimed(trabajo, intento, worker_id, time.monotonic())


def _finish_dead(trabajo: Trabajo, documento: Documento, code: str, message: str) -> None:
    trabajo.estado, trabajo.terminado, trabajo.ultimo_error = EstadoTrabajo.DEAD, timezone.now(), f"{code}: {message}"
    trabajo.save(update_fields=["estado", "terminado", "ultimo_error"])
    if states.can_transition(documento.estado, EstadoDocumento.FAILED):
        states.transition(
            documento, EstadoDocumento.FAILED, actor_label=WORKER_LABEL, error_code=code, error_message=message
        )


def _owns(trabajo: Trabajo, worker_id: str) -> Trabajo | None:
    """Relee el trabajo bloqueándolo; None si ya no es de este worker (arrendamiento perdido o entrega repetida)."""
    fresh = Trabajo.objects.select_for_update().get(pk=trabajo.pk)
    if fresh.estado != EstadoTrabajo.RUNNING or fresh.bloqueado_por != worker_id or fresh.intentos != trabajo.intentos:
        log_event("job.lost_ownership", trabajo=trabajo.pk, worker=worker_id)
        return None
    return fresh


def _record_metrics(intento: Intento, claimed: Claimed, stats, prepared, resultado, code="", message="") -> None:
    intento.resultado = resultado
    intento.error_code, intento.error_message = code, message[:2000]
    intento.fin = timezone.now()
    intento.duracion_ms = int((time.monotonic() - claimed.started) * 1000)
    if stats is not None:
        intento.llamadas_modelo = stats.calls
        intento.tokens_entrada, intento.tokens_salida = stats.input_tokens, stats.output_tokens
        intento.coste_estimado = stats.cost()
        intento.modelo = stats.model or intento.modelo
    if prepared is not None:
        intento.metodo = prepared.method
        intento.preparacion = prepared.metadata()
    intento.save()


def _fail(claimed: Claimed, code: str, message: str, retryable: bool, stats=None, prepared=None) -> None:
    with transaction.atomic():
        trabajo = _owns(claimed.trabajo, claimed.worker_id)
        if trabajo is None:
            return
        documento = Documento.objects.select_for_update().get(pk=trabajo.documento_id)
        resultado = ResultadoIntento.RETRYABLE_ERROR if retryable else ResultadoIntento.FATAL_ERROR
        _record_metrics(claimed.intento, claimed, stats, prepared, resultado, code, message)
        if retryable and trabajo.intentos < trabajo.max_intentos:
            delay = int(conf.get("JOB_RETRY_DELAY_SECONDS")) * (2 ** (trabajo.intentos - 1))
            trabajo.estado = EstadoTrabajo.PENDING
            trabajo.ejecutar_desde = timezone.now() + timedelta(seconds=delay)
            trabajo.bloqueado_en, trabajo.bloqueado_por = None, ""
            trabajo.ultimo_error = f"{code}: {message}"[:2000]
            trabajo.save()
            states.transition(
                documento, EstadoDocumento.QUEUED, actor_label=WORKER_LABEL,
                comentario=f"Reintento programado en {delay} s", error_code=code, error_message=message,
            )
        else:
            _finish_dead(trabajo, documento, code, message)
    log_event(
        "job.failed",
        trabajo=claimed.trabajo.pk,
        documento=claimed.trabajo.documento.public_id,
        correlation_id=claimed.trabajo.documento.correlation_id,
        code=code,
        retryable=retryable,
        intento=claimed.intento.numero,
    )


def _persist_success(claimed: Claimed, prepared, result) -> None:
    data = from_model_output(result.output)
    evidence = drafts.evidence_dict(data)
    with transaction.atomic():
        trabajo = _owns(claimed.trabajo, claimed.worker_id)
        if trabajo is None:
            return
        documento = Documento.objects.select_for_update(of=("self",)).select_related("empresa").get(pk=trabajo.documento_id)
        if documento.estado != EstadoDocumento.PROCESSING:
            trabajo.estado, trabajo.terminado = EstadoTrabajo.DONE, timezone.now()
            trabajo.ultimo_error = f"Resultado descartado: documento en estado {documento.estado}"
            trabajo.save()
            return

        intento = claimed.intento
        intento.prompt_version, intento.schema_version = result.prompt_version, result.schema_version
        _record_metrics(intento, claimed, result.stats, prepared, ResultadoIntento.SUCCESS)

        Extraccion.objects.filter(documento=documento, actual=True).update(actual=False)
        header = {}
        for name, fv in data.fields.items():
            if not fv.usable:
                continue
            column = FIELD_TO_COLUMN[name]
            value = drafts.fit(Extraccion, column, fv.value)
            if value is not None:
                header[column] = value
        extraccion = Extraccion.objects.create(
            documento=documento,
            intento=intento,
            evidencias=evidence,
            salida_modelo=result.raw,
            **header,
        )
        ExtraccionLinea.objects.bulk_create(
            ExtraccionLinea(
                extraccion=extraccion,
                orden=index,
                pagina=line.page if line.page and line.page > 0 else None,
                **{
                    column: drafts.fit(ExtraccionLinea, column, value)
                    for column, value in (
                        ("descripcion", line.description),
                        ("cantidad", line.quantity),
                        ("precio_unitario", line.unit_price),
                        ("descuento", line.discount),
                        ("tipo_impositivo", line.tax_rate),
                        ("subtotal", line.subtotal),
                        ("total", line.total),
                    )
                },
            )
            for index, line in enumerate(data.lines)
        )
        ExtraccionImpuesto.objects.bulk_create(
            ExtraccionImpuesto(
                extraccion=extraccion,
                tipo=t.kind,
                porcentaje=drafts.fit(ExtraccionImpuesto, "porcentaje", t.rate),
                base=drafts.fit(ExtraccionImpuesto, "base", t.base),
                cuota=drafts.fit(ExtraccionImpuesto, "cuota", t.amount),
            )
            for t in data.taxes
        )

        borrador, _ = Borrador.objects.get_or_create(documento=documento, defaults={"empresa": documento.empresa})
        drafts.fill_from_extraction(borrador, extraccion)
        _link_known_supplier(borrador)
        issues = drafts.revalidate(borrador)
        extraccion.validacion = {"issues": issues}
        extraccion.save(update_fields=["validacion"])

        EventoRevision.objects.create(
            documento=documento,
            borrador=borrador,
            accion=AccionRevision.EXTRACTED,
            comentario=f"Intento {intento.numero} · {intento.modelo} · {intento.prompt_version}",
            actor_label=WORKER_LABEL,
        )
        target = EstadoDocumento.NEEDS_REVIEW if drafts.blockers(borrador) else EstadoDocumento.PENDING_APPROVAL
        states.transition(documento, target, actor_label=WORKER_LABEL)
        trabajo.estado, trabajo.terminado, trabajo.ultimo_error = EstadoTrabajo.DONE, timezone.now(), ""
        trabajo.save()
    log_event(
        "job.succeeded",
        trabajo=trabajo.pk,
        documento=documento.public_id,
        correlation_id=documento.correlation_id,
        estado=target,
        metodo=prepared.method,
        issues=len(issues),
        duracion_ms=intento.duracion_ms,
        tokens_in=intento.tokens_entrada,
        tokens_out=intento.tokens_salida,
        coste=intento.coste_estimado,
    )


def _link_known_supplier(borrador: Borrador) -> None:
    """Enlaza con un Proveedor ya existente de invoice_data (no crea ninguno: eso solo al aprobar)."""
    from invoice_data.models import Proveedor

    if borrador.proveedor_nif_norm:
        borrador.proveedor = Proveedor.objects.filter(cif=borrador.proveedor_nif_norm).first()
        borrador.save(update_fields=["proveedor"])


def run_claimed(claimed: Claimed, *, provider=None, ocr_engine=None, sleep=time.sleep) -> None:
    provider = provider or get_provider()
    documento = Documento.objects.select_related("empresa").get(pk=claimed.trabajo.documento_id)
    claimed.intento.modelo = provider.model
    claimed.intento.proveedor_ia = provider.name
    claimed.intento.save(update_fields=["modelo", "proveedor_ia"])
    prepared = None
    try:
        prepared = prepare(
            path_for(documento.storage_key), documento.tipo_fuente, ocr_engine, provider.supports_vision
        )
        result = run_extraction(
            prepared,
            empresa_nombre=documento.empresa.nombre,
            empresa_nif=documento.empresa.nif,
            provider=provider,
            sleep=sleep,
        )
    except PreparationError as error:
        _fail(claimed, error.code, error.message, error.retryable, prepared=prepared)
        return
    except ExtractionFailed as error:
        _fail(claimed, error.code, error.message, error.retryable, stats=error.stats, prepared=prepared)
        return
    except Exception as error:  # fallo inesperado: se registra sin contenido del documento y se reintenta
        log_event("job.unexpected_error", trabajo=claimed.trabajo.pk, error=type(error).__name__)
        _fail(claimed, "INTERNAL_ERROR", type(error).__name__, True, prepared=prepared)
        return
    _persist_success(claimed, prepared, result)


def run_once(worker_id: str | None = None, **kwargs) -> bool:
    """Procesa un trabajo si hay alguno. Devuelve True si procesó algo."""
    claimed = claim_next(worker_id or default_worker_id())
    if claimed is None:
        return False
    run_claimed(claimed, **kwargs)
    return True
