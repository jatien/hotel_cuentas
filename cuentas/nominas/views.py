# nominas/views.py
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .models import NominaMensual
from .forms import ImportarNominaForm
from .services import importar_nomina_mensual
import json

MESES_NOMBRES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
    7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}

CAMPOS_EDITABLES = {'departamento', 'importe_extra'}  # lista blanca: nada más se puede tocar desde aquí


def nominas_view(request, anio=2026):
    nominas = NominaMensual.objects.filter(anio=anio).select_related('empleado').order_by('mes', 'empleado__nombre')

    datos_por_mes = {}
    totales_mes = {}
    for n in nominas:
        total = float(n.cost_tot) + float(n.importe_extra)
        datos_por_mes.setdefault(n.mes, []).append({
            'id': n.id,
            'empleado': n.empleado.nombre,
            'departamento': n.departamento,
            'deveng': float(n.deveng),
            'cost_tot': float(n.cost_tot),
            'importe_extra': float(n.importe_extra),
            'total': round(total, 2),
        })
        totales_mes[n.mes] = totales_mes.get(n.mes, 0) + total

    meses_presentes = sorted(datos_por_mes.keys())
    meses_tabs = [(m, MESES_NOMBRES[m], round(totales_mes[m], 2)) for m in meses_presentes]
    total_anual = round(sum(totales_mes.values()), 2)

    return render(request, 'nominas/nominas.html', {
        'anio': anio, 'meses_tabs': meses_tabs, 'datos_por_mes': datos_por_mes,
        'total_anual': total_anual,
    })


@require_POST
def actualizar_campo(request):
    data = json.loads(request.body)
    campo = data.get('campo')
    if campo not in CAMPOS_EDITABLES:
        return JsonResponse({'ok': False, 'error': 'Campo no editable'}, status=400)

    try:
        nomina = NominaMensual.objects.get(id=data.get('id'))
    except NominaMensual.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Nómina no encontrada'}, status=404)

    valor = data.get('valor')
    if campo == 'importe_extra':
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'error': 'Valor numérico inválido'}, status=400)

    setattr(nomina, campo, valor)
    nomina.save(update_fields=[campo])

    total = float(nomina.cost_tot) + float(nomina.importe_extra)
    return JsonResponse({'ok': True, 'total': round(total, 2)})


def importar_nomina(request):
    if request.method == 'POST':
        form = ImportarNominaForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                resultado = importar_nomina_mensual(
                    form.cleaned_data['archivo'],
                    form.cleaned_data.get('anio'),
                    form.cleaned_data.get('mes'),
                )
                messages.success(
                    request,
                    f"{MESES_NOMBRES[resultado['mes']]} {resultado['anio']}: "
                    f"{resultado['creadas']} nuevas, {resultado['actualizadas']} actualizadas."
                )
                if resultado['sin_departamento']:
                    messages.warning(request, 'Sin departamento asignado: ' + ', '.join(resultado['sin_departamento']))
                return redirect('nominas')
            except Exception as e:
                messages.error(request, f'Error al importar: {e}')
    else:
        form = ImportarNominaForm()
    return render(request, 'nominas/importar.html', {'form': form})
def analisis_view(request, anio=2026):
    mes_param = request.GET.get('mes', 'todos')
    parametro = request.GET.get('parametro', 'departamento')

    queryset = NominaMensual.objects.filter(anio=anio).select_related('empleado')
    if mes_param != 'todos':
        queryset = queryset.filter(mes=int(mes_param))

    chart_type = chart_labels = chart_data = None

    if parametro == 'departamento':
        totales = {}
        for n in queryset:
            total = float(n.cost_tot) + float(n.importe_extra)
            totales[n.departamento] = totales.get(n.departamento, 0) + total
        chart_type = 'doughnut'
        chart_labels = list(totales.keys())
        chart_data = [round(v, 2) for v in totales.values()]

    elif parametro == 'total_impositivo':
        total_impuestos, total_sin_impuestos = 0, 0
        for n in queryset:
            impuestos = float(n.irpf_esp) + float(n.irpf_din)
            total_impuestos += impuestos
            total_sin_impuestos += float(n.deveng) - impuestos
        chart_type = 'bar'
        chart_labels = ['Total sin impuestos', 'Impuestos (IRPF)']
        chart_data = [round(total_sin_impuestos, 2), round(total_impuestos, 2)]

    meses_disponibles = sorted(set(
        NominaMensual.objects.filter(anio=anio).values_list('mes', flat=True)
    ))
    meses_opciones = [(m, MESES_NOMBRES[m]) for m in meses_disponibles]

    return render(request, 'nominas/analisis.html', {
        'anio': anio,
        'meses_opciones': meses_opciones,
        'mes_seleccionado': mes_param,
        'parametro_seleccionado': parametro,
        'chart_type': chart_type,
        'chart_labels': chart_labels,
        'chart_data': chart_data,
    })