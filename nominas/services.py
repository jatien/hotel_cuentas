import csv, io, re, os
from decimal import Decimal
from django.db import transaction
from .models import Empleado, NominaMensual

# Cada campo del modelo puede llamarse distinto según la exportación —
# aquí van todos los alias conocidos. Añadir uno nuevo es solo añadir un string a la lista.
CAMPOS_ALIAS = {
    'deveng': ['DEVENG'],
    'base_din': ['BASE DIN'],
    'base_esp': ['BASE ESP'],
    'irpf_esp': ['IRPF ESP'],
    'irpf_din': ['IRPF DIN'],
    'embarg': ['EMBARG'],
    'prima': ['PRIMA'],
    'manut': ['MANUT'],
    'enf_acc': ['ENF/ACC', 'ENF ACC'],
    'bonif': ['BONIF'],
    's_neto': ['S.NETO', 'S NETO', 'SNETO'],
    'ss_trabaj': ['SS TRABAJ'],
    'ss_empr': ['SS EMPR'],
    'rlc': ['RLC'],
    'cost_tot': ['COST TOT'],
}
ALIAS_NOMBRE = ['NOMBRE', 'NOMBRE TRABAJADOR']
ALIAS_NIF = ['NIF']
ALIAS_DEPARTAMENTO = ['DEPARTAMENTO']
# Todo lo demás (Profesión, Género, Trabajador, FECHA ALTA/BAJA, MOTIVO BAJA...)
# simplemente no coincide con ningún alias de arriba -> se ignora automáticamente.


class ImportacionInvalida(Exception):
    """Error esperado y legible por el usuario — nunca debe llegar como página de error de Django."""
    pass


def parse_decimal(value):
    value = (value or '').strip()
    if not value:
        return Decimal('0')
    try:
        return Decimal(value.replace('.', '').replace(',', '.'))
    except Exception:
        raise ImportacionInvalida(f"Valor numérico inválido: '{value}'")


def normalizar(texto):
    return (texto or '').strip().upper()


def detectar_mapeo_columnas(cabecera):
    """Para cada columna de la cabecera, decide a qué campo del modelo corresponde
    (si corresponde a alguno). Las que no reconoce, las ignora sin más."""
    indices_campos = {}
    idx_nombre = idx_nif = idx_departamento = None

    for i, celda in enumerate(cabecera):
        celda_norm = normalizar(celda)
        if not celda_norm:
            continue
        if celda_norm in ALIAS_NOMBRE:
            idx_nombre = i
            continue
        if celda_norm in ALIAS_NIF:
            idx_nif = i
            continue
        if celda_norm in ALIAS_DEPARTAMENTO:
            idx_departamento = i
            continue
        for campo, alias in CAMPOS_ALIAS.items():
            if celda_norm in alias:
                indices_campos[i] = campo
                break
        # si no coincide con nada -> se ignora (Profesión, Género, Trabajador, fechas...)

    return indices_campos, idx_nombre, idx_nif, idx_departamento


def encontrar_fila_cabecera(filas):
    """Los exports reales traen líneas de título/empresa antes de la cabecera real.
    Buscamos la primera fila que contenga la columna NIF -> esa es la cabecera de verdad."""
    for i, fila in enumerate(filas):
        if any(normalizar(c) in ALIAS_NIF for c in fila):
            return i
    return None


def importar_nomina_mensual(file_obj, anio=None, mes=None):
    nombre_archivo = file_obj.name
    if not nombre_archivo.lower().endswith('.csv'):
        raise ImportacionInvalida(
            f"El archivo '{nombre_archivo}' no es un .csv. Si viene de Excel, expórtalo como CSV (delimitado por comas) antes de subirlo."
        )

    if not anio or not mes:
        match = re.search(r'(\d{2})_(\d{4})', os.path.basename(nombre_archivo))
        if not match:
            raise ImportacionInvalida('No se pudo deducir mes/año del nombre del archivo; indícalos manualmente.')
        mes, anio = int(match.group(1)), int(match.group(2))

    try:
        contenido = file_obj.read().decode('utf-8')
    except UnicodeDecodeError:
        raise ImportacionInvalida(
            'No se pudo leer el archivo como texto — probablemente no es un CSV real (¿es un .xlsx renombrado?).'
        )

    todas_filas = list(csv.reader(io.StringIO(contenido)))
    idx_cabecera = encontrar_fila_cabecera(todas_filas)
    if idx_cabecera is None:
        raise ImportacionInvalida('No se encontró una columna NIF en el archivo — no parece un CSV de nóminas válido.')

    cabecera = todas_filas[idx_cabecera]
    indices_campos, idx_nombre, idx_nif, idx_departamento = detectar_mapeo_columnas(cabecera)

    if idx_nombre is None or idx_nif is None:
        raise ImportacionInvalida('El archivo no tiene una columna de Nombre o de NIF reconocible.')

    creadas, actualizadas, sin_departamento = 0, 0, []
    with transaction.atomic():
        for i, fila in enumerate(todas_filas[idx_cabecera + 1:], start=idx_cabecera + 2):
            if len(fila) <= idx_nif or not fila[idx_nif].strip():
                continue  # fila vacía o de relleno

            nombre = fila[idx_nombre].strip()
            nif = fila[idx_nif].strip()
            if not nombre or not nif:
                continue

            departamento = ''
            if idx_departamento is not None and len(fila) > idx_departamento:
                departamento = fila[idx_departamento].strip()

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
                    sin_departamento.append(nombre)

            valores = {}
            for idx, campo in indices_campos.items():
                valor_celda = fila[idx] if idx < len(fila) else ''
                valores[campo] = parse_decimal(valor_celda)
            for campo in CAMPOS_ALIAS:  # si este CSV no traía alguna columna -> 0 por defecto
                valores.setdefault(campo, Decimal('0'))
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