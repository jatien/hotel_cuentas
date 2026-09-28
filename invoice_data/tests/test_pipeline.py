"""
Tests of the database side of extraction, the web views and the commands.

Everything runs on generated, fictional PDFs (see fixtures.py). The hotel's
identity is fixed by override_settings so results do not depend on the
project's real settings.
"""

import csv
import io
import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from invoice_data.models import (
    EstadoExtraccion,
    ExtractedInvoiceData,
    InvoiceDocument,
    InvoiceLineItem,
    InvoiceTaxBreakdown,
    Proveedor,
)
from invoice_data.services.ingestion import ingest_file
from invoice_data.services.pipeline import DatosConfirmados, document_to_dict, process_document
from invoice_data.tests.base import MediaIsolatedTestCase
from invoice_data.tests.fixtures import invoice_text, make_image, make_invoice_pdf, make_scanned_pdf

HOTEL = {"INVOICE_OWN_NIF": "B99999999", "INVOICE_OWN_NAMES": ("HOTEL DEMO",)}


@override_settings(**HOTEL)
class PipelineTestCase(MediaIsolatedTestCase):
    """Base: a scratch folder for source files and a helper that ingests a generated invoice."""

    def setUp(self):
        """Create the scratch folder."""
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Remove the scratch folder."""
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def ingest(self, name="factura.pdf", total="121,00", extra_line="", **kwargs) -> InvoiceDocument:
        """Generates a fictional invoice PDF and registers it; returns the document."""
        path = make_invoice_pdf(self.tmp_dir / name, total=total, extra_line=extra_line)
        kwargs.setdefault("departamento", "cocina")
        document, _ = ingest_file(path, **kwargs)
        return document


class PipelineTests(PipelineTestCase):
    """process_document(): what gets stored, and the guarantees around it."""

    def test_a_consistent_invoice_is_stored_completely(self):
        document = self.ingest()
        datos = process_document(document)
        self.assertEqual(datos.estado, EstadoExtraccion.OK)
        self.assertTrue(datos.cuadra)
        self.assertEqual((datos.numero_factura, datos.proveedor_cif), ("X-2026/15", "B12345674"))
        self.assertEqual(datos.fecha_factura.isoformat(), "2026-02-17")
        self.assertEqual((datos.base_imponible, datos.iva_total, datos.total), (Decimal("100.00"), Decimal("21.00"), Decimal("121.00")))
        self.assertEqual(document.lineas.count(), 2)
        self.assertEqual(list(document.impuestos.values_list("tipo", "base", "cuota")), [(Decimal("21.00"), Decimal("100.00"), Decimal("21.00"))])

    def test_the_supplier_is_created_by_cif_and_the_hotel_is_not_taken_for_it(self):
        document = self.ingest()
        process_document(document)
        document.refresh_from_db()
        self.assertEqual(document.proveedor.cif, "B12345674")
        self.assertEqual(Proveedor.objects.count(), 1)
        self.assertNotIn("B99999999", Proveedor.objects.values_list("cif", flat=True))

    def test_two_invoices_of_the_same_supplier_share_one_supplier_row(self):
        process_document(self.ingest("a.pdf"))
        process_document(self.ingest("b.pdf", extra_line="otra linea distinta"))
        self.assertEqual(Proveedor.objects.count(), 1)

    def test_an_inconsistent_invoice_is_flagged_and_explains_why(self):
        document = self.ingest(total="130,00")  # printed total contradicts lines + IVA
        datos = process_document(document)
        self.assertEqual(datos.estado, EstadoExtraccion.REVISAR)
        self.assertFalse(datos.cuadra)
        self.assertTrue(datos.verificacion["avisos"])

    def test_reprocessing_replaces_instead_of_duplicating(self):
        document = self.ingest()
        process_document(document)
        process_document(document)
        self.assertEqual(ExtractedInvoiceData.objects.filter(document=document).count(), 1)
        self.assertEqual(InvoiceLineItem.objects.filter(document=document).count(), 2)
        self.assertEqual(InvoiceTaxBreakdown.objects.filter(document=document).count(), 1)

    def test_confirmed_data_is_protected_unless_forced(self):
        document = self.ingest()
        datos = process_document(document)
        datos.confirmado = True
        datos.save()
        with self.assertRaises(DatosConfirmados):
            process_document(document)
        self.assertEqual(process_document(document, force=True).estado, EstadoExtraccion.OK)

    def test_json_is_rebuilt_from_the_database_and_shows_manual_corrections(self):
        document = self.ingest()
        datos = process_document(document)
        datos.total = Decimal("125.50")
        datos.corregido = True
        datos.confirmado = True
        datos.save()
        data = document_to_dict(document)
        self.assertEqual(data["totales"]["total_con_iva"], "125.50")
        self.assertTrue(data["verificacion"]["corregido"])
        self.assertTrue(data["verificacion"]["confirmado"])
        json.dumps(data)  # must be serialisable as it is

    def test_a_document_never_extracted_serialises_as_pending_with_the_same_shape(self):
        data = document_to_dict(self.ingest())
        self.assertEqual(data["estado"], "PENDIENTE")
        self.assertEqual(data["lineas"], [])
        self.assertIn("totales", data)

    def test_a_scan_goes_through_full_page_ocr_then_the_same_extraction(self):
        """Routing test: the OCR step is replaced by a stub returning the invoice text."""
        document, _ = ingest_file(make_image(self.tmp_dir / "foto.png"), departamento="cocina")
        with mock.patch("invoice_data.services.pipeline.run_ocr", return_value=invoice_text()) as stub:
            datos = process_document(document)
        stub.assert_called_once()
        self.assertEqual(datos.total, Decimal("121.00"))
        self.assertEqual(document.lineas.count(), 2)


class DatabaseSafetyTests(PipelineTestCase):
    """OCR garbage must never make the save fail: PostgreSQL rejects over-long text and out-of-range numbers."""

    def test_overlong_text_is_trimmed_to_the_column_size(self):
        from invoice_data.services.pipeline import save_extraction
        data = document_to_dict(self.ingest())
        data["factura"]["numero"] = "N" * 300
        data["proveedor"] = {"nombre": "P" * 500, "cif": "B12345674"}
        data["lineas"] = [{"codigo": "C" * 200, "descripcion": "D" * 900, "cantidad": "1", "precio_unitario": "1", "iva_porcentaje": None,
                           "importe": "1.00", "albaran": "A" * 400, "albaran_fecha": None}]
        datos = save_extraction(self.ingest("otra.pdf", extra_line="x"), data)
        self.assertEqual(len(datos.numero_factura), 100)
        self.assertEqual(len(datos.proveedor_nombre), 200)
        line = InvoiceLineItem.objects.get(codigo_articulo="C" * 50)
        self.assertEqual((len(line.descripcion), len(line.albaran)), (500, 100))

    def test_absurdly_large_amounts_become_empty_instead_of_failing(self):
        from invoice_data.services.pipeline import save_extraction
        document = self.ingest()
        data = document_to_dict(document)
        data["totales"]["total_con_iva"] = "123456789012345.67"
        data["lineas"] = [{"codigo": "A", "descripcion": "x", "cantidad": "99999999999", "precio_unitario": "1", "iva_porcentaje": None,
                           "importe": "1.00", "albaran": None, "albaran_fecha": None}]
        datos = save_extraction(document, data)
        self.assertIsNone(datos.total)
        self.assertIsNone(InvoiceLineItem.objects.get(document=document).cantidad)


class UploadViewTests(PipelineTestCase):
    """The drop-zone import."""

    def post_upload(self, name="factura.pdf", **extra):
        """Uploads a generated invoice through the real view; returns the response."""
        content = make_invoice_pdf(self.tmp_dir / name, total=extra.pop("total", "121,00")).read_bytes()
        data = {"tipo_entidad": "proveedor", "departamento": "cocina", "extraer": "on", **extra}
        data["files"] = SimpleUploadedFile(name, content, content_type="application/pdf")
        return self.client.post(reverse("invoice_data:upload"), data, follow=True)

    def test_upload_imports_and_extracts(self):
        response = self.post_upload()
        self.assertEqual(InvoiceDocument.objects.count(), 1)
        self.assertEqual(InvoiceDocument.objects.get().datos.total, Decimal("121.00"))
        self.assertContains(response, "1 importada(s), 1 extraída(s)")

    def test_upload_without_extracting(self):
        self.post_upload(extraer="")
        self.assertFalse(hasattr(InvoiceDocument.objects.get(), "datos"))

    def test_uploading_the_same_file_twice_reports_a_duplicate(self):
        self.post_upload()
        response = self.post_upload()
        self.assertEqual(InvoiceDocument.objects.count(), 1)
        self.assertContains(response, "1 duplicada(s)")

    def test_a_creditor_needs_no_department(self):
        self.post_upload(tipo_entidad="acreedor", departamento="")
        document = InvoiceDocument.objects.get()
        self.assertEqual((document.tipo_entidad, document.departamento), ("acreedor", ""))

    def test_a_supplier_without_department_is_rejected(self):
        response = self.post_upload(departamento="")
        self.assertEqual(InvoiceDocument.objects.count(), 0)
        self.assertContains(response, "departamento")

    def test_no_file_gives_a_spanish_error(self):
        response = self.client.post(reverse("invoice_data:upload"), {"tipo_entidad": "proveedor", "departamento": "cocina"}, follow=True)
        self.assertContains(response, "No se ha seleccionado ningún archivo")

    def test_an_unsupported_file_is_skipped_not_fatal(self):
        data = {"tipo_entidad": "proveedor", "departamento": "cocina",
                "files": SimpleUploadedFile("nota.txt", b"hola", content_type="text/plain")}
        response = self.client.post(reverse("invoice_data:upload"), data, follow=True)
        self.assertContains(response, "no soportado")
        self.assertEqual(InvoiceDocument.objects.count(), 0)

    def test_extraction_failure_keeps_the_import(self):
        """If extraction blows up, the file is still imported and the user is told."""
        with mock.patch("invoice_data.views.process_document", side_effect=RuntimeError("fallo simulado")):
            response = self.post_upload()
        self.assertEqual(InvoiceDocument.objects.count(), 1)
        self.assertContains(response, "no se pudo extraer")


class IndexViewTests(PipelineTestCase):
    """The list page: counters, filters, pagination-safe rendering."""

    def test_empty_archive_renders(self):
        response = self.client.get(reverse("invoice_data:index"))
        self.assertContains(response, "No hay facturas que mostrar")

    def test_counters_and_rows(self):
        process_document(self.ingest("a.pdf"))
        self.ingest("b.pdf", extra_line="pendiente sin extraer")
        response = self.client.get(reverse("invoice_data:index"))
        self.assertEqual(response.context["stats"]["total"], 2)
        self.assertEqual(response.context["stats"]["sin_extraer"], 1)
        self.assertEqual(response.context["stats"]["correctas"], 1)
        self.assertContains(response, "Suministros Ejemplo")

    def test_filter_by_state(self):
        process_document(self.ingest("a.pdf"))
        process_document(self.ingest("bad.pdf", total="130,00"))
        url = reverse("invoice_data:index")
        self.assertEqual(len(self.client.get(url, {"estado": "revisar"}).context["page"]), 1)
        self.assertEqual(len(self.client.get(url, {"estado": "ok"}).context["page"]), 1)

    def test_filter_by_text_and_department(self):
        process_document(self.ingest("a.pdf", departamento="cocina"))
        process_document(self.ingest("b.pdf", extra_line="x", departamento="bar"))
        url = reverse("invoice_data:index")
        self.assertEqual(len(self.client.get(url, {"departamento": "bar"}).context["page"]), 1)
        self.assertEqual(len(self.client.get(url, {"q": "B12345674"}).context["page"]), 2)
        self.assertEqual(len(self.client.get(url, {"q": "no-existe"}).context["page"]), 0)


class DetailViewTests(PipelineTestCase):
    """The review screen and its actions."""

    def detail(self, document):
        """URL of a document's review page."""
        return reverse("invoice_data:detail", args=[document.pk])

    def act(self, document, action, **data):
        """POSTs an action to the review page and follows the redirect."""
        return self.client.post(self.detail(document), {"action": action, **data}, follow=True)

    def test_pending_document_offers_extraction(self):
        response = self.client.get(self.detail(self.ingest()))
        self.assertContains(response, "Extraer datos")

    def test_extract_action_fills_the_screen(self):
        document = self.ingest()
        response = self.act(document, "extract")
        self.assertContains(response, "Datos extraídos")
        self.assertContains(response, "X-2026/15")
        self.assertContains(response, "TORNILLO GALVANIZADO")

    def test_save_confirms_and_marks_manual_corrections(self):
        document = self.ingest()
        process_document(document)
        datos = document.datos
        payload = {"numero_factura": "CORREGIDA-9", "fecha_factura": "2026-02-18", "proveedor_nombre": "Nombre Corregido S.L.",
                   "proveedor_cif": "B12345674", "base_imponible": "100.00", "iva_total": "21.00", "total": "121.00"}
        self.act(document, "save", **payload)
        datos.refresh_from_db()
        self.assertTrue(datos.confirmado)
        self.assertTrue(datos.corregido)
        self.assertEqual(datos.numero_factura, "CORREGIDA-9")

    def test_saving_without_changes_confirms_but_is_not_marked_as_corrected(self):
        document = self.ingest()
        datos = process_document(document)
        payload = {"numero_factura": datos.numero_factura, "fecha_factura": datos.fecha_factura.isoformat(),
                   "proveedor_nombre": datos.proveedor_nombre, "proveedor_cif": datos.proveedor_cif,
                   "base_imponible": "100.00", "iva_total": "21.00", "total": "121.00"}
        self.act(document, "save", **payload)
        datos.refresh_from_db()
        self.assertTrue(datos.confirmado)
        self.assertFalse(datos.corregido)

    def test_invalid_form_is_shown_again_and_nothing_is_saved(self):
        document = self.ingest()
        datos = process_document(document)
        response = self.client.post(self.detail(document), {"action": "save", "numero_factura": "X", "fecha_factura": "no-es-fecha", "total": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hay errores en el formulario")
        datos.refresh_from_db()
        self.assertFalse(datos.confirmado)

    def test_confirmed_invoice_cannot_be_re_extracted_until_unconfirmed(self):
        document = self.ingest()
        datos = process_document(document)
        datos.confirmado = True
        datos.save()
        response = self.act(document, "extract")
        self.assertContains(response, "ya está confirmada")
        self.act(document, "unconfirm")
        datos.refresh_from_db()
        self.assertFalse(datos.confirmado)

    def test_delete_data_keeps_the_file_but_clears_everything_extracted(self):
        document = self.ingest()
        process_document(document)
        self.act(document, "delete_data")
        document.refresh_from_db()
        self.assertFalse(hasattr(document, "datos"))
        self.assertEqual((document.lineas.count(), document.impuestos.count()), (0, 0))
        self.assertIsNone(document.proveedor)
        self.assertTrue(Path(document.file.path).exists())

    def test_delete_document_removes_the_row_and_the_file(self):
        document = self.ingest()
        process_document(document)
        file_path = Path(document.file.path)
        response = self.act(document, "delete_document")
        self.assertFalse(InvoiceDocument.objects.filter(pk=document.pk).exists())
        self.assertFalse(file_path.exists())
        self.assertContains(response, "Factura eliminada por completo")

    def test_unknown_document_is_a_404(self):
        self.assertEqual(self.client.get(reverse("invoice_data:detail", args=[9999])).status_code, 404)


class ExportViewTests(PipelineTestCase):
    """JSON and CSV outputs, the ones meant for analysis."""

    def test_json_endpoint(self):
        document = self.ingest()
        process_document(document)
        response = self.client.get(reverse("invoice_data:json", args=[document.pk]))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["esquema"], "factura/1")
        self.assertEqual(data["totales"]["total_con_iva"], "121.00")
        self.assertEqual(len(data["lineas"]), 2)

    def test_json_download_sets_a_filename(self):
        document = self.ingest("mi factura.pdf")
        response = self.client.get(reverse("invoice_data:json", args=[document.pk]), {"descargar": 1})
        self.assertIn('filename="mi factura.pdf.json"', response["Content-Disposition"])

    def rows(self, response) -> list[list[str]]:
        """Parses a CSV response (';' separated, BOM) into rows."""
        text = response.content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text), delimiter=";"))

    def test_invoices_csv_has_one_row_per_extracted_invoice_with_decimal_commas(self):
        process_document(self.ingest("a.pdf"))
        self.ingest("sin-extraer.pdf", extra_line="no extraida")
        rows = self.rows(self.client.get(reverse("invoice_data:export_facturas")))
        self.assertEqual(rows[0][:3], ["archivo", "tipo", "departamento"])
        self.assertEqual(len(rows), 2)  # header + the one extracted invoice
        self.assertIn("121,00", rows[1])
        self.assertIn("2026-02-17", rows[1])

    def test_only_confirmed_filter(self):
        process_document(self.ingest("a.pdf"))
        confirmed = process_document(self.ingest("b.pdf", extra_line="x"))
        confirmed.confirmado = True
        confirmed.save()
        url = reverse("invoice_data:export_facturas")
        self.assertEqual(len(self.rows(self.client.get(url))), 3)
        self.assertEqual(len(self.rows(self.client.get(url, {"confirmadas": 1}))), 2)

    def test_lines_csv_has_one_row_per_line_with_the_invoice_context(self):
        process_document(self.ingest())
        rows = self.rows(self.client.get(reverse("invoice_data:export_lineas")))
        self.assertEqual(len(rows), 3)  # header + 2 lines
        header = rows[0]
        first = dict(zip(header, rows[1]))
        self.assertEqual(first["codigo"], "AB1234")
        self.assertEqual(first["numero"], "X-2026/15")
        self.assertEqual(first["importe"], "25,00")


