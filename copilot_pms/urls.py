from django.urls import path

from copilot_pms import views

app_name = "copilot_pms"

urlpatterns = [
    path("", views.index, name="index"),
    path("preguntar/", views.preguntar_form, name="preguntar"),
    path("graficos/", views.galeria, name="galeria"),
    path("<uuid:public_id>/", views.conversacion, name="conversacion"),
    path("api/preguntar", views.api_preguntar, name="api_preguntar"),
    path("api/herramientas", views.api_herramientas, name="api_herramientas"),
]
