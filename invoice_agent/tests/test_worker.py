"""Cola y worker: flujo completo, reintentos, fallos definitivos, entregas repetidas y concurrencia."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from invoice_agent.models import (
    Borrador,
    EstadoDocumento,
    EstadoTrabajo,
    Extraccion,
    Intento,
    ResultadoIntento,
    Trabajo,
)
from invoice_agent.providers.base import ProviderAuthError, ProviderTimeout
from invoice_agent.providers.fake import FakeProvider
from invoice_agent.schemas import ReprocessIn
from invoice_agent.services import review, worker
from invoice_agent.services.errors import Conflict
from invoice_agent.services.ingestion import ingest_upload
from invoice_agent.tests.helpers import (
    INVOICE_LINES,
    FakeOcr,
    IsolatedTestCase,
    field,
    make_pdf,
    model_json,
    upload,
)


def noop(_):
    return None


class WorkerTests(IsolatedTestCase):
    agent_settings = {"PROVIDER_MAX_RETRIES": 0, "JOB_MAX_ATTEMPTS": 2, "MAX_CONCURRENT_JOBS": 5}

    def upload_doc(self, name="f.pdf", empresa=None):
        doc, _ = ingest_upload(upload(make_pdf(self.work / name, lines=INVOICE_LINES + [name])), (empresa or self.empresa).pk, self.ctx())
        return doc

    def run_job(self, *responses):
        provider = FakeProvider(list(responses))
        claimed = worker.claim_next("w1")
        self.assertIsNotNone(claimed)
        worker.run_claimed(claimed, provider=provider, ocr_engine=FakeOcr(), sleep=noop)
        return claimed, provider

    def test_consistent_invoice_ends_pending_approval(self):
        doc = self.upload_doc()
        claimed, _ = self.run_job(model_json())
        doc.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.PENDING_APPROVAL)
        borrador = Borrador.objects.get(documento=doc)
        self.assertEqual(borrador.total_bruto, Decimal("121.00"))
        self.assertEqual(borrador.lineas.count(), 2)
        self.assertEqual(borrador.numero_norm, "F20260042")
        intento = Intento.objects.get(documento=doc)
        self.assertEqual(intento.resultado, ResultadoIntento.SUCCESS)
        self.assertEqual(intento.tokens_entrada, 1000)
        self.assertEqual(intento.metodo, "native")
        self.assertEqual(intento.prompt_version, "factura-ia/1.0")
        self.assertIsNotNone(intento.duracion_ms)
        self.assertEqual(Trabajo.objects.get(pk=claimed.trabajo.pk).estado, EstadoTrabajo.DONE)
        extraccion = Extraccion.objects.get(documento=doc)
        self.assertEqual(extraccion.evidencias["invoice_number"]["page"], 1)

    def test_inconsistent_invoice_needs_review(self):
        doc = self.upload_doc()
        self.run_job(model_json(gross_total=field("150.00")))
        doc.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.NEEDS_REVIEW)
        codes = {i["code"] for i in Borrador.objects.get(documento=doc).incidencias}
        self.assertIn("TOTAL_MISMATCH", codes)

    def test_transient_failure_requeues_then_fails_after_max_attempts(self):
        doc = self.upload_doc()
        self.run_job(ProviderTimeout())
        doc.refresh_from_db()
        trabajo = Trabajo.objects.get(documento=doc)
        self.assertEqual(doc.estado, EstadoDocumento.QUEUED)
        self.assertEqual(trabajo.estado, EstadoTrabajo.PENDING)
        self.assertEqual(doc.error_code, "PROVIDER_TIMEOUT")

        self.run_job(ProviderTimeout())
        doc.refresh_from_db()
        trabajo.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.FAILED)
        self.assertEqual(trabajo.estado, EstadoTrabajo.DEAD)
        self.assertEqual(Intento.objects.filter(documento=doc).count(), 2)
        self.assertIsNone(worker.claim_next("w1"))

    def test_fatal_failure_fails_immediately(self):
        doc = self.upload_doc()
        self.run_job(ProviderAuthError("clave"))
        doc.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.FAILED)
        self.assertEqual(doc.error_code, "PROVIDER_AUTH")
        self.assertEqual(Intento.objects.get(documento=doc).resultado, ResultadoIntento.FATAL_ERROR)

    def test_retry_waits_for_backoff(self):
        with override_settings(INVOICE_AGENT={**settings.INVOICE_AGENT, "JOB_RETRY_DELAY_SECONDS": 60}):
            self.upload_doc()
            self.run_job(ProviderTimeout())
            self.assertIsNone(worker.claim_next("w1"))  # aún no toca

    def test_repeated_delivery_does_not_duplicate(self):
        """Un worker zombi cuyo arrendamiento caducó no puede guardar después de que otro lo recupere."""
        doc = self.upload_doc()
        stale = worker.claim_next("zombi")
        Trabajo.objects.filter(pk=stale.trabajo.pk).update(bloqueado_en=timezone.now() - timedelta(hours=1))
        fresh = worker.claim_next("w2")
        self.assertEqual(fresh.trabajo.pk, stale.trabajo.pk)
        worker.run_claimed(fresh, provider=FakeProvider([model_json()]), ocr_engine=FakeOcr(), sleep=noop)
        worker.run_claimed(stale, provider=FakeProvider([model_json()]), ocr_engine=FakeOcr(), sleep=noop)
        self.assertEqual(Extraccion.objects.filter(documento=doc).count(), 1)
        self.assertEqual(Borrador.objects.filter(documento=doc).count(), 1)
        doc.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.PENDING_APPROVAL)

    def test_crashed_worker_lease_is_recovered(self):
        doc = self.upload_doc()
        claimed = worker.claim_next("muerto")
        Trabajo.objects.filter(pk=claimed.trabajo.pk).update(bloqueado_en=timezone.now() - timedelta(hours=1))
        self.run_job(model_json())
        doc.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.PENDING_APPROVAL)
        self.assertEqual(Intento.objects.filter(documento=doc).count(), 2)

    def test_concurrency_limit(self):
        self.upload_doc("a.pdf")
        self.upload_doc("b.pdf")
        with override_settings(INVOICE_AGENT={**settings.INVOICE_AGENT, "MAX_CONCURRENT_JOBS": 1}):
            self.assertIsNotNone(worker.claim_next("w1"))
            self.assertIsNone(worker.claim_next("w2"))

    def test_two_workers_take_different_jobs(self):
        self.upload_doc("a.pdf")
        self.upload_doc("b.pdf")
        first, second = worker.claim_next("w1"), worker.claim_next("w2")
        self.assertNotEqual(first.trabajo.pk, second.trabajo.pk)

    def test_reprocess_reuses_draft_and_keeps_history(self):
        doc = self.upload_doc()
        self.run_job(model_json(gross_total=field("150.00")))
        review.request_reprocess(self.ctx(), doc, ReprocessIn())
        doc.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.QUEUED)
        self.run_job(model_json())
        doc.refresh_from_db()
        self.assertEqual(doc.estado, EstadoDocumento.PENDING_APPROVAL)
        self.assertEqual(Borrador.objects.filter(documento=doc).count(), 1)
        self.assertEqual(Extraccion.objects.filter(documento=doc).count(), 2)
        self.assertEqual(Extraccion.objects.filter(documento=doc, actual=True).count(), 1)

    def test_reprocess_twice_is_one_job(self):
        doc = self.upload_doc()
        self.run_job(model_json(gross_total=field("150.00")))
        review.request_reprocess(self.ctx(), doc, ReprocessIn())
        with self.assertRaises(Conflict):
            review.request_reprocess(self.ctx(), doc, ReprocessIn())
        self.assertEqual(Trabajo.objects.filter(documento=doc, estado=EstadoTrabajo.PENDING).count(), 1)

    def test_duplicate_business_key_requires_review(self):
        first = self.upload_doc("a.pdf")
        self.run_job(model_json())
        second = self.upload_doc("b.pdf")
        self.run_job(model_json())
        second.refresh_from_db()
        self.assertEqual(second.estado, EstadoDocumento.NEEDS_REVIEW)
        codes = {i["code"] for i in Borrador.objects.get(documento=second).incidencias}
        self.assertIn("DUPLICATE_SUSPECTED", codes)
        first.refresh_from_db()
        self.assertEqual(first.estado, EstadoDocumento.PENDING_APPROVAL)

    def test_duplicates_are_not_detected_across_companies(self):
        self.upload_doc("a.pdf")
        self.run_job(model_json())
        other = self.upload_doc("b.pdf", empresa=self.otra)
        self.run_job(model_json(customer_tax_id=field(self.otra.nif)))
        codes = {i["code"] for i in Borrador.objects.get(documento=other).incidencias}
        self.assertNotIn("DUPLICATE_SUSPECTED", codes)

    def test_amounts_that_do_not_fit_are_dropped_not_crashing(self):
        doc = self.upload_doc()
        lines = [{"description": "x", "quantity": "1", "unit_price": "99999999999", "discount": None, "tax_rate": "21",
                  "line_subtotal": "1.00", "line_total": None, "unreadable_fields": [], "page": 1, "source_text": None}]
        self.run_job(model_json(line_items=lines))
        doc.refresh_from_db()
        self.assertNotEqual(doc.estado, EstadoDocumento.FAILED)
        self.assertIsNone(Borrador.objects.get(documento=doc).lineas.get().precio_unitario)
