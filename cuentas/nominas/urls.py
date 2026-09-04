
from django.urls import path
from . import views

urlpatterns = [
    path('', views.nominas_view, name='nominas'),
]