class CommandTests(PipelineTestCase):
    """The management commands."""

    def run_command(self, *args, **options) -> str:
        """Runs a command and returns what it printed."""
        out = io.StringIO()
        call_command(*args, stdout=out, **options)
        return out.getvalue()

    def test_process_invoices_handles_only_pending_documents_by_default(self):
        done = self.ingest("a.pdf")
        process_document(done)
        pending = self.ingest("b.pdf", extra_line="pendiente")
        output = self.run_command("process_invoices")
        self.assertIn("b.pdf", output)
        self.assertNotIn("a.pdf", output)
        self.assertTrue(hasattr(InvoiceDocument.objects.get(pk=pending.pk), "datos"))

    def test_process_invoices_never_overwrites_confirmed_data_without_force(self):
        document = self.ingest()
        datos = process_document(document)
        datos.confirmado = True
        datos.save()
        self.assertIn("omitida (confirmada)", self.run_command("process_invoices", "--reprocesar"))
        self.assertNotIn("omitida (confirmada)", self.run_command("process_invoices", "--reprocesar", "--forzar"))

    def test_process_invoices_can_print_the_json(self):
        document = self.ingest()
        output = self.run_command("process_invoices", "--id", str(document.pk), "--json")
        self.assertIn('"esquema": "factura/1"', output)

    def test_process_invoices_isolates_a_failing_document(self):
        self.ingest("a.pdf")
        self.ingest("b.pdf", extra_line="segunda")
        with mock.patch("invoice_data.management.commands.process_invoices.process_document", side_effect=RuntimeError("boom")):
            output = self.run_command("process_invoices")
        self.assertEqual(output.count("ERROR"), 2)  # both reported, none aborted the run
        self.assertIn("2 con error", output)

    def test_import_invoices_reads_the_proveedores_and_acreedores_structure(self):
        (self.tmp_dir / "proveedores" / "cocina" / "2026").mkdir(parents=True)
        (self.tmp_dir / "acreedores").mkdir()
        make_invoice_pdf(self.tmp_dir / "proveedores" / "cocina" / "2026" / "a.pdf")
        make_invoice_pdf(self.tmp_dir / "acreedores" / "b.pdf", extra_line="distinta")
        self.run_command("import_invoices", str(self.tmp_dir))
        by_name = {d.original_filename: d for d in InvoiceDocument.objects.all()}
        self.assertEqual((by_name["a.pdf"].tipo_entidad, by_name["a.pdf"].departamento), ("proveedor", "cocina"))
        self.assertEqual((by_name["b.pdf"].tipo_entidad, by_name["b.pdf"].departamento), ("acreedor", ""))

    def test_import_invoices_requires_one_of_the_two_folders(self):
        with self.assertRaises(CommandError):
            self.run_command("import_invoices", str(self.tmp_dir))

    def test_check_invoices_dry_run_writes_csv_and_one_json_per_file_without_touching_the_database(self):
        folder = self.tmp_dir / "lote"
        folder.mkdir()
        make_invoice_pdf(folder / "buena.pdf")
        make_invoice_pdf(folder / "mala.pdf", total="130,00", numero="X-2026/16")
        make_scanned_pdf(folder / "escaneo.pdf")
        (folder / "rota.pdf").write_text("no es pdf")
        output = self.run_command("check_invoices", str(folder))

        self.assertEqual(InvoiceDocument.objects.count(), 0)  # a dry run
        for state in ("OK", "REVISAR", "OCR", "ERROR"):
            self.assertIn(state, output)
        self.assertTrue((self.tmp_dir / "resultados_lote.csv").exists())
        files = sorted(p.name for p in (self.tmp_dir / "lote_json").iterdir())
        self.assertEqual(files, ["buena.pdf.json", "escaneo.pdf.json", "mala.pdf.json", "rota.pdf.json"])
        estados = {p.name: json.loads(p.read_text(encoding="utf-8"))["estado"] for p in (self.tmp_dir / "lote_json").iterdir()}
        self.assertEqual(estados, {"buena.pdf.json": "OK", "mala.pdf.json": "REVISAR", "escaneo.pdf.json": "OCR", "rota.pdf.json": "ERROR"})

    def test_check_invoices_flags_the_same_invoice_twice(self):
        folder = self.tmp_dir / "lote"
        folder.mkdir()
        make_invoice_pdf(folder / "uno.pdf")
        make_invoice_pdf(folder / "dos.pdf", extra_line="distinto")  # same supplier + number, different file
        self.assertIn("posible duplicada", self.run_command("check_invoices", str(folder)))

    def test_check_invoices_needs_an_existing_folder(self):
        with self.assertRaises(CommandError):
            self.run_command("check_invoices", str(self.tmp_dir / "no-existe"))
