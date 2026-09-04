# nominas/services.py
import csv, io, re, os
from decimal import Decimal
from django.db import transaction
from .models import Empleado, NominaMensual

CAMPOS = {
    'DEVENG': 'deveng', 'BASE DIN': 'base_din', 'BASE ESP': 'base_esp',
    'IRPF ESP': 'irpf_esp', 'IRPF DIN': 'irpf_din', 'EMBARG': 'embarg',
    'PRIMA': 'prima', 'MANUT': 'manut', 'ENF/ACC': 'enf_acc', 'BONIF': 'bonif',
    'S.NETO': 's_neto', 'SS TRABAJ': 'ss_trabaj', 'SS EMPR': 'ss_empr',
    'RLC': 'rlc', 'COST TOT': 'cost_tot',
}

def parse_decimal(value):
    if value in (None, '', ' '):
        return Decimal('0')
    return Decimal(str(value).strip().replace('.', '').replace(',', '.'))


def importar_nomina_mensual(file_obj, anio=None, mes=None):
    """file_obj puede ser un archivo abierto en 'rb' o un UploadedFile de Django — ambos tienen .name y .read()."""
    if not anio or not mes:
        match = re.search(r'(\d{2})_(\d{4})', os.path.basename(file_obj.name))
        if not match:
            raise ValueError('No se pudo deducir mes/año del nombre del archivo; indícalos manualmente')
        mes, anio = int(match.group(1)), int(match.group(2))

    contenido = file_obj.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(contenido))

    count = 0
    with transaction.atomic():
        for row in reader:
            nif = row['NIF'].strip()
            empleado, _ = Empleado.objects.update_or_create(
                nif=nif, defaults={'nombre': row['Nombre trabajador'].strip()}
            )
            valores = {campo: parse_decimal(row[col]) for col, campo in CAMPOS.items()}
            NominaMensual.objects.update_or_create(
                empleado=empleado, anio=anio, mes=mes,
                defaults={'departamento': row['Departamento'].strip(), **valores}
            )
            count += 1

    return count, anio, mes