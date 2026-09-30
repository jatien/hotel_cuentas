from django import forms

EXTENSIONES_PERMITIDAS = ('.csv',)

class ImportarNominaForm(forms.Form):
    archivo = forms.FileField(label='Archivo CSV')
    anio = forms.IntegerField(required=False, label='Año (opcional si el nombre del archivo lo indica)')
    mes = forms.IntegerField(required=False, min_value=1, max_value=12, label='Mes (opcional)')

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        nombre = archivo.name.lower()
        if not nombre.endswith(EXTENSIONES_PERMITIDAS):
            raise forms.ValidationError(
                f"'{archivo.name}' no es un archivo .csv. Si viene de Excel, usa 'Guardar como' → CSV (delimitado por comas)."
            )
        if archivo.size == 0:
            raise forms.ValidationError('El archivo está vacío.')
        return archivo