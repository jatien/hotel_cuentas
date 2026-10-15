from datetime import date
from decimal import Decimal

from django.contrib import admin, messages

from .models import (AsignacionDepartamento, ApunteIva, CompraProveedor, ExtraPersonal, FacturaAcreedor,
                     GastoAdicional, Reserva)


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ("numero", "agencia", "estado", "entrada", "salida", "habitaciones",
                    "total_estancias", "fact_estancias", "pend_estancias")
    list_filter = ("estado", "agencia", "tarifa")
    search_fields = ("numero", "ref_agencia", "grupo", "facturas")
    date_hierarchy = "entrada"


@admin.register(CompraProveedor)
class CompraProveedorAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "numero", "proveedor", "bruto", "iva", "total")
    list_filter = ("tipo", "proveedor")
    search_fields = ("numero", "proveedor", "nif")
    date_hierarchy = "fecha"


@admin.register(FacturaAcreedor)
class FacturaAcreedorAdmin(admin.ModelAdmin):
    list_display = ("fecha", "acreedor", "documento", "cuenta", "importe")
    list_filter = ("fichero",)
    search_fields = ("acreedor", "documento", "cuenta")
    date_hierarchy = "fecha"


@admin.register(AsignacionDepartamento)
class AsignacionDepartamentoAdmin(admin.ModelAdmin):
    """Asignar departamentos de un vistazo: el desplegable se edita en la propia lista."""

    list_display = ("nombre", "origen", "clave", "departamento")
    list_editable = ("departamento",)
    list_filter = ("origen", ("departamento", admin.EmptyFieldListFilter), "departamento")
    search_fields = ("nombre", "clave")
    list_per_page = 200


@admin.register(ApunteIva)
class ApunteIvaAdmin(admin.ModelAdmin):
    list_display = ("fecha", "libro", "documento", "concepto", "cuenta", "cuota", "asiento")
    list_filter = ("libro",)
    search_fields = ("documento", "concepto", "cuenta", "asiento")
    date_hierarchy = "fecha"
    actions = ["crear_gasto_adicional"]

    @admin.action(description="Crear gasto adicional (IVA al 21 %%, repartido en el año)")
    def crear_gasto_adicional(self, request, queryset):
        creados = 0
        for a in queryset.filter(libro=ApunteIva.Libro.SOPORTADO):
            if GastoAdicional.objects.filter(asiento_iva=a.asiento).exists():
                continue
            GastoAdicional.objects.create(
                concepto=a.concepto.removeprefix("S/FRA ").strip() or "Gasto adicional",
                acreedor=a.concepto.removeprefix("S/FRA ").strip(),
                base=(a.cuota / Decimal("0.21")).quantize(Decimal("0.01")), iva=a.cuota,
                desde=date(a.fecha.year, 1, 1), hasta=date(a.fecha.year, 12, 31), asiento_iva=a.asiento,
            )
            creados += 1
        self.message_user(request, f"{creados} gasto(s) adicional(es) creado(s). Revisa la cuenta, la base y el "
                                   "periodo en «Gastos adicionales».", messages.SUCCESS)


@admin.register(ExtraPersonal)
class ExtraPersonalAdmin(admin.ModelAdmin):
    list_display = ("anio", "mes", "persona", "importe")
    list_filter = ("anio", "mes")
    search_fields = ("persona",)


@admin.register(GastoAdicional)
class GastoAdicionalAdmin(admin.ModelAdmin):
    list_display = ("concepto", "cuenta", "base", "iva", "desde", "hasta", "departamento")
    list_filter = ("cuenta", "departamento")
    search_fields = ("concepto", "acreedor")
