"""
URLs de Detección de facturas con IA, montadas en mysite/urls.py bajo "facturas-ia/".
Las plantillas usan siempre los nombres ({% url 'invoice_agent:...' %}).
"""

from django.urls import path

from invoice_agent import views
from invoice_agent.api import views as api

app_name = "invoice_agent"

urlpatterns = [
    path("", views.index, name="index"),
    path("<uuid:document_id>/", views.detalle, name="detalle"),
    path("<uuid:document_id>/<slug:que>/", views.accion, name="accion"),
    path("original/<str:token>/", views.original, name="original"),
    # API JSON
    path("api/documentos", api.documents, name="api_documents"),
    path("api/documentos/<uuid:document_id>", api.document_detail, name="api_document"),
    path("api/documentos/<uuid:document_id>/extraccion", api.document_extraction, name="api_extraction"),
    path("api/documentos/<uuid:document_id>/original", api.document_original, name="api_original"),
    path("api/documentos/<uuid:document_id>/reprocesar", api.document_reprocess, name="api_reprocess"),
    path("api/borradores/<int:draft_id>", api.draft_detail, name="api_draft"),
    path("api/borradores/<int:draft_id>/aprobar", api.draft_approve, name="api_approve"),
    path("api/borradores/<int:draft_id>/rechazar", api.draft_reject, name="api_reject"),
    path("api/borradores/<int:draft_id>/eventos", api.draft_events, name="api_events"),
]
