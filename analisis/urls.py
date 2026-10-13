from django.urls import path

from . import views

app_name = "analisis"

urlpatterns = [
    path("", views.resumen, name="resumen"),
]
