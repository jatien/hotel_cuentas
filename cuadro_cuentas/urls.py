from django.urls import path

from . import views

app_name = "cuadro_cuentas"

urlpatterns = [
    path("libro/", views.libro_diario, name="libro_diario"),
    path("api/asientos/", views.api_crear_asiento, name="api_crear_asiento"),
    path("api/asientos/<int:pk>/", views.api_asiento, name="api_asiento"),
    path("api/asientos/<int:pk>/resumen/", views.api_resumen, name="api_resumen"),
    path("api/asientos/<int:pk>/estado/", views.api_estado, name="api_estado"),
    path("api/asientos/<int:pk>/apuntes/", views.api_crear_apunte, name="api_crear_apunte"),
    path("api/asientos/<int:pk>/orden/", views.api_ordenar, name="api_ordenar"),
    path("api/apuntes/<int:pk>/", views.api_apunte, name="api_apunte"),
]