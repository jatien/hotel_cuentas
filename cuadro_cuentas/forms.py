from django import forms

from .nif import validar_nif
from .tipos_alta import TIPOS_ALTA, TIPOS_POR_CLAVE


class AltaSubcuentaForm(forms.Form):
    tipo = forms.ChoiceField(
        label="¿Qué quieres dar de alta?",
        choices=[(t["clave"], t["etiqueta"]) for t in TIPOS_ALTA],
        initial="proveedor",
        widget=forms.RadioSelect,
    )
    base = forms.CharField(label="Cuenta del plan", max_length=5)
    nombre = forms.CharField(max_length=200)
    nif = forms.CharField(label="NIF/CIF", max_length=20, required=False)
    codigo = forms.CharField(
        label="Código", max_length=8, required=False,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )

    def clean_nif(self):
        return validar_nif(self.cleaned_data.get("nif"))

    def clean(self):
        datos = super().clean()
        tipo = TIPOS_POR_CLAVE.get(datos.get("tipo"))
        if not tipo:
            return datos
        if tipo["bases"] is not None and datos.get("base") not in tipo["bases"]:
            self.add_error("base", f"Para «{tipo['etiqueta']}» elige una de las cuentas propuestas.")
        if tipo["nif"] == "obligatorio" and not datos.get("nif") and "nif" not in self.errors:
            self.add_error("nif", f"El NIF/CIF es obligatorio para «{tipo['etiqueta']}».")
        return datos