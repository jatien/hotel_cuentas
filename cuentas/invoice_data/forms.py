from django import forms

from invoice_data.models import TipoEntidad

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
    Upload form fields. departamento is only required when tipo_entidad is
    proveedor - enforced in clean() below, since a plain required=True on
    the field can't express "required only conditionally".
    """

    tipo_entidad = forms.ChoiceField(
        choices=TipoEntidad.choices, label="Tipo", widget=forms.RadioSelect, initial=TipoEntidad.PROVEEDOR
    )
    departamento = forms.ChoiceField(choices=DEPARTAMENTO_CHOICES, label="Departamento", required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("tipo_entidad") == TipoEntidad.PROVEEDOR and not cleaned.get("departamento"):
            self.add_error("departamento", "Selecciona un departamento para un proveedor.")
        return cleaned