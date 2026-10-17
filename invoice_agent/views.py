"""
Pantallas de Detección de facturas con IA (en español).

    index         lista con filtro por estado y subida de facturas
    detalle       revisión: original a la izquierda, borrador editable a la derecha,
                  incidencias, evidencias de cada campo y auditoría
    accion        POST: guardar correcciones, aprobar, rechazar, reprocesar
    original      descarga del original con token firmado de corta duración

Usan los mismos servicios que la API, así que las reglas (estados, permisos,
auditoría, validación) son idénticas en las dos vías.
"""

from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from invoice_agent.forms import (
    BorradorForm,
    DecisionForm,
    ImpuestoFormSet,
    LineaFormSet,
    SubidaForm,
)
from invoice_agent.models import (
    Borrador,
    Documento,
    Empresa,
    EstadoDocumento,
    EventoRevision,
)
from invoice_agent.schemas import DecisionIn, DraftPatch, LineIn, ReprocessIn, TaxIn
from invoice_agent.services import drafts, review, states
from invoice_agent.services.access import NotAuthenticated, context_for_request, scoped
from invoice_agent.services.errors import ServiceError
from invoice_agent.services.ingestion import ingest_upload
from invoice_agent.services.states import InvalidTransition
from invoice_agent.services.storage import make_token, path_for, read_token
from invoice_agent.services.validation import FIELD_LABELS

PAGE_SIZE = 50


def _ctx_or_login(request):
    try:
        return context_for_request(request), None
    except NotAuthenticated:
        from django.conf import settings

        return None, redirect(f"{settings.LOGIN_URL}?next={request.path}")


def index(request):
    ctx, login = _ctx_or_login(request)
    if login:
        return login
    empresas = Empresa.objects.filter(pk__in=ctx.empresa_ids).order_by("nombre")
    form = SubidaForm(empresas=empresas)

    if request.method == "POST":
        form = SubidaForm(request.POST, request.FILES, empresas=empresas)
        if form.is_valid():
            nuevos = repetidos = 0
            for upload in form.cleaned_data["ficheros"]:
                try:
                    _, created = ingest_upload(upload, form.cleaned_data["empresa"].pk, ctx)
                except ServiceError as error:
                    messages.error(request, f"{upload.name}: {error.message}")
                    continue
                nuevos += created
                repetidos += not created
            if nuevos:
                messages.success(request, f"{nuevos} factura(s) en cola para extraer.")
            if repetidos:
                messages.info(request, f"{repetidos} fichero(s) ya estaban subidos: no se han duplicado.")
            return redirect("invoice_agent:index")

    documentos = scoped(Documento.objects.select_related("empresa", "borrador"), ctx).order_by("-creado")
    estado = request.GET.get("estado", "")
    if estado in EstadoDocumento.values:
        documentos = documentos.filter(estado=estado)
    conteos = dict(
        scoped(Documento.objects, ctx).values_list("estado").annotate(n=Count("id")).values_list("estado", "n")
    )
    page = Paginator(documentos, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "invoice_agent/index.html",
        {
            "form": form,
            "page": page,
            "estado": estado,
            "estados": [(value, label, conteos.get(value, 0)) for value, label in EstadoDocumento.choices],
            "en_curso": conteos.get(EstadoDocumento.QUEUED, 0) + conteos.get(EstadoDocumento.PROCESSING, 0),
        },
    )


def _initial(borrador: Borrador) -> dict:
    return {c: getattr(borrador, c) for c in drafts.HEADER_COLUMNS}


def _field_rows(form, borrador: Borrador | None):
    """Pares (campo del formulario, evidencia de la extracción) para pintar la evidencia junto a cada campo."""
    evidencias = borrador.extraccion.evidencias if borrador and borrador.extraccion else {}
    rows = []
    for name in drafts.HEADER_COLUMNS:
        field_name = drafts.COLUMN_TO_FIELD[name]
        rows.append((form[name], evidencias.get(field_name), FIELD_LABELS.get(field_name, name)))
    return rows


def detalle(request, document_id):
    ctx, login = _ctx_or_login(request)
    if login:
        return login
    try:
        documento = review.get_document(ctx, document_id)
    except ServiceError:
        raise Http404("Documento no encontrado")
    borrador = Borrador.objects.filter(documento=documento).select_related("extraccion__intento").first()

    form = lineas = impuestos = None
    if borrador is not None:
        form = BorradorForm(initial=_initial(borrador), prefix="b")
        lineas = LineaFormSet(
            initial=[{c: getattr(line, c) for c in drafts.LINE_COLUMNS} for line in borrador.lineas.all()], prefix="l"
        )
        impuestos = ImpuestoFormSet(
            initial=[{c: getattr(tax, c) for c in drafts.TAX_COLUMNS} for tax in borrador.impuestos.all()], prefix="t"
        )
    return _render_detalle(request, documento, borrador, form, lineas, impuestos)


