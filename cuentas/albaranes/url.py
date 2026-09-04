from django.urls import path
from . import views

urlpatterns = [
    path('', views.albaranes_view, name='albaranes'),
]