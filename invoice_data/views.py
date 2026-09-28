"""
Views for Gestión de Facturas.

    index                 list with filters, counters and exports
    upload_invoices       drop-zone import (optionally extracting right away)
    document_detail       review screen: PDF on the left, extracted data on the right
    document_json         one invoice as JSON (schema "factura/1")
    export_facturas_csv   one row per invoice, for Excel / analysis
    export_lineas_csv     one row per product line, for Excel / analysis

All user-facing text is Spanish.
"""

import csv
import tempfile
from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from invoice_data.forms import DatosFacturaForm, FiltroFacturasForm, InvoiceUploadForm
from invoice_data.models import (
    EstadoExtraccion,
    ExtractedInvoiceData,
    IngestionStatus,
    InvoiceDocument,
    InvoiceLineItem,
    TipoEntidad,
)
from invoice_data.services.ingestion import IMAGE_EXTENSIONS, PDF_EXTENSIONS, ingest_file
from invoice_data.services.pipeline import (
    DatosConfirmados,
    document_to_dict,
    process_document,
    supplier_for,
)

ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS
PAGE_SIZE = 50


def _apply_filters(queryset, form: FiltroFacturasForm):
    """Narrows the document list by the (valid) filter form; unknown or empty filters do nothing."""
    if not form.is_valid():
        return queryset
    data = form.cleaned_data
    if data["q"]:
        term = data["q"]
        queryset = queryset.filter(
            Q(original_filename__icontains=term)
            | Q(datos__numero_factura__icontains=term)
            | Q(datos__proveedor_nombre__icontains=term)
            | Q(datos__proveedor_cif__icontains=term)
        )
    if data["estado"] == "pendiente":
        queryset = queryset.filter(datos__isnull=True)
    elif data["estado"] in (EstadoExtraccion.OK, EstadoExtraccion.REVISAR):
        queryset = queryset.filter(datos__estado=data["estado"])
    if data["tipo_entidad"]:
        queryset = queryset.filter(tipo_entidad=data["tipo_entidad"])
    if data["departamento"]:
        queryset = queryset.filter(departamento=data["departamento"])
    if data["confirmada"] == "si":
        queryset = queryset.filter(datos__confirmado=True)
    elif data["confirmada"] == "no":
        queryset = queryset.filter(Q(datos__isnull=True) | Q(datos__confirmado=False))
    return queryset


def index(request):
    """Main page: counters for the whole archive, filters, and a paginated list."""
    stats = InvoiceDocument.objects.aggregate(
        total=Count("id"),
        sin_extraer=Count("id", filter=Q(datos__isnull=True)),
        correctas=Count("id", filter=Q(datos__estado=EstadoExtraccion.OK)),
        a_revisar=Count("id", filter=Q(datos__estado=EstadoExtraccion.REVISAR)),
        confirmadas=Count("id", filter=Q(datos__confirmado=True)),
    )
    filtro = FiltroFacturasForm(request.GET or None)
    documents = _apply_filters(
        InvoiceDocument.objects.select_related("datos", "proveedor"), filtro
    )
    page = Paginator(documents, PAGE_SIZE).get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "invoice_data/index.html",
        {"stats": stats, "filtro": filtro, "page": page, "querystring": params.urlencode()},
    )


