import tempfile
from pathlib import Path

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from invoice_data.services.extraction import extract_document
from invoice_data.services.line_items import extract_line_items
from invoice_data.services.ocr import run_ocr
from invoice_data.forms import InvoiceUploadForm
from invoice_data.models import ExtractedInvoiceData, IngestionStatus, InvoiceDocument, TipoEntidad
from invoice_data.services.ingestion import ingest_file, IMAGE_EXTENSIONS, PDF_EXTENSIONS

ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


def index(request):
    documents = InvoiceDocument.objects.all()[:20]
    stats = {
        "total": InvoiceDocument.objects.count(),
        "pendientes_ocr": InvoiceDocument.objects.filter(needs_ocr=True).count(),
        "listos": InvoiceDocument.objects.filter(
            needs_ocr=False, status=IngestionStatus.INGESTED
        ).count(),
        "errores": InvoiceDocument.objects.filter(status=IngestionStatus.ERROR).count(),
    }
    return render(request, "invoice_data/index.html", {"documents": documents, "stats": stats})


def upload_invoices(request):
    if request.method == "POST":
        form = InvoiceUploadForm(request.POST)
        uploaded_files = request.FILES.getlist("files")

        if not uploaded_files:
            messages.error(request, "No se ha seleccionado ningún archivo.")
        elif not form.is_valid():
            messages.error(request, "Selecciona un departamento válido.")
        else:
            tipo_entidad = form.cleaned_data["tipo_entidad"]
            departamento = form.cleaned_data["departamento"] if tipo_entidad == TipoEntidad.PROVEEDOR else ""
            created_count = 0
            duplicate_count = 0
            error_count = 0
            skipped_count = 0

            for uploaded_file in uploaded_files:
                suffix = Path(uploaded_file.name).suffix.lower()
                if suffix not in ALLOWED_EXTENSIONS:
                    skipped_count += 1
                    messages.warning(
                        request, f"Tipo de archivo no soportado, omitido: {uploaded_file.name}"
                    )
                    continue

                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    for chunk in uploaded_file.chunks():
                        tmp.write(chunk)
                    tmp_path = Path(tmp.name)

                try:
                    doc, created = ingest_file(
                        tmp_path,
                        departamento=departamento,
                        original_filename=uploaded_file.name,
                        tipo_entidad=tipo_entidad,
                    )
                    if not created:
                        duplicate_count += 1
                    elif doc.status == "error":
                        error_count += 1
                    else:
                        created_count += 1
                finally:
                    tmp_path.unlink(missing_ok=True)

            messages.success(
                request,
                f"{created_count} archivo(s) importados, {duplicate_count} duplicados, "
                f"{error_count} con error, {skipped_count} omitidos.",
            )
            return redirect(reverse("invoice_data:upload"))
    else:
        form = InvoiceUploadForm()

    return render(request, "invoice_data/upload.html", {"form": form})


def document_detail(request, pk):
    """
    Detail page for one InvoiceDocument. Extraction results are NOT
    considered final just because they exist - extracting overwrites the
    row with fresh (unconfirmed) data, and it's only "confirmado" once
    the user explicitly clicks Guardar. Eliminar clears the row entirely,
    so a bad extraction never sits there stale - the empty state always
    means "nothing here yet or it was intentionally cleared", never
    "extraction is frozen and wrong".
    """
    document = get_object_or_404(InvoiceDocument, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "run_ocr" and document.needs_ocr:
            run_ocr(document)
            messages.success(request, "OCR ejecutado correctamente.")

        elif action == "extract_data":
            extract_document(document)
            messages.success(request, "Datos extraidos. Revisa y pulsa Guardar si son correctos.")

        elif action == "save_data":
            updated = ExtractedInvoiceData.objects.filter(document=document).update(confirmado=True)
            if updated:
                messages.success(request, "Datos guardados como correctos.")

        elif action == "delete_data":
            ExtractedInvoiceData.objects.filter(document=document).delete()
            messages.warning(request, "Datos de cabecera eliminados.")

        elif action == "extract_lines":
            items = extract_line_items(document)
            if items:
                messages.success(request, f"{len(items)} linea(s) extraida(s). Revisa y pulsa Guardar si son correctas.")
            else:
                messages.warning(request, "No se ha reconocido ningun patron de lineas para este documento.")

        elif action == "save_lines":
            document.line_items.update(confirmado=True)
            messages.success(request, "Lineas guardadas como correctas.")

        elif action == "delete_lines":
            document.line_items.all().delete()
            messages.warning(request, "Lineas de producto eliminadas.")

        elif action == "delete_document":
            document.file.delete(save=False)  # removes the actual file from disk, not just the DB row
            document.delete()
            messages.warning(request, "Factura eliminada por completo.")
            return redirect("invoice_data:index")

        return redirect("invoice_data:detail", pk=document.pk)

    return render(
        request,
        "invoice_data/detail.html",
        {
            "document": document,
            "extracted_data": getattr(document, "extracted_data", None),
            "line_items": document.line_items.all(),
        },
    )