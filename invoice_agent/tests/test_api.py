"""API JSON y pantallas: subida, consulta, correcciones con auditoría, aprobación, permisos y aislamiento por empresa."""

import json
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.test import override_settings
from django.urls import reverse

from invoice_agent.models import (
    AccionRevision,
    Borrador,
    Documento,
    EstadoDocumento,
    EventoRevision,
)
from invoice_agent.providers.fake import FakeProvider
from invoice_agent.services import worker
from invoice_agent.tests.helpers import (
    INVOICE_LINES,
    FakeOcr,
    IsolatedTestCase,
    field,
    make_pdf,
    model_json,
    upload,
)


class ApiTestCase(IsolatedTestCase):
    def post_file(self, name="f.pdf", empresa=None, lines=None):
        path = make_pdf(self.work / name, lines=(lines or INVOICE_LINES) + [name])
        return self.client.post(
            reverse("invoice_agent:api_documents"), {"file": upload(path), "empresa": (empresa or self.empresa).pk}
        )

    def process(self, *responses):
        claimed = worker.claim_next("w")
        worker.run_claimed(claimed, provider=FakeProvider(list(responses)), ocr_engine=FakeOcr(), sleep=lambda s: None)

    def uploaded_and_processed(self, output=None, name="f.pdf"):
        data = self.post_file(name).json()
        self.process(output or model_json())
        return data["id"], Borrador.objects.get(documento__public_id=data["id"])

    def patch(self, draft_id, payload):
        return self.client.patch(
            reverse("invoice_agent:api_draft", args=[draft_id]), json.dumps(payload), content_type="application/json"
        )

    def post_json(self, name, args, payload=None):
        return self.client.post(reverse(f"invoice_agent:{name}", args=args), json.dumps(payload or {}),
                                content_type="application/json")


