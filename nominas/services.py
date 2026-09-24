import csv, io, re, os
from decimal import Decimal
from django.db import transaction
from .models import Empleado, NominaMensual
'''
Usamos este archivo ( services.py ) para importar los datos 
del archivo csv que nos da el banco (esto se puede mejorar, generalizando).
Esto se añade como input en el html.
'''



CAMPOS = {
    'DEVENG': 'deveng', 'BASE DIN': 'base_din', 'BASE ESP': 'base_esp',
    'IRPF ESP': 'irpf_esp', 'IRPF DIN': 'irpf_din', 'EMBARG': 'embarg',
    'PRIMA': 'prima', 'MANUT': 'manut', 'ENF/ACC': 'enf_acc', 'BONIF': 'bonif',
    'S.NETO': 's_neto', 'SS TRABAJ': 'ss_trabaj', 'SS EMPR': 'ss_empr',
    'RLC': 'rlc', 'COST TOT': 'cost_tot',
}


def parse_decimal(value):
    value = (value or '').strip()
    if not value:
        return Decimal('0')
    return Decimal(value.replace('.', '').replace(',', '.'))


def importar_nomina_mensual(file_obj, anio=None, mes=None):
    if not anio or not mes:
        match = re.search(r'(\d{2})_(\d{4})', os.path.basename(file_obj.name))
        if not match:
            raise ValueError('No se pudo deducir mes/año del nombre del archivo; indícalos manualmente')
        mes, anio = int(match.group(1)), int(match.group(2))

    contenido = file_obj.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(contenido))

    creadas, actualizadas, sin_departamento = 0, 0, []
    with transaction.atomic():
        for row in reader:
            nombre = row['NOMBRE'].strip()
            nif = row['NIF'].strip()
            departamento = (row.get('DEPARTAMENTO') or '').strip()

            empleado, _ = Empleado.objects.update_or_create(
                nif=nif, defaults={'nombre': nombre}
            )

            if not departamento:
                anterior = (NominaMensual.objects
                            .filter(empleado=empleado)
                            .exclude(anio=anio, mes=mes)
                            .order_by('-anio', '-mes')
                            .first())
                if anterior:
                    departamento = anterior.departamento
                else:
                    sin_departamento.append(nombre)  # nunca hemos visto a este empleado antes

            valores = {campo: parse_decimal(row[col]) for col, campo in CAMPOS.items()}
            valores['departamento'] = departamento

            nomina, creada = NominaMensual.objects.update_or_create(
                empleado=empleado, anio=anio, mes=mes,
                defaults=valores,
            )
            creadas += creada
            actualizadas += not creada

    return {
        'anio': anio, 'mes': mes, 'creadas': creadas, 'actualizadas': actualizadas,
        'sin_departamento': sin_departamento,
    }