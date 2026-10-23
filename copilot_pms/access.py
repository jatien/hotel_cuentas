"""
Contexto de acceso del copilot, construido siempre en el servidor.

- REQUIRE_LOGIN=False (por defecto, uso en la LAN como el resto del proyecto):
  sin usuarios; el actor se identifica por la IP y se ven todas las empresas activas.
- REQUIRE_LOGIN=True: hace falta sesión. Cada herramienta exige sus permisos de
  Django (solo se ofrecen al modelo las permitidas) y las facturas con IA se
  limitan a las empresas del usuario, con las mismas reglas que invoice_agent.
"""

from django.utils import timezone

from copilot_pms import conf
from copilot_pms.tools.base import Contexto


class NoAutenticado(Exception):
    pass


def _empresas_facturas(user, login: bool) -> frozenset:
    from invoice_agent.models import Empresa

    activas = Empresa.objects.filter(activa=True)
    if login and not user.is_superuser:
        activas = activas.filter(usuarios=user)
    return frozenset(activas.values_list("pk", flat=True))


def contexto_para(request) -> Contexto:
    login = bool(conf.get("REQUIRE_LOGIN"))
    user = getattr(request, "user", None)
    if login:
        if user is None or not user.is_authenticated:
            raise NoAutenticado()
        actor = user.get_username()
    else:
        user = None
        # REMOTE_ADDR y nunca X-Forwarded-For (la escribe el cliente). Detrás del proxy, Waitress o
        # seguridad.middleware.ProxyClientIPMiddleware ya ponen aquí la IP real del cliente.
        actor = f"LAN {request.META.get('REMOTE_ADDR') or 'desconocido'}"
    return Contexto(hoy=timezone.localdate(), user=user, actor_label=actor, login_requerido=login,
                    empresas_facturas=_empresas_facturas(user, login))


def contexto_sistema(actor: str = "consola") -> Contexto:
    """Para el comando de gestión: quien tiene acceso al servidor ya tiene acceso a la base de datos."""
    return Contexto(hoy=timezone.localdate(), actor_label=actor, login_requerido=False,
                    empresas_facturas=_empresas_facturas(None, False))
