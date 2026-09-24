# nominas/forms.py (añadir)
from django import forms

class ImportarNominaForm(forms.Form):
    archivo = forms.FileField(label='Archivo CSV')
    anio = forms.IntegerField(required=False, label='Año (opcional si el nombre del archivo lo indica)')
    mes = forms.IntegerField(required=False, min_value=1, max_value=12, label='Mes (opcional)')

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        if not archivo.name.lower().endswith('.csv'):
            raise forms.ValidationError('El archivo debe ser un .csv')
        return archivo