def upload_invoices(request):
    """
    Drop-zone import. Every file is written to a temp path (the engine needs
    a real file), ingested with the chosen entity type and department, and
    optionally extracted immediately. Failures are reported per file and
    never stop the rest of the batch.
    """
    if request.method != "POST":
        return render(request, "invoice_data/upload.html", {"form": InvoiceUploadForm()})

    form = InvoiceUploadForm(request.POST)
    uploaded_files = request.FILES.getlist("files")

    if not uploaded_files:
        messages.error(request, "No se ha seleccionado ningún archivo.")
    elif not form.is_valid():
        messages.error(request, "Revisa el formulario: " + "; ".join(sum(form.errors.values(), [])))
    else:
        tipo_entidad = form.cleaned_data["tipo_entidad"]
        departamento = form.cleaned_data["departamento"] if tipo_entidad == TipoEntidad.PROVEEDOR else ""
        counts = {"importadas": 0, "duplicadas": 0, "errores": 0, "omitidas": 0, "extraidas": 0, "revisar": 0}

        for uploaded in uploaded_files:
            suffix = Path(uploaded.name).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                counts["omitidas"] += 1
                messages.warning(request, f"Tipo de archivo no soportado, omitido: {uploaded.name}")
                continue

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                for chunk in uploaded.chunks():
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)
            try:
                document, created = ingest_file(
                    tmp_path, departamento=departamento, original_filename=uploaded.name, tipo_entidad=tipo_entidad
                )
            finally:
                tmp_path.unlink(missing_ok=True)

            if not created:
                counts["duplicadas"] += 1
            elif document.status == IngestionStatus.ERROR:
                counts["errores"] += 1
                messages.error(request, f"{uploaded.name}: {document.error_message}")
            else:
                counts["importadas"] += 1
                if form.cleaned_data["extraer"]:
                    try:
                        datos = process_document(document)
                        counts["extraidas"] += 1
                        counts["revisar"] += datos.estado == EstadoExtraccion.REVISAR
                    except Exception as error:  # OCR missing, unreadable file...
                        messages.warning(request, f"{uploaded.name}: importada, pero no se pudo extraer ({error})")

        messages.success(
            request,
            f"{counts['importadas']} importada(s), {counts['extraidas']} extraída(s) "
            f"({counts['revisar']} a revisar), {counts['duplicadas']} duplicada(s), "
            f"{counts['errores']} con error, {counts['omitidas']} omitida(s).",
        )
        return redirect(reverse("invoice_data:index"))

    return render(request, "invoice_data/upload.html", {"form": form})


def document_detail(request, pk):
    """
    Review screen. GET shows the PDF and the extracted data. POST actions:
      extract          run (or re-run) the extraction
      save             store the corrected header and mark it as confirmed
      unconfirm        reopen a confirmed invoice for editing
      delete_data      discard the extraction (the file stays)
      delete_document  delete the invoice completely, file included
    Successful actions redirect back (POST-redirect-GET); an invalid form
    is shown again with its errors.
    """
    document = get_object_or_404(InvoiceDocument.objects.select_related("proveedor"), pk=pk)
    datos = getattr(document, "datos", None)
    form = DatosFacturaForm(instance=datos) if datos else None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "extract":
            try:
                process_document(document)
                messages.success(request, "Datos extraídos. Revísalos y pulsa «Guardar y confirmar» si son correctos.")
            except DatosConfirmados:
                messages.warning(request, "La factura ya está confirmada. Quita la confirmación o elimina los datos para volver a extraer.")
            except Exception as error:
                messages.error(request, f"No se pudo extraer: {error}")

        elif action == "save" and datos:
            form = DatosFacturaForm(request.POST, instance=datos)
            if not form.is_valid():
                messages.error(request, "Hay errores en el formulario.")
                return render(request, "invoice_data/detail.html", _detail_context(document, datos, form))
            saved = form.save(commit=False)
            saved.corregido = saved.corregido or form.has_changed()
            saved.confirmado = True
            saved.save()
            document.proveedor = supplier_for(saved.proveedor_cif, saved.proveedor_nombre)
            document.save(update_fields=["proveedor"])
            messages.success(request, "Datos guardados y factura confirmada.")

        elif action == "unconfirm" and datos:
            datos.confirmado = False
            datos.save(update_fields=["confirmado"])
            messages.info(request, "Confirmación retirada: ya se puede editar o volver a extraer.")

        elif action == "delete_data" and datos:
            datos.delete()
            document.impuestos.all().delete()
            document.lineas.all().delete()
            document.proveedor = None
            document.save(update_fields=["proveedor"])
            messages.warning(request, "Datos de la factura eliminados.")

        elif action == "delete_document":
            document.file.delete(save=False)  # the file on disk, not only the row
            document.delete()
            messages.warning(request, "Factura eliminada por completo.")
            return redirect(reverse("invoice_data:index"))

        return redirect(reverse("invoice_data:detail", args=[document.pk]))

    return render(request, "invoice_data/detail.html", _detail_context(document, datos, form))


