from django import forms

from .consultas import NIVELES_SUMAS, parse_importe
from .models import Adjunto, Apunte, Asiento, DatosEmpresa, Ejercicio

FECHA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = DatosEmpresa
        fields = ["codigo", "denominacion", "nif", "forma_juridica", "domicilio", "actividad", "moneda"]

    def clean_moneda(self):
        moneda = self.cleaned_data["moneda"].upper().strip()
        if len(moneda) != 3 or not moneda.isalpha():
            raise forms.ValidationError("Código ISO de tres letras, por ejemplo EUR.")
        return moneda


class EjercicioForm(forms.ModelForm):
    class Meta:
        model = Ejercicio
        fields = ["anio", "fecha_inicio", "fecha_fin"]
        widgets = {"fecha_inicio": FECHA, "fecha_fin": FECHA}


class CabeceraForm(forms.ModelForm):
    """Cabecera de un asiento en borrador."""

    class Meta:
        model = Asiento
        fields = ["fecha", "titulo", "concepto", "numero_externo", "tipo_documento", "fecha_documento"]
        widgets = {"fecha": FECHA, "fecha_documento": FECHA}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tipo_documento"].choices = [("", "Sin documento")] + list(Asiento._meta.get_field(
            "tipo_documento").choices)

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        ejercicio = self.instance.ejercicio
        if not (ejercicio.fecha_inicio <= fecha <= ejercicio.fecha_fin):
            raise forms.ValidationError(f"La fecha debe estar dentro del ejercicio {ejercicio}.")
        return fecha


class AdjuntoForm(forms.ModelForm):
    class Meta:
        widgets = {"enlace": forms.TextInput(attrs={"placeholder": "/facturas-ia/… o https://…"})}
        model = Adjunto
        fields = ["tipo", "numero", "nombre", "archivo", "enlace"]


class _ConImporte(forms.Form):
    importe = forms.CharField(required=False, widget=forms.TextInput(attrs={"inputmode": "decimal",
                                                                            "placeholder": "Ej.: 110,00"}))

    def clean_importe(self):
        try:
            return parse_importe(self.cleaned_data.get("importe"))
        except ValueError as e:
            raise forms.ValidationError(str(e))


PUNTEO_FILTRO = [("", "Todos"), ("sin", "Sin puntear")] + [
    (v, l) for v, l in Apunte.Punteo.choices if v]


class DiarioForm(_ConImporte):
    vista = forms.ChoiceField(choices=[("detalle", "Detallada"), ("resumen", "Resumida")], required=False)
    desde = forms.DateField(required=False, widget=FECHA)
    hasta = forms.DateField(required=False, widget=FECHA)
    numero = forms.IntegerField(required=False, min_value=1, label="Nº asiento")
    cuenta = forms.CharField(required=False, widget=forms.TextInput(attrs={"list": "lista-cuentas",
                                                                          "placeholder": "572 o nombre"}))
    documento = forms.CharField(required=False, label="Número / documento")
    concepto = forms.CharField(required=False)
    estado = forms.ChoiceField(required=False, choices=[("", "Todos")] + list(Asiento.Estado.choices))
    tipo = forms.ChoiceField(required=False, choices=[("", "Todos")] + list(Asiento.Tipo.choices))
    punteo = forms.ChoiceField(required=False, choices=PUNTEO_FILTRO)

    def clean(self):
        datos = super().clean()
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            self.add_error("hasta", "La fecha final es anterior a la inicial.")
        return datos


class SumasForm(forms.Form):
    desde = forms.DateField(required=False, widget=FECHA)
    hasta = forms.DateField(required=False, widget=FECHA)
    nivel = forms.TypedChoiceField(choices=NIVELES_SUMAS, coerce=int, required=False, initial=8)
    cuenta_desde = forms.CharField(required=False, label="De la cuenta",
                                   widget=forms.TextInput(attrs={"inputmode": "numeric", "placeholder": "1"}))
    cuenta_hasta = forms.CharField(required=False, label="A la cuenta",
                                   widget=forms.TextInput(attrs={"inputmode": "numeric", "placeholder": "7"}))
    sin_movimiento = forms.BooleanField(required=False, label="Incluir cuentas sin movimiento")
    apertura = forms.BooleanField(required=False, label="Incluir asiento de apertura", initial=True)
    cierre = forms.BooleanField(required=False, label="Incluir asiento de cierre")
    borradores = forms.BooleanField(required=False, label="Incluir borradores (no oficial)")

    def clean(self):
        datos = super().clean()
        for campo in ("cuenta_desde", "cuenta_hasta"):
            valor = (datos.get(campo) or "").strip()
            if valor and not valor.isdigit():
                self.add_error(campo, "Solo dígitos.")
            datos[campo] = valor
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            self.add_error("hasta", "La fecha final es anterior a la inicial.")
        return datos
