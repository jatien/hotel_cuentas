from django.shortcuts import render
from .models import NominaMensual
import json

MESES_NOMBRES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
    7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}

def nominas_view(request, anio=2026):
    nominas = NominaMensual.objects.filter(anio=anio).select_related('empleado').order_by('mes', 'empleado__nombre')

    datos_por_mes = {}
    for n in nominas:
        datos_por_mes.setdefault(n.mes, []).append({
            'empleado': n.empleado.nombre,
            'departamento': n.departamento,
            'deveng': float(n.deveng),
            'cost_tot': float(n.cost_tot),
            'importe_extra': float(n.importe_extra),
        })

    meses_presentes = sorted(datos_por_mes.keys())
    meses_tabs = [(m, MESES_NOMBRES[m]) for m in meses_presentes]

    return render(request, 'nominas/nominas.html', {
        'anio': anio,
        'meses_tabs': meses_tabs,
        'datos_por_mes': datos_por_mes,
    })