class ApiTests(ApiTestCase):
    def test_upload_returns_202_and_duplicate_200(self):
        response = self.post_file()
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["estado"], "queued")
        self.assertIn(body["id"], response["Location"])
        again = self.post_file()
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["duplicado"])
        self.assertEqual(again.json()["id"], body["id"])

    def test_upload_errors_are_consistent(self):
        response = self.client.post(reverse("invoice_agent:api_documents"), {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "FILE_REQUIRED")

    def test_status_list_and_extraction(self):
        doc_id, borrador = self.uploaded_and_processed()
        status = self.client.get(reverse("invoice_agent:api_document", args=[doc_id])).json()
        self.assertEqual(status["estado"], "pending_approval")
        self.assertEqual(status["borrador_id"], borrador.pk)

        listing = self.client.get(reverse("invoice_agent:api_documents"), {"estado": "pending_approval", "page_size": 1}).json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(len(listing["results"]), 1)
        bad = self.client.get(reverse("invoice_agent:api_documents"), {"estado": "inventado"})
        self.assertEqual(bad.status_code, 400)

        extraction = self.client.get(reverse("invoice_agent:api_extraction", args=[doc_id])).json()
        self.assertEqual(extraction["evidencias"]["invoice_number"]["page"], 1)
        self.assertEqual(extraction["evidencias"]["due_date"]["state"], "absent")
        self.assertEqual(extraction["borrador"]["total_bruto"], "121.00")  # importes como texto, nunca float
        self.assertEqual(extraction["prompt_version"], "factura-ia/1.0")

    def test_correction_is_audited_with_extracted_previous_and_new_values(self):
        doc_id, borrador = self.uploaded_and_processed(model_json(gross_total=field("150.00")))
        self.assertEqual(Documento.objects.get(public_id=doc_id).estado, EstadoDocumento.NEEDS_REVIEW)

        response = self.patch(borrador.pk, {"total_bruto": "121.00", "comentario": "Total mal leído"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["estado"], "pending_approval")
        evento = EventoRevision.objects.get(borrador=borrador, accion=AccionRevision.CORRECTED)
        self.assertEqual(
            (evento.campo, evento.valor_extraido, evento.valor_anterior, evento.valor_nuevo),
            ("total_bruto", "150.00", "150.00", "121.00"),
        )
        self.assertEqual(evento.comentario, "Total mal leído")
        # Lo extraído no cambia: solo el borrador.
        self.assertEqual(str(borrador.extraccion.total_bruto), "150.00")

        events = self.client.get(reverse("invoice_agent:api_events", args=[borrador.pk])).json()["results"]
        self.assertIn("corrected", [e["accion"] for e in events])

    def test_lines_and_taxes_can_be_replaced(self):
        _, borrador = self.uploaded_and_processed()
        payload = {"lineas": [{"descripcion": "Todo", "cantidad": "1", "precio_unitario": "100", "subtotal": "100"}]}
        response = self.patch(borrador.pk, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["lineas"]), 1)
        self.assertTrue(EventoRevision.objects.filter(borrador=borrador, campo="lineas").exists())

    def test_acknowledging_warnings_but_not_errors(self):
        _, borrador = self.uploaded_and_processed(model_json(amount_due=field("100.00")))
        self.assertEqual(borrador.documento.estado, EstadoDocumento.NEEDS_REVIEW)
        bad = self.patch(borrador.pk, {"aceptar_avisos": ["TOTAL_MISMATCH"]})
        self.assertEqual(bad.status_code, 422)
        ok = self.patch(borrador.pk, {"aceptar_avisos": ["AMOUNT_DUE_MISMATCH"], "comentario": "Hubo un anticipo"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["estado"], "pending_approval")
        self.assertTrue(EventoRevision.objects.filter(accion=AccionRevision.ACKNOWLEDGED).exists())

    def test_invalid_patch_payloads(self):
        _, borrador = self.uploaded_and_processed()
        self.assertEqual(self.patch(borrador.pk, {"campo_inventado": 1}).status_code, 422)
        self.assertEqual(self.patch(borrador.pk, {"total_bruto": "no-es-numero"}).status_code, 422)
        self.assertEqual(self.patch(borrador.pk, {"total_bruto": "1e20"}).status_code, 422)
        raw = self.client.patch(reverse("invoice_agent:api_draft", args=[borrador.pk]), "{", content_type="application/json")
        self.assertEqual(raw.json()["error"]["code"], "INVALID_JSON")

    def test_approve_and_reject_transitions(self):
        doc_id, borrador = self.uploaded_and_processed()
        response = self.post_json("api_approve", [borrador.pk], {"comentario": "OK"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["estado"], "approved")
        borrador.refresh_from_db()
        self.assertIsNotNone(borrador.aprobado_en)
        self.assertEqual(borrador.proveedor.cif, "B12345674")  # catálogo común de proveedores (invoice_data)
        # approved es final
        self.assertEqual(self.patch(borrador.pk, {"total_bruto": "1"}).status_code, 409)
        self.assertEqual(self.post_json("api_reject", [borrador.pk]).status_code, 409)
        self.assertEqual(self.post_json("api_reprocess", [doc_id]).status_code, 409)

    def test_cannot_approve_while_needs_review(self):
        _, borrador = self.uploaded_and_processed(model_json(gross_total=field("150.00")))
        response = self.post_json("api_approve", [borrador.pk])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "NOT_PENDING_APPROVAL")
        rejected = self.post_json("api_reject", [borrador.pk], {"comentario": "No es nuestra"})
        self.assertEqual(rejected.json()["estado"], "rejected")

    def test_second_copy_cannot_be_approved(self):
        _, first = self.uploaded_and_processed(name="a.pdf")
        self.post_json("api_approve", [first.pk])
        _, second = self.uploaded_and_processed(name="b.pdf")
        codes = {i["code"] for i in second.incidencias}
        self.assertIn("DUPLICATE_APPROVED", codes)
        self.assertEqual(self.post_json("api_approve", [second.pk]).status_code, 409)

    def test_database_constraint_blocks_double_approval_race(self):
        _, first = self.uploaded_and_processed(name="a.pdf")
        self.post_json("api_approve", [first.pk])
        _, second = self.uploaded_and_processed(name="b.pdf")
        Documento.objects.filter(pk=second.documento_id).update(estado=EstadoDocumento.PENDING_APPROVAL)
        # Simula que la revalidación no ve el duplicado (carrera): la restricción de la base de datos lo para.
        with mock.patch("invoice_agent.services.drafts.blockers", return_value=[]):
            response = self.post_json("api_approve", [second.pk])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "DUPLICATE_APPROVED")

    def test_reprocess_with_corrections_needs_confirmation(self):
        doc_id, borrador = self.uploaded_and_processed(model_json(gross_total=field("150.00")))
        self.patch(borrador.pk, {"total_bruto": "121.00"})
        refused = self.post_json("api_reprocess", [doc_id])
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["error"]["code"], "HAS_CORRECTIONS")
        accepted = self.post_json("api_reprocess", [doc_id], {"descartar_correcciones": True})
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.json()["estado"], "queued")

    def test_signed_original_url(self):
        doc_id, _ = self.uploaded_and_processed()
        url = self.client.get(reverse("invoice_agent:api_original", args=[doc_id])).json()["url"]
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))
        tampered = url.rstrip("/")[:-3] + "xyz/"
        self.assertEqual(self.client.get(tampered).status_code, 404)
        with override_settings(INVOICE_AGENT={**settings.INVOICE_AGENT, "SIGNED_URL_SECONDS": -1}):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_method_not_allowed_and_unknown_ids(self):
        self.assertEqual(self.client.delete(reverse("invoice_agent:api_documents")).status_code, 405)
        missing = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(self.client.get(reverse("invoice_agent:api_document", args=[missing])).status_code, 404)


