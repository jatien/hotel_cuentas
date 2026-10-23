"""
Contexto de acceso: quién actúa y sobre qué empresas.

Toda consulta de la app pasa por `scoped()` con un AccessContext construido
en el servidor a partir de la petición. Nunca se confía en un empresa_id,
usuario o permiso que venga del cliente o del modelo sin comprobarlo aquí.

- REQUIRE_LOGIN=False (por defecto, uso en la LAN como el resto del
  proyecto): sin usuarios; se accede a todas las empresas activas y el actor
  se identifica por la IP.
- REQUIRE_LOGIN=True: hace falta sesión iniciada; un superusuario ve todas
  las empresas activas y el resto solo aquellas en las que figura en
  Empresa.usuarios. Aprobar exige además el permiso
  invoice_agent.change_borrador.
"""

from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from invoice_agent import conf
from invoice_agent.models import Empresa


class NotAuthenticated(Exception):
    """Hace falta iniciar sesión."""


@dataclass(frozen=True)
class AccessContext:
    empresa_ids: frozenset[int]
    user: object | None = None
    actor_label: str = "sistema"
    can_approve: bool = True
    extra: dict = field(default_factory=dict)

    def check_empresa(self, empresa_id: int) -> None:
        if empresa_id not in self.empresa_ids:
            raise PermissionDenied("Sin acceso a esa empresa.")

    @property
    def user_id(self):
        return getattr(self.user, "pk", None)


def _client_ip(request) -> str:
    # REMOTE_ADDR y nunca X-Forwarded-For (la escribe el cliente). Detrás del proxy, Waitress o
    # seguridad.middleware.ProxyClientIPMiddleware ya ponen aquí la IP real del cliente.
    return request.META.get("REMOTE_ADDR", "") or "desconocido"


def context_for_request(request) -> AccessContext:
    """Construye el contexto desde la petición (sesión de Django), nunca desde datos del cliente."""
    activas = Empresa.objects.filter(activa=True)
    if not conf.get("REQUIRE_LOGIN"):
        return AccessContext(
            empresa_ids=frozenset(activas.values_list("pk", flat=True)),
            user=None,
            actor_label=f"LAN {_client_ip(request)}",
            can_approve=True,
        )

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise NotAuthenticated()
    if not user.is_superuser:
        activas = activas.filter(usuarios=user)
    return AccessContext(
        empresa_ids=frozenset(activas.values_list("pk", flat=True)),
        user=user,
        actor_label=user.get_username(),
        can_approve=user.has_perm("invoice_agent.change_borrador"),
    )


def system_context() -> AccessContext:
    """Contexto del worker: procesa documentos de cualquier empresa activa, sin aprobar nada."""
    return AccessContext(
        empresa_ids=frozenset(Empresa.objects.values_list("pk", flat=True)),
        actor_label="worker",
        can_approve=False,
    )


def scoped(queryset: QuerySet, ctx: AccessContext, empresa_field: str = "empresa_id") -> QuerySet:
    """Filtra cualquier queryset por las empresas del contexto."""
    return queryset.filter(**{f"{empresa_field}__in": ctx.empresa_ids})
