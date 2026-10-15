from django import forms

from .consultas import parse_importe
from .models import Asiento, Ejercicio, Hoja


class HojaForm(forms.ModelForm):
    class Meta:
        model = Hoja
        fields = ["nombre", "descripcion", "color"]
        widgets = {
            "color": forms.RadioSelect,
            "descripcion": forms.TextInput(attrs={"placeholder": "Para qué es esta hoja (opcional)"}),
        }


class _ConImportes(forms.Form):
    """Admite importes con coma decimal y puntos de miles: 1.234,56"""
    CAMPOS_IMPORTE = ()

    def clean(self):
        datos = super().clean()
        for campo in self.CAMPOS_IMPORTE:
            try:
                datos[campo] = parse_importe(self.data.get(campo))
            except ValueError as e:
                self.add_error(campo, str(e))
        return datos


class BuscadorForm(_ConImportes):
    CAMPOS_IMPORTE = ("importe", "importe_min", "importe_max")

    importe = forms.CharField(required=False, label="Importe exacto",
                              widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Ej.: 110,00"}))
    aproximado = forms.BooleanField(required=False, label="Aproximado (±1 %)")
    importe_min = forms.CharField(required=False, label="Desde",
                                  widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Mínimo"}))
    importe_max = forms.CharField(required=False, label="Hasta",
                                  widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Máximo"}))
    lado = forms.ChoiceField(required=False, label="Lado",
                             choices=[("", "Debe o Haber"), ("debe", "Solo Debe"), ("haber", "Solo Haber")])
    texto = forms.CharField(required=False, label="Texto",
                            widget=forms.TextInput(attrs={"placeholder": "Concepto, nº de factura, nombre…"}))
    cuenta = forms.CharField(required=False, label="Cuenta",
                             widget=forms.TextInput(attrs={"placeholder": "400, 57200001 o nombre",
                                                           "list": "lista-cuentas"}))
    desde = forms.DateField(required=False, label="Fecha desde", widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(required=False, label="Fecha hasta", widget=forms.DateInput(attrs={"type": "date"}))
    ejercicio = forms.ModelChoiceField(Ejercicio.objects.all(), required=False, empty_label="Todos",
                                       to_field_name="anio")
    hoja = forms.ChoiceField(required=False)
    estado = forms.ChoiceField(required=False, choices=[("", "Todos")] + list(Asiento.Estado.choices))
    tipo = forms.ChoiceField(required=False, label="Tipo de asiento",
                             choices=[("", "Todos")] + list(Asiento.Tipo.choices))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hoja"].choices = ([("", "Todas"), ("sin", "Sin hoja")]
                                       + [(str(h.id), h.nombre) for h in Hoja.objects.all()])

    def clean(self):
        datos = super().clean()
        minimo, maximo = datos.get("importe_min"), datos.get("importe_max")
        if minimo is not None and maximo is not None and minimo > maximo:
            self.add_error("importe_max", "El máximo es menor que el mínimo.")
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            self.add_error("hasta", "La fecha final es anterior a la inicial.")
        return datos

    def tiene_criterios(self):
        return self.is_bound and any(
            v not in (None, "", False) for k, v in self.cleaned_data.items())
