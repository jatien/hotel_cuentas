"""
Forms for Gestión de Facturas.

    InvoiceUploadForm   the drop-zone page: kind of entity, department, extract now?
    DatosFacturaForm    correcting the extracted header/totals on the review screen
    FiltroFacturasForm  filters of the invoice list
"""

from django import forms

from invoice_data.models import ExtractedInvoiceData, TipoEntidad

DEPARTAMENTO_CHOICES = [
    ("cocina", "Cocina"),
    ("recepcion", "Recepción"),
    ("comedor", "Comedor"),
    ("mantenimiento", "Mantenimiento"),
    ("lavanderia", "Lavandería"),
    ("bar", "Bar"),
]


class InvoiceUploadForm(forms.Form):
    """
    Upload options. `departamento` is only required for suppliers (a
    creditor has none), which a plain required=True cannot express, so it
    is enforced in clean().
    """

    tipo_entidad = forms.ChoiceField(
        choices=TipoEntidad.choices, label="Tipo", widget=forms.RadioSelect, initial=TipoEntidad.PROVEEDOR
    )
    departamento = forms.ChoiceField(choices=DEPARTAMENTO_CHOICES, label="Departamento", required=False)
    extraer = forms.BooleanField(label="Extraer los datos al importar", required=False, initial=True)

    def clean(self):
        """A supplier needs a department; a creditor does not."""
        cleaned = super().clean()
        if cleaned.get("tipo_entidad") == TipoEntidad.PROVEEDOR and not cleaned.get("departamento"):
            self.add_error("departamento", "Selecciona un departamento para un proveedor.")
        return cleaned


class DatosFacturaForm(forms.ModelForm):
    """Header and totals of an extraction, editable so a person can correct them before confirming."""

    class Meta:
        model = ExtractedInvoiceData
        fields = [
            "numero_factura", "fecha_factura", "proveedor_nombre", "proveedor_cif",
            "base_imponible", "iva_total", "total",
        ]
        labels = {
            "numero_factura": "Nº de factura",
            "fecha_factura": "Fecha",
            "proveedor_nombre": "Proveedor",
            "proveedor_cif": "CIF / NIF",
            "base_imponible": "Total sin IVA",
            "iva_total": "IVA",
            "total": "Total con IVA",
        }
        widgets = {
            "fecha_factura": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "base_imponible": forms.NumberInput(attrs={"step": "0.01"}),
            "iva_total": forms.NumberInput(attrs={"step": "0.01"}),
            "total": forms.NumberInput(attrs={"step": "0.01"}),
        }


class FiltroFacturasForm(forms.Form):
    """Filters of the invoice list (all optional, sent by GET so the URL can be shared)."""

    q = forms.CharField(label="Buscar", required=False)
    estado = forms.ChoiceField(
        label="Estado",
        required=False,
        choices=[("", "Todos"), ("pendiente", "Sin extraer"), ("ok", "Correctas"), ("revisar", "A revisar")],
    )
    tipo_entidad = forms.ChoiceField(
        label="Tipo", required=False, choices=[("", "Todos")] + list(TipoEntidad.choices)
    )
    departamento = forms.ChoiceField(
        label="Departamento", required=False, choices=[("", "Todos")] + DEPARTAMENTO_CHOICES
    )
    confirmada = forms.ChoiceField(
        label="Confirmación",
        required=False,
        choices=[("", "Todas"), ("si", "Confirmadas"), ("no", "Sin confirmar")],
    )
