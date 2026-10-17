from django.urls import path

from . import views

app_name = "cajas"

urlpatterns = [
    path("", views.resumen, name="resumen"),
    path("nuevo/", views.nuevo, name="nuevo"),
    path("importar/", views.importar, name="importar"),
    path("<int:anio>/<int:mes>/", views.detalle, name="detalle"),
    path("<int:anio>/<int:mes>/editar/", views.editar, name="editar"),
    path("<int:anio>/<int:mes>/borrar/", views.borrar, name="borrar"),
]
