from django.urls import path
from django.views.generic import TemplateView

from inicio import views

urlpatterns = [
    path("", views.home, name="home"),
    # La página de tarjetas anterior, por si hace falta mientras la rueda se completa.
    path("clasico/", TemplateView.as_view(template_name="home_clasico.html"), name="home_clasico"),
]
