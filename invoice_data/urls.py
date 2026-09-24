from django.urls import path
from django.conf import settings
from invoice_data import views
from django.conf.urls.static import static

app_name = "invoice_data"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload/", views.upload_invoices, name="upload"),
    path("<int:pk>/", views.document_detail, name="detail"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)