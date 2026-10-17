"""
Máquina de estados del documento.

    queued ──► processing ──► needs_review ──► pending_approval ──► approved
      ▲            │  │            │   ▲              │
      │            │  └──► failed  │   └──────────────┤ (una corrección reabre la revisión)
      │            │               ▼                  ▼
      │            └─(reintento)─► queued          rejected
      └──────────── reprocess (desde failed, needs_review, pending_approval, rejected)

- approved es final: no se reprocesa ni se corrige.
- Solo una persona aprueba o rechaza; el worker nunca pasa a approved.

Los cambios de estado son condicionales (UPDATE ... WHERE estado = <actual>),
así dos peticiones simultáneas no pueden aplicar la misma transición dos veces.
"""

from django.db import transaction
from django.utils import timezone

from invoice_agent.models import AccionRevision, Borrador, Documento, EventoRevision
from invoice_agent.models import EstadoDocumento as E

TRANSITIONS: dict[str, set[str]] = {
    E.QUEUED: {E.PROCESSING},
    E.PROCESSING: {E.NEEDS_REVIEW, E.PENDING_APPROVAL, E.FAILED, E.QUEUED},
    E.NEEDS_REVIEW: {E.PENDING_APPROVAL, E.REJECTED, E.QUEUED},
    E.PENDING_APPROVAL: {E.APPROVED, E.REJECTED, E.NEEDS_REVIEW, E.QUEUED},
    E.FAILED: {E.QUEUED},
    E.REJECTED: {E.QUEUED},
    E.APPROVED: set(),
}

REPROCESSABLE = {E.FAILED, E.NEEDS_REVIEW, E.PENDING_APPROVAL, E.REJECTED}
EDITABLE = {E.NEEDS_REVIEW, E.PENDING_APPROVAL}


class InvalidTransition(Exception):
    """La transición no está permitida o el estado cambió mientras tanto."""

    def __init__(self, current: str, target: str):
        self.current, self.target = current, target
        super().__init__(f"No se puede pasar de «{current}» a «{target}».")


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def transition(
    documento: Documento,
    target: str,
    *,
    actor_label: str,
    actor=None,
    comentario: str = "",
    error_code: str = "",
    error_message: str = "",
) -> None:
    """
    Aplica una transición de forma atómica y la deja en la auditoría.
    Lanza InvalidTransition si no está permitida o si otro proceso cambió el
    estado antes. Actualiza el objeto en memoria.
    """
    current = documento.estado
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    with transaction.atomic():
        updated = Documento.objects.filter(pk=documento.pk, estado=current).update(
            estado=target,
            estado_cambiado=timezone.now(),
            error_code=error_code,
            error_message=error_message[:2000],
        )
        if updated != 1:
            fresh = Documento.objects.only("estado").get(pk=documento.pk)
            raise InvalidTransition(fresh.estado, target)
        EventoRevision.objects.create(
            documento=documento,
            borrador=Borrador.objects.filter(documento=documento).first(),
            accion=AccionRevision.STATUS,
            estado_anterior=current,
            estado_nuevo=target,
            comentario=comentario,
            actor=actor,
            actor_label=actor_label,
        )
    documento.estado = target
    documento.error_code = error_code
    documento.error_message = error_message[:2000]
