from django.urls import path

from seguridad import views

app_name = "seguridad"

urlpatterns = [
    path("csp-report/", views.csp_report, name="csp_report"),
    path("health/", views.health, name="health"),
]
