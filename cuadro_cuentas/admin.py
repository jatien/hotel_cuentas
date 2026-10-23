from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.forms.models import BaseInlineFormSet

from .models import Adjunto, Apunte, Asiento, CuentaContable, DatosEmpresa, Ejercicio, HistorialAsiento
from .services import asignar_contrapartidas, numerar_apuntes


@admin.register(DatosEmpresa)
class DatosEmpresaAdmin(admin.ModelAdmin):
    list_display = ["codigo", "denominacion", "nif", "forma_juridica", "moneda"]

@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "nivel", "naturaleza_label", "imputable", "activa"]
    list_filter = ["imputable", "activa", "naturaleza"]
    search_fields = ["codigo", "nombre"]
    autocomplete_fields = ["padre"]


@admin.register(Ejercicio)
class EjercicioAdmin(admin.ModelAdmin):
    list_display = ["anio", "empresa", "fecha_inicio", "fecha_fin", "cerrado"]


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
    fields = ["orden", "cuenta", "debe", "haber", "contrapartida", "concepto", "punteo"]
    autocomplete_fields = ["cuenta", "contrapartida"]
    extra = 2


class AdjuntoInline(admin.TabularInline):
    model = Adjunto
    fields = ["tipo", "numero", "nombre", "archivo", "enlace"]
    extra = 0


@admin.register(Asiento)
class AsientoAdmin(admin.ModelAdmin):
    """Solo los borradores se modifican o borran aquí: lo contabilizado se corrige desde la
    ficha del asiento (revertir, rectificar o anular), que deja rastro en el historial."""
    list_display = ["numero", "fecha", "numero_externo", "concepto", "origen", "estado", "total_debe", "total_haber"]
    list_filter = ["ejercicio", "origen", "estado", "tipo"]
    date_hierarchy = "fecha"
    search_fields = ["concepto", "titulo", "numero_externo", "referencia_origen"]
    readonly_fields = ["ejercicio", "numero", "estado", "creado", "creado_por", "contabilizado_en",
                       "contabilizado_por", "anulado_en", "motivo_anulacion", "revierte", "rectifica"]
    inlines = [ApunteInline, AdjuntoInline]

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.estado != Asiento.Estado.BORRADOR:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.estado != Asiento.Estado.BORRADOR:
            return False
        return super().has_delete_permission(request, obj)

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
        numerar_apuntes(form.instance)
        asignar_contrapartidas(form.instance, recalcular=True)


@admin.register(HistorialAsiento)
class HistorialAsientoAdmin(admin.ModelAdmin):
    list_display = ["fecha", "ejercicio", "numero", "accion", "usuario", "detalle"]
    list_filter = ["accion", "ejercicio"]
    search_fields = ["detalle"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False