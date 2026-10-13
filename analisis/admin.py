from django.contrib import admin

from .models import AsignacionDepartamento, ApunteIva, CompraProveedor, ExtraPersonal, FacturaAcreedor, Reserva


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
    list_display = ("fecha", "libro", "documento", "concepto", "cuenta", "cuota")
    list_filter = ("libro",)
    search_fields = ("documento", "concepto", "cuenta")
    date_hierarchy = "fecha"


@admin.register(ExtraPersonal)
class ExtraPersonalAdmin(admin.ModelAdmin):
    list_display = ("anio", "mes", "persona", "importe")
    list_filter = ("anio", "mes")
    search_fields = ("persona",)
