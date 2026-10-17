"""
API JSON de invoice_agent (montada bajo /facturas-ia/api/).

    POST   documentos                         subir y poner en cola (202; 200 si el fichero ya existía)
    GET    documentos?estado=&empresa=&page=  lista paginada
    GET    documentos/<id>                    estado del procesado
    GET    documentos/<id>/extraccion         datos extraídos, evidencias, validación y borrador
    GET    documentos/<id>/original           URL firmada de corta duración del original
    POST   documentos/<id>/reprocesar         volver a procesar (explícito)
    PATCH  borradores/<id>                    corregir campos / líneas / impuestos / aceptar avisos
    POST   borradores/<id>/aprobar            aprobar (no contabiliza)
    POST   borradores/<id>/rechazar           rechazar
    GET    borradores/<id>/eventos            auditoría

Errores siempre con la forma {"error": {"code", "message", "details"}}.
La autenticación y la empresa salen de la sesión (services/access.py), nunca
del cuerpo de la petición. Las peticiones de escritura llevan protección CSRF
de Django (cabecera X-CSRFToken), como el resto del proyecto.
"""

import functools
import json

from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, Paginator
from django.http import JsonResponse
from django.urls import reverse
from pydantic import BaseModel, ValidationError

from invoice_agent.api import serialize
from invoice_agent.models import (
    AccionRevision,
    Documento,
    EstadoDocumento,
    EventoRevision,
)
from invoice_agent.schemas import (
    DecisionIn,
    DraftPatch,
    ErrorBody,
    ErrorResponse,
    ReprocessIn,
)
from invoice_agent.services import review
from invoice_agent.services.access import NotAuthenticated, context_for_request, scoped
from invoice_agent.services.errors import ServiceError
from invoice_agent.services.ingestion import ingest_upload
from invoice_agent.services.states import InvalidTransition
from invoice_agent.services.storage import make_token

MAX_PAGE_SIZE = 100


def _error(status: int, code: str, message: str, details=None) -> JsonResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JsonResponse(body.model_dump(mode="json"), status=status)


def _ok(model: BaseModel | dict, status: int = 200) -> JsonResponse:
    data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    return JsonResponse(data, status=status)


def api(*methods):
    """Decorador: métodos permitidos, contexto de acceso y traducción de errores a JSON."""

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return _error(405, "METHOD_NOT_ALLOWED", f"Método no permitido: {request.method}.")
            try:
                ctx = context_for_request(request)
                return view(request, ctx, *args, **kwargs)
            except NotAuthenticated:
                return _error(401, "NOT_AUTHENTICATED", "Inicia sesión.")
            except PermissionDenied:
                return _error(404, "NOT_FOUND", "No encontrado.")  # no revela recursos de otras empresas
            except ServiceError as error:
                return _error(error.status, error.code, error.message, error.details)
            except InvalidTransition as error:
                return _error(409, "INVALID_TRANSITION", str(error), {"estado": error.current})
            except ValidationError as error:
                details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in error.errors(include_url=False)]
                return _error(422, "INVALID_REQUEST", "Datos no válidos.", details)

        return wrapper

    return decorator


def _json_body(request, schema: type[BaseModel]) -> BaseModel:
    if not request.body:
        return schema()
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ServiceError("INVALID_JSON", "El cuerpo no es JSON válido.", 400) from error
    return schema.model_validate(payload)


@api("GET", "POST")
def documents(request, ctx):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            raise ServiceError("FILE_REQUIRED", "Falta el fichero (campo «file»).")
        empresa_id = request.POST.get("empresa")
        if not empresa_id and len(ctx.empresa_ids) == 1:
            empresa_id = next(iter(ctx.empresa_ids))
        documento, created = ingest_upload(upload, empresa_id, ctx)
        response = _ok(serialize.document_out(documento, duplicado=not created), 202 if created else 200)
        response["Location"] = reverse("invoice_agent:api_document", args=[documento.public_id])
        return response

    queryset = scoped(Documento.objects.select_related("empresa"), ctx).order_by("-creado")
    estado = request.GET.get("estado")
    if estado:
        if estado not in EstadoDocumento.values:
            raise ServiceError("INVALID_FILTER", f"Estado desconocido: {estado}.")
        queryset = queryset.filter(estado=estado)
    empresa = request.GET.get("empresa")
    if empresa:
        queryset = queryset.filter(empresa_id=empresa if empresa.isdigit() else -1)
    try:
        page_size = min(max(int(request.GET.get("page_size", 25)), 1), MAX_PAGE_SIZE)
        page_number = max(int(request.GET.get("page", 1)), 1)
    except ValueError as error:
        raise ServiceError("INVALID_PAGINATION", "page y page_size deben ser números.") from error
    paginator = Paginator(queryset, page_size)
    try:
        page = paginator.page(page_number)
    except EmptyPage:
        page = None
    return _ok(
        {
            "count": paginator.count,
            "page": page_number,
            "page_size": page_size,
            "results": [serialize.document_out(d).model_dump(mode="json") for d in (page or [])],
        }
    )


@api("GET")
def document_detail(request, ctx, document_id):
    return _ok(serialize.document_out(review.get_document(ctx, document_id)))


@api("GET")
def document_extraction(request, ctx, document_id):
    return _ok(serialize.extraction_out(review.get_document(ctx, document_id)))


@api("GET")
def document_original(request, ctx, document_id):
    documento = review.get_document(ctx, document_id)
    EventoRevision.objects.create(
        documento=documento,
        accion=AccionRevision.FILE_ACCESS,
        actor=ctx.user if ctx.user_id else None,
        actor_label=ctx.actor_label,
    )
    from invoice_agent import conf

    url = request.build_absolute_uri(reverse("invoice_agent:original", args=[make_token(documento.public_id)]))
    return _ok({"url": url, "expires_in": int(conf.get("SIGNED_URL_SECONDS"))})


@api("POST")
def document_reprocess(request, ctx, document_id):
    documento = review.request_reprocess(ctx, review.get_document(ctx, document_id), _json_body(request, ReprocessIn))
    return _ok(serialize.document_out(documento), 202)


@api("PATCH", "GET")
def draft_detail(request, ctx, draft_id):
    borrador = review.get_draft(ctx, draft_id)
    if request.method == "PATCH":
        borrador = review.apply_patch(ctx, borrador, _json_body(request, DraftPatch))
    return _ok(serialize.draft_out(review.get_draft(ctx, borrador.pk)))


@api("POST")
def draft_approve(request, ctx, draft_id):
    borrador = review.approve(ctx, review.get_draft(ctx, draft_id), _json_body(request, DecisionIn))
    return _ok(serialize.draft_out(review.get_draft(ctx, borrador.pk)))


@api("POST")
def draft_reject(request, ctx, draft_id):
    borrador = review.reject(ctx, review.get_draft(ctx, draft_id), _json_body(request, DecisionIn))
    return _ok(serialize.draft_out(review.get_draft(ctx, borrador.pk)))


@api("GET")
def draft_events(request, ctx, draft_id):
    borrador = review.get_draft(ctx, draft_id)
    events = EventoRevision.objects.filter(documento_id=borrador.documento_id)
    return _ok({"results": [serialize.event_out(e).model_dump(mode="json") for e in events]})
