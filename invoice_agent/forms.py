"""Formularios de las pantallas. Solo validan formato; los cambios pasan por services/review.py."""

from decimal import Decimal

from django import forms
from django.forms import formset_factory

from invoice_agent.models import TipoImpuesto
from invoice_agent.services.normalize import parse_amount


class ImporteField(forms.CharField):
    """Importe en formato español o con punto (1.234,56 / 1234.56). Vacío = sin valor."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal", "class": "num"}))
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if isinstance(value, Decimal):
            # 8.000000 -> "8,00"; 2.5000 -> "2,50"; 0.125 -> "0,125" (sin ceros sobrantes, mínimo 2 decimales)
            trimmed = value.normalize()
            if trimmed.as_tuple().exponent > -2:
                trimmed = trimmed.quantize(Decimal("0.01"))
            return f"{trimmed:f}".replace(".", ",")
        return value

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return None
        amount = parse_amount(value)
        if amount is None:
            raise forms.ValidationError("Importe no válido.")
        return amount


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(item, initial) for item in data]
        return [single(data, initial)]


class SubidaForm(forms.Form):
    empresa = forms.ModelChoiceField(queryset=None, empty_label=None, label="Empresa que recibe la factura")
    ficheros = MultipleFileField(label="Facturas (PDF o imagen)")

    def __init__(self, *args, empresas=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = empresas


class BorradorForm(forms.Form):
    proveedor_nombre = forms.CharField(label="Proveedor", max_length=255, required=False)
    proveedor_nif = forms.CharField(label="NIF proveedor", max_length=40, required=False)
    proveedor_direccion = forms.CharField(label="Dirección", max_length=500, required=False)
    proveedor_pais = forms.CharField(label="País (ISO)", max_length=2, required=False)
    numero_factura = forms.CharField(label="Nº factura", max_length=100, required=False)
    fecha_factura = forms.DateField(label="Fecha", required=False, input_formats=["%d/%m/%Y", "%Y-%m-%d"],
                                    widget=forms.DateInput(format="%d/%m/%Y"))
    fecha_vencimiento = forms.DateField(label="Vencimiento", required=False, input_formats=["%d/%m/%Y", "%Y-%m-%d"],
                                        widget=forms.DateInput(format="%d/%m/%Y"))
    moneda = forms.CharField(label="Moneda", max_length=3, required=False)
    referencia_pedido = forms.CharField(label="Ref. pedido", max_length=100, required=False)
    subtotal_neto = ImporteField(label="Subtotal neto")
    descuentos = ImporteField(label="Descuentos")
    cargos = ImporteField(label="Cargos adicionales")
    total_impuestos = ImporteField(label="Total impuestos")
    total_retenciones = ImporteField(label="Retenciones")
    total_bruto = ImporteField(label="Total factura")
    importe_a_pagar = ImporteField(label="Importe a pagar")
    comentario = forms.CharField(label="Comentario (auditoría)", required=False, max_length=2000,
                                 widget=forms.TextInput())


class LineaForm(forms.Form):
    descripcion = forms.CharField(max_length=500, required=False)
    cantidad = ImporteField()
    precio_unitario = ImporteField()
    descuento = ImporteField()
    tipo_impositivo = ImporteField()
    subtotal = ImporteField()
    total = ImporteField()

    def is_blank(self) -> bool:
        data = getattr(self, "cleaned_data", {}) or {}
        return not any(v not in (None, "") for v in data.values())


class ImpuestoForm(forms.Form):
    tipo = forms.ChoiceField(choices=TipoImpuesto.choices)
    porcentaje = ImporteField()
    base = ImporteField()
    cuota = ImporteField()

    def is_blank(self) -> bool:
        data = getattr(self, "cleaned_data", {}) or {}
        return all(data.get(k) is None for k in ("porcentaje", "base", "cuota"))


LineaFormSet = formset_factory(LineaForm, extra=1, max_num=500)
ImpuestoFormSet = formset_factory(ImpuestoForm, extra=1, max_num=50)


class DecisionForm(forms.Form):
    comentario = forms.CharField(required=False, max_length=2000, widget=forms.TextInput())
