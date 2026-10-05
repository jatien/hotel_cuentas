from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.forms.models import BaseInlineFormSet

from .models import Apunte, Asiento, CuentaContable, Ejercicio
from .services import asignar_contrapartidas
from .models import DatosEmpresa


@admin.register(DatosEmpresa)
class DatosEmpresaAdmin(admin.ModelAdmin):
    list_display = ["denominacion", "nif", "forma_juridica"]

@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "nivel", "imputable", "activa"]
    list_filter = ["imputable", "activa"]
    search_fields = ["codigo", "nombre"]
    autocomplete_fields = ["padre"]


@admin.register(Ejercicio)
class EjercicioAdmin(admin.ModelAdmin):
    list_display = ["anio", "fecha_inicio", "fecha_fin", "cerrado"]


class ApunteFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        debe = haber = Decimal("0")
        n = 0
        for form in self.forms:
            datos = form.cleaned_data
            if not datos or datos.get("DELETE"):
                continue
            debe += datos.get("debe") or 0
            haber += datos.get("haber") or 0
            n += 1
        if n < 2:
            raise ValidationError("Un asiento necesita al menos dos apuntes.")
        if debe != haber:
            raise ValidationError(f"El asiento no cuadra: Debe {debe} ≠ Haber {haber}.")


class ApunteInline(admin.TabularInline):
    model = Apunte
    formset = ApunteFormSet
    fields = ["orden", "cuenta", "debe", "haber", "contrapartida", "concepto"]
    autocomplete_fields = ["cuenta", "contrapartida"]
    extra = 2


@admin.register(Asiento)
class AsientoAdmin(admin.ModelAdmin):
    list_display = ["numero", "fecha", "concepto", "origen", "estado", "total_debe", "total_haber"]
    list_filter = ["ejercicio", "origen", "estado"]
    date_hierarchy = "fecha"
    search_fields = ["concepto", "referencia_origen"]
    readonly_fields = ["ejercicio", "numero"]
    inlines = [ApunteInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _debe=Sum("apuntes__debe"), _haber=Sum("apuntes__haber")
        )

    @admin.display(description="Debe", ordering="_debe")
    def total_debe(self, obj):
        return obj._debe

    @admin.display(description="Haber", ordering="_haber")
    def total_haber(self, obj):
        return obj._haber

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        asignar_contrapartidas(form.instance)