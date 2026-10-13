from django.contrib import admin

from .models import CompraProveedor, MovimientoAcreedor, Reserva


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


@admin.register(MovimientoAcreedor)
class MovimientoAcreedorAdmin(admin.ModelAdmin):
    list_display = ("fecha", "acreedor", "tipo", "documento", "contrapartida", "debe", "haber")
    list_filter = ("tipo", "fichero")
    search_fields = ("acreedor", "documento", "concepto", "contrapartida")
    date_hierarchy = "fecha"
