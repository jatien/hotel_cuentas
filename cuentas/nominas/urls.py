
from django.urls import path
from . import views

urlpatterns = [
    path('', views.nominas_view, name='nominas'),
    path('importar/', views.importar_nomina, name='importar_nomina'),
    path('actualizar-campo/', views.actualizar_campo, name='actualizar_campo'),
    path('analisis/', views.analisis_view, name='nominas_analisis'),
]