@override_settings()
class TenantIsolationTests(ApiTestCase):
    agent_settings = {"REQUIRE_LOGIN": True}

    def setUp(self):
        super().setUp()
        self.ana = User.objects.create_user("ana", password="x")
        self.ana.user_permissions.add(Permission.objects.get(codename="change_borrador"))
        self.empresa.usuarios.add(self.ana)
        self.luis = User.objects.create_user("luis", password="x")
        self.otra.usuarios.add(self.luis)
        self.client.force_login(self.ana)

    def test_anonymous_gets_401_and_screens_redirect(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("invoice_agent:api_documents")).status_code, 401)
        self.assertEqual(self.client.get(reverse("invoice_agent:index")).status_code, 302)

    def test_cannot_upload_to_or_read_other_company(self):
        self.assertEqual(self.post_file(empresa=self.otra).status_code, 404)
        doc_id, borrador = self.uploaded_and_processed()
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(reverse("invoice_agent:api_document", args=[doc_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("invoice_agent:api_extraction", args=[doc_id])).status_code, 404)
        self.assertEqual(self.patch(borrador.pk, {"total_bruto": "1"}).status_code, 404)
        self.assertEqual(self.post_json("api_approve", [borrador.pk]).status_code, 404)
        self.assertEqual(self.client.get(reverse("invoice_agent:detalle", args=[doc_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("invoice_agent:api_documents")).json()["count"], 0)

    def test_client_cannot_choose_company_through_payload(self):
        _, borrador = self.uploaded_and_processed()
        self.assertEqual(self.patch(borrador.pk, {"empresa": self.otra.pk}).status_code, 422)

    def test_approval_requires_permission_and_records_user(self):
        _, borrador = self.uploaded_and_processed()
        viewer = User.objects.create_user("vera", password="x")
        self.empresa.usuarios.add(viewer)
        self.client.force_login(viewer)
        self.assertEqual(self.post_json("api_approve", [borrador.pk]).status_code, 403)
        self.client.force_login(self.ana)
        self.assertEqual(self.post_json("api_approve", [borrador.pk]).status_code, 200)
        borrador.refresh_from_db()
        self.assertEqual(borrador.aprobado_por, self.ana)
        evento = EventoRevision.objects.get(borrador=borrador, accion=AccionRevision.APPROVED)
        self.assertEqual((evento.actor, evento.actor_label), (self.ana, "ana"))


class ScreenTests(ApiTestCase):
    def test_index_upload_and_list(self):
        path = make_pdf(self.work / "p.pdf")
        response = self.client.post(
            reverse("invoice_agent:index"), {"empresa": self.empresa.pk, "ficheros": [upload(path)]}, follow=True
        )
        self.assertContains(response, "en cola")
        self.assertContains(response, "p.pdf")

    def test_detail_save_and_approve(self):
        doc_id, borrador = self.uploaded_and_processed(model_json(gross_total=field("150.00")))
        page = self.client.get(reverse("invoice_agent:detalle", args=[doc_id]))
        self.assertContains(page, "Necesita revisión")
        self.assertContains(page, "Extraído: 150.00")

        form = page.context["form"]
        data = {f"b-{name}": (form.initial.get(name) or "") for name in form.fields}
        data["b-fecha_factura"] = "15/09/2026"
        data["b-total_bruto"] = "121,00"
        for prefix, formset in (("l", page.context["lineas"]), ("t", page.context["impuestos"])):
            data[f"{prefix}-TOTAL_FORMS"] = str(len(formset.forms))
            data[f"{prefix}-INITIAL_FORMS"] = str(formset.initial_form_count())
            for index, sub in enumerate(formset.forms):
                for name, f in sub.fields.items():
                    value = sub.initial.get(name, "")
                    data[f"{prefix}-{index}-{name}"] = f.prepare_value(value) if hasattr(f, "prepare_value") and value is not None else ""
        response = self.client.post(reverse("invoice_agent:accion", args=[doc_id, "guardar"]), data, follow=True)
        self.assertContains(response, "Cambios guardados")
        self.assertEqual(Documento.objects.get(public_id=doc_id).estado, EstadoDocumento.PENDING_APPROVAL)
        borrador.refresh_from_db()
        self.assertEqual(borrador.lineas.count(), 2)  # las líneas no se pierden al guardar

        response = self.client.post(reverse("invoice_agent:accion", args=[doc_id, "aprobar"]), {"comentario": ""}, follow=True)
        self.assertContains(response, "Factura aprobada")
        self.assertEqual(Documento.objects.get(public_id=doc_id).estado, EstadoDocumento.APPROVED)
