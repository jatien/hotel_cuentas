"""
URLs of Gestión de Facturas. The project mounts them under one prefix, e.g.
    path("gestion-facturas/", include("invoice_data.urls"))
All templates use these names ({% url 'invoice_data:...' %}), never literal paths.
"""

from django.urls import path

from invoice_data import views

app_name = "invoice_data"

urlpatterns = [
    path("", views.index, name="index"),
    path("importar/", views.upload_invoices, name="upload"),
    path("<int:pk>/", views.document_detail, name="detail"),
    path("<int:pk>/json/", views.document_json, name="json"),
    path("exportar/facturas.csv", views.export_facturas_csv, name="export_facturas"),
    path("exportar/lineas.csv", views.export_lineas_csv, name="export_lineas"),
]
