"""
Logs estructurados (una línea JSON por evento) con id de correlación y
redacción de datos sensibles. No se registran claves de API, textos de
facturas ni importes: solo identificadores, estados, tiempos y contadores.
"""

import json
import logging
import re

logger = logging.getLogger("invoice_agent")

_SECRET_KEYS = re.compile(r"(api[_-]?key|authorization|password|secret|token)", re.IGNORECASE)
_BEARER = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+)")


def redact_value(value):
    if isinstance(value, str):
        return _BEARER.sub("[REDACTADO]", value)[:500]
    return value


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    """Escribe un evento estructurado; las claves con aspecto de secreto se ocultan."""
    clean = {}
    for key, value in fields.items():
        clean[key] = "[REDACTADO]" if _SECRET_KEYS.search(key) else redact_value(value)
    payload = {"event": event, **clean}
    logger.log(level, json.dumps(payload, default=str, ensure_ascii=False))