def _detail_context(document, datos, form) -> dict:
    """Everything the review template needs, gathered in one place."""
    return {
        "document": document,
        "datos": datos,
        "form": form,
        "impuestos": document.impuestos.all(),
        "lineas": document.lineas.all(),
        "verificacion": (datos.verificacion if datos else {}) or {},
    }


def document_json(request, pk):
    """The invoice as JSON, rebuilt from the database (so corrections are included); ?descargar=1 forces a download."""
    document = get_object_or_404(InvoiceDocument, pk=pk)
    response = JsonResponse(document_to_dict(document), json_dumps_params={"ensure_ascii": False, "indent": 2})
    if request.GET.get("descargar"):
        response["Content-Disposition"] = f'attachment; filename="{document.original_filename}.json"'
    return response


def _es(value) -> str:
    """A number as Spanish Excel expects it (decimal comma); empty for None."""
    return "" if value is None else str(value).replace(".", ",")


def _csv_response(filename: str) -> tuple[HttpResponse, "csv._writer"]:
    """An HttpResponse set up as a ';'-separated UTF-8 CSV with BOM, so Spanish Excel opens it correctly."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    return response, csv.writer(response, delimiter=";")


def export_facturas_csv(request):
    """One row per extracted invoice; ?confirmadas=1 limits it to reviewed ones."""
    rows = ExtractedInvoiceData.objects.select_related("document").order_by("fecha_factura", "id")
    if request.GET.get("confirmadas"):
        rows = rows.filter(confirmado=True)
    response, out = _csv_response("facturas.csv")
    out.writerow([
        "archivo", "tipo", "departamento", "proveedor", "cif", "numero", "fecha",
        "total_sin_iva", "iva", "total_con_iva", "cuadra", "estado", "confirmada", "corregida",
    ])
    for d in rows:
        out.writerow([
            d.document.original_filename, d.document.tipo_entidad, d.document.departamento,
            d.proveedor_nombre, d.proveedor_cif, d.numero_factura,
            d.fecha_factura.isoformat() if d.fecha_factura else "",
            _es(d.base_imponible), _es(d.iva_total), _es(d.total),
            "si" if d.cuadra else "no", d.estado, "si" if d.confirmado else "no", "si" if d.corregido else "no",
        ])
    return response


def export_lineas_csv(request):
    """One row per product line, with its invoice's supplier and date; ?confirmadas=1 limits to reviewed invoices."""
    lines = InvoiceLineItem.objects.select_related("document", "document__datos").order_by(
        "document__datos__fecha_factura", "document_id", "id"
    )
    if request.GET.get("confirmadas"):
        lines = lines.filter(document__datos__confirmado=True)
    response, out = _csv_response("lineas.csv")
    out.writerow([
        "archivo", "proveedor", "cif", "numero", "fecha", "albaran", "albaran_fecha",
        "codigo", "descripcion", "cantidad", "precio_unitario", "iva_porcentaje", "importe",
    ])
    for line in lines:
        d = getattr(line.document, "datos", None)
        out.writerow([
            line.document.original_filename,
            d.proveedor_nombre if d else "", d.proveedor_cif if d else "",
            d.numero_factura if d else "",
            d.fecha_factura.isoformat() if d and d.fecha_factura else "",
            line.albaran, line.albaran_fecha.isoformat() if line.albaran_fecha else "",
            line.codigo_articulo, line.descripcion,
            _es(line.cantidad), _es(line.precio_unitario), _es(line.iva_porcentaje), _es(line.importe),
        ])
    return response
