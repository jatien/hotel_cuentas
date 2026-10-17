from django.contrib import admin

from .models import CAMPOS_IMPORTE, CajaDia, CajaMes


class CajaDiaInline(admin.TabularInline):
    model = CajaDia
    fields = ["dia"] + CAMPOS_IMPORTE
    extra = 0


@admin.register(CajaMes)
class CajaMesAdmin(admin.ModelAdmin):
    list_display = ["__str__", "anio", "mes", "origen", "actualizado"]
    list_filter = ["anio"]
    inlines = [CajaDiaInline]
