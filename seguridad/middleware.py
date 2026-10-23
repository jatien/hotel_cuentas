"""
IP real del cliente detrás del proxy.

Con Caddy delante, REMOTE_ADDR es la IP del proxy (127.0.0.1 o la del contenedor) y la del
cliente llega en X-Forwarded-For. Esa cabecera la puede escribir cualquiera, así que solo se
tiene en cuenta cuando la petición viene de un proxy de confianza (TRUSTED_PROXIES) y se toma
la IP más a la derecha que no sea de un proxy: la que añadió nuestro propio proxy.

Las peticiones que no vienen de un proxy de confianza pierden sus cabeceras X-Forwarded-*,
incluida X-Forwarded-Proto, de la que depende SECURE_PROXY_SSL_HEADER en prod.py.

Sin proxies configurados (desarrollo con runserver) no se toca nada.

El resto del proyecto sigue leyendo REMOTE_ADDR (auditoría de invoice_agent, actor del copilot).
"""

import ipaddress
import logging

from django.conf import settings

logger = logging.getLogger("seguridad")

_CABECERAS_PROXY = ("HTTP_X_FORWARDED_FOR", "HTTP_X_FORWARDED_PROTO", "HTTP_X_FORWARDED_HOST")


def _redes(valores) -> tuple:
    redes = []
    for valor in valores:
        valor = valor.strip()
        if valor:
            redes.append(ipaddress.ip_network(valor, strict=False))
    return tuple(redes)


def _ip(texto: str):
    try:
        return ipaddress.ip_address(texto.strip().strip("[]"))
    except ValueError:
        return None


class ProxyClientIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.proxies = _redes(getattr(settings, "TRUSTED_PROXIES", ()))

    def _de_confianza(self, ip) -> bool:
        return ip is not None and any(ip in red for red in self.proxies)

    def __call__(self, request):
        if self.proxies:
            remota = _ip(request.META.get("REMOTE_ADDR", ""))
            reenviada = request.META.get("HTTP_X_FORWARDED_FOR", "")
            if self._de_confianza(remota) and reenviada:
                cliente = None
                for parte in reversed(reenviada.split(",")):
                    ip = _ip(parte)
                    if ip is None:
                        logger.warning("x_forwarded_for_invalido valor=%r", reenviada[:200])
                        break
                    cliente = ip
                    if not self._de_confianza(ip):
                        break
                if cliente is not None:
                    request.META["PROXY_ADDR"] = request.META["REMOTE_ADDR"]
                    request.META["REMOTE_ADDR"] = str(cliente)
            elif not self._de_confianza(remota):
                # Petición que no viene del proxy: sus cabeceras X-Forwarded-* las ha escrito el
                # cliente y no valen (tampoco para SECURE_PROXY_SSL_HEADER).
                for cabecera in _CABECERAS_PROXY:
                    request.META.pop(cabecera, None)
        return self.get_response(request)
