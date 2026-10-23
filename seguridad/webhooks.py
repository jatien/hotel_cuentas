"""
Verificación de webhooks entrantes (motor de reservas, pasarela de pago, etc.).

Un webhook es una petición que un servicio externo hace a nuestra app. Cualquiera podría
fabricarla, así que solo se acepta si:

1. trae una firma HMAC-SHA256 del cuerpo exacto, hecha con un secreto compartido que solo
   conocemos nosotros y el servicio;
2. trae una marca de tiempo reciente (por defecto ±5 minutos), incluida en la firma, para que
   no se pueda reenviar una petición antigua capturada (repetición);
3. su identificador no se ha visto ya (opcional, si el servicio manda uno).

Uso en una vista:

    from seguridad.webhooks import firma_webhook

    @firma_webhook(secreto="NEOBOOKINGS_WEBHOOK_SECRET")   # nombre del ajuste en settings/.env
    def recibir_reserva(request):
        datos = json.loads(request.body)
        ...

Cada proveedor firma a su manera (nombre de cabeceras, qué entra en la firma): se adapta con
los parámetros de `firma_webhook` o con `verificar_firma` directamente.
"""

import hashlib
import hmac
import logging
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger("seguridad.webhooks")

TOLERANCIA_S = 300


class FirmaInvalida(Exception):
    pass


def firmar(secreto: str, marca_tiempo: str, cuerpo: bytes) -> str:
    """Firma esperada: hex(HMAC-SHA256(secreto, "<marca_tiempo>." + cuerpo))."""
    mensaje = marca_tiempo.encode() + b"." + cuerpo
    return hmac.new(secreto.encode(), mensaje, hashlib.sha256).hexdigest()


def verificar_firma(secreto: str, cuerpo: bytes, firma: str, marca_tiempo: str, *,
                    tolerancia: int = TOLERANCIA_S, ahora: float | None = None) -> None:
    if not secreto:
        raise FirmaInvalida("sin secreto configurado")
    if not firma or not marca_tiempo:
        raise FirmaInvalida("faltan cabeceras de firma")
    try:
        instante = int(marca_tiempo)
    except ValueError as exc:
        raise FirmaInvalida("marca de tiempo no válida") from exc
    ahora = time.time() if ahora is None else ahora
    if abs(ahora - instante) > tolerancia:
        raise FirmaInvalida("marca de tiempo fuera de plazo (posible repetición)")
    esperada = firmar(secreto, marca_tiempo, cuerpo)
    firma = firma.removeprefix("sha256=")
    if not hmac.compare_digest(esperada, firma):
        raise FirmaInvalida("firma incorrecta")


def firma_webhook(*, secreto: str, cabecera_firma: str = "X-Signature", cabecera_tiempo: str = "X-Timestamp",
                  cabecera_id: str = "X-Webhook-Id", tolerancia: int = TOLERANCIA_S):
    """Decorador de vista: rechaza con 401 los webhooks sin firma válida y con 409 los repetidos.

    `secreto` es el NOMBRE del ajuste que contiene el secreto (se lee en cada petición).
    """

    def decorador(vista):
        @csrf_exempt  # lo llama un servidor externo: la firma sustituye al token CSRF
        @require_POST
        @wraps(vista)
        def envoltorio(request, *args, **kwargs):
            try:
                verificar_firma(
                    getattr(settings, secreto, ""), request.body,
                    request.headers.get(cabecera_firma, ""), request.headers.get(cabecera_tiempo, ""),
                    tolerancia=tolerancia,
                )
            except FirmaInvalida as exc:
                logger.warning("webhook_rechazado vista=%s motivo=%s ip=%s", vista.__name__, exc,
                               request.META.get("REMOTE_ADDR", ""))
                return HttpResponse(status=401)

            identificador = request.headers.get(cabecera_id, "")
            if identificador:
                clave = f"webhook:{vista.__name__}:{identificador[:200]}"
                if not cache.add(clave, 1, timeout=tolerancia * 2):
                    logger.warning("webhook_repetido vista=%s id=%s", vista.__name__, identificador[:200])
                    return HttpResponse(status=409)
            return vista(request, *args, **kwargs)

        return envoltorio

    return decorador