def _render_detalle(request, documento, borrador, form, lineas, impuestos, status=200):
    incidencias = []
    if borrador is not None:
        aceptados = set(borrador.avisos_aceptados)
        for issue in borrador.incidencias:
            incidencias.append({**issue, "aceptado": issue["code"] in aceptados and issue["severity"] == "warning"})
    intento = documento.intentos.order_by("-numero").first()
    return render(
        request,
        "invoice_agent/detalle.html",
        {
            "documento": documento,
            "borrador": borrador,
            "form": form,
            "campos": _field_rows(form, borrador) if form else [],
            "lineas": lineas,
            "impuestos": impuestos,
            "incidencias": incidencias,
            "bloqueantes": drafts.blockers(borrador) if borrador else [],
            "eventos": EventoRevision.objects.filter(documento=documento).order_by("-creado")[:100],
            "intento": intento,
            "editable": documento.estado in states.EDITABLE,
            "reprocesable": documento.estado in states.REPROCESSABLE,
            "en_curso": documento.estado in (EstadoDocumento.QUEUED, EstadoDocumento.PROCESSING),
            "original_url": reverse("invoice_agent:original", args=[make_token(documento.public_id)]),
            "decision": DecisionForm(),
        },
        status=status,
    )


def _patch_from_forms(form, lineas, impuestos, aceptar) -> DraftPatch:
    header = {k: v for k, v in form.cleaned_data.items() if k != "comentario"}
    return DraftPatch(
        **header,
        lineas=[LineIn(**f.cleaned_data) for f in lineas.forms if f.cleaned_data and not f.is_blank()],
        impuestos=[TaxIn(**f.cleaned_data) for f in impuestos.forms if f.cleaned_data and not f.is_blank()],
        aceptar_avisos=aceptar or None,
        comentario=form.cleaned_data.get("comentario", ""),
    )


@require_POST
def accion(request, document_id, que):
    ctx, login = _ctx_or_login(request)
    if login:
        return login
    try:
        documento = review.get_document(ctx, document_id)
    except ServiceError:
        raise Http404("Documento no encontrado")
    borrador = Borrador.objects.filter(documento=documento).first()
    destino = redirect("invoice_agent:detalle", document_id=documento.public_id)
    comentario = request.POST.get("comentario", "")[:2000]

    try:
        if que == "guardar" and borrador is not None:
            form = BorradorForm(request.POST, prefix="b")
            lineas = LineaFormSet(request.POST, prefix="l")
            impuestos = ImpuestoFormSet(request.POST, prefix="t")
            if not (form.is_valid() and lineas.is_valid() and impuestos.is_valid()):
                messages.error(request, "Revisa los campos marcados.")
                return _render_detalle(request, documento, borrador, form, lineas, impuestos, status=400)
            review.apply_patch(ctx, borrador, _patch_from_forms(form, lineas, impuestos, request.POST.getlist("aceptar")))
            messages.success(request, "Cambios guardados y factura revalidada.")
        elif que == "aprobar" and borrador is not None:
            review.approve(ctx, borrador, DecisionIn(comentario=comentario))
            messages.success(request, "Factura aprobada. (No se ha contabilizado: queda lista para hacerlo.)")
        elif que == "rechazar" and borrador is not None:
            review.reject(ctx, borrador, DecisionIn(comentario=comentario))
            messages.info(request, "Factura rechazada.")
        elif que == "reprocesar":
            review.request_reprocess(
                ctx, documento, ReprocessIn(descartar_correcciones=bool(request.POST.get("descartar")), comentario=comentario)
            )
            messages.info(request, "Documento en cola para volver a extraerlo.")
        else:
            return HttpResponseNotAllowed(["POST"])
    except ServiceError as error:
        messages.error(request, error.message)
    except InvalidTransition as error:
        messages.error(request, str(error))
    except PermissionDenied:
        raise Http404("Documento no encontrado")
    return destino


def original(request, token):
    """Sirve el original si el token es válido y no ha caducado. Solo PDF e imágenes, nunca HTML."""
    try:
        public_id = read_token(token)
    except signing.SignatureExpired:
        raise Http404("El enlace ha caducado; vuelve a abrir la factura.")
    except signing.BadSignature:
        raise Http404("Enlace no válido.")
    documento = Documento.objects.filter(public_id=public_id).first()
    if documento is None:
        raise Http404("Documento no encontrado.")
    path = path_for(documento.storage_key) if documento.storage_key else None
    if path is None or not path.is_file():
        raise Http404("El original ya no está disponible (política de retención).")
    response = FileResponse(open(path, "rb"), content_type=documento.mime_type)
    response["Content-Disposition"] = f'inline; filename="{documento.public_id}{path.suffix}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
