from decimal import Decimal

from django import forms
from django.forms import formset_factory

from .models import CAMPOS_IMPORTE, MESES, CajaMes


class ImporteField(forms.DecimalField):
    """Importe que acepta coma decimal («1.234,56») además de punto («1234.56»)."""

    def to_python(self, value):
        if isinstance(value, str):
            texto = value.strip().replace("€", "").replace(" ", "")
            if "," in texto:
                texto = texto.replace(".", "").replace(",", ".")
            value = texto or None
        valor = super().to_python(value)
        return valor if valor is not None else Decimal("0")


class DiaForm(forms.Form):
    """Una fila (un día) de la tabla de cajas del mes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in CAMPOS_IMPORTE:
            self.fields[nombre] = ImporteField(max_digits=12, decimal_places=2, required=False)


DiasFormSet = formset_factory(DiaForm, extra=0)


class NotasMesForm(forms.ModelForm):
    class Meta:
        model = CajaMes
        fields = ["notas"]
        widgets = {"notas": forms.Textarea(attrs={"rows": 2, "placeholder": "Notas del mes (opcional)"})}


class ImportarCajasForm(forms.Form):
    archivo = forms.FileField(label="Excel de cajas (.xlsx)")
    anio = forms.IntegerField(
        label="Año", required=False, min_value=2000, max_value=2100,
        help_text="Opcional: si no se indica se deduce del nombre de la hoja («Mayo 26») o del fichero.",
    )
    mes = forms.TypedChoiceField(
        label="Mes", required=False, coerce=int, empty_value=None,
        choices=[("", "Todos los meses del libro")] + MESES,
    )
    reemplazar = forms.BooleanField(
        label="Reemplazar los meses que ya existen", required=False, initial=True,
        help_text="Desmárcalo para no tocar los meses que ya tienen datos en la app "
                  "(por ejemplo, si los has corregido a mano).",
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if not archivo.name.lower().endswith((".xlsx", ".xlsm", ".xls", ".ods")):
            raise forms.ValidationError(f"'{archivo.name}' no es un Excel (.xlsx).")
        if archivo.size == 0:
            raise forms.ValidationError("El archivo está vacío.")
        return archivo
