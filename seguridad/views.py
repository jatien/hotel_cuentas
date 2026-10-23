"""
Vistas de infraestructura de seguridad.

    csp_report   recibe los informes de la Content Security Policy que envía el navegador
                 y los escribe en el log de seguridad (logs/security.log)
    health       comprobación para el proxy / Docker: el proceso responde y llega a la BD
"""

import json
import logging

from django.db import connection
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger("seguridad.csp")

# Un informe CSP ocupa ~1 KB; lo que pase de aquí se descarta sin leerlo entero.
MAX_INFORME_BYTES = 16 * 1024

# Campos útiles para localizar la infracción (nombres de report-uri y de la Reporting API).
_CAMPOS = (
    ("document-uri", "documentURL"),
    ("violated-directive", "effectiveDirective"),
    ("blocked-uri", "blockedURL"),
    ("source-file", "sourceFile"),
    ("line-number", "lineNumber"),
    ("disposition", "disposition"),
)


def _resumen(informe: dict) -> dict:
    datos = {}
    for antiguo, nuevo in _CAMPOS:
        valor = informe.get(antiguo, informe.get(nuevo))
        if valor not in (None, ""):
            datos[antiguo] = str(valor)[:300]
    return datos


@csrf_exempt  # lo envía el navegador por su cuenta, sin token CSRF
@require_POST
def csp_report(request):
    longitud = int(request.META.get("CONTENT_LENGTH") or 0)
    if longitud <= 0 or longitud > MAX_INFORME_BYTES:
        return HttpResponse(status=413 if longitud else 400)
    try:
        cuerpo = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse(status=400)

    # report-uri: {"csp-report": {...}} · Reporting API: [{"type": "csp-violation", "body": {...}}]
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get("csp-report"), dict):
        informes = [cuerpo["csp-report"]]
    elif isinstance(cuerpo, list):
        informes = [r.get("body") for r in cuerpo[:20] if isinstance(r, dict) and isinstance(r.get("body"), dict)]
    else:
        informes = []

    for informe in informes:
        logger.warning("csp_violation %s ip=%s", json.dumps(_resumen(informe), ensure_ascii=False),
                       request.META.get("REMOTE_ADDR", ""))
    return HttpResponse(status=204)


@never_cache
@require_GET
def health(request):
    try:
        connection.ensure_connection()
    except Exception:  # noqa: BLE001 — el detalle va al log, no a la respuesta
        logging.getLogger("seguridad").exception("health: sin conexión a la base de datos")
        return HttpResponse("db", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")
