import calendar
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import DiasFormSet, ImportarCajasForm, NotasMesForm
from .importacion import ErrorImportacion, importar_libro
from .models import CAMPOS_IMPORTE, MESES, MESES_NOMBRES, OTROS_COBROS, PUNTOS_VENTA, CajaDia, CajaMes
from .resumen import anios_disponibles, resumen_anual, totales_dias


def _anio_param(request, anios):
    try:
        anio = int(request.GET.get("anio", ""))
        if 2000 <= anio <= 2100:
            return anio
    except ValueError:
        pass
    return anios[0] if anios else date.today().year


def resumen(request):
    anios = anios_disponibles()
    anio = _anio_param(request, anios)
    if anio not in anios:
        anios = sorted(set(anios) | {anio}, reverse=True)
    r = resumen_anual(anio)

    # Datos para Chart.js (por json_script, nunca interpolados en el JS)
    grafico = {
        "meses": [f["nombre"] for f in r["filas"]],
        "series": [
            {"etiqueta": etiqueta, "valores": [round(float(f[campo]), 2) for f in r["filas"]]}
            for campo, etiqueta in PUNTOS_VENTA
        ],
    }
    return render(request, "cajas/resumen.html", {
        "anio": anio, "anios": anios, "r": r, "grafico": grafico,
        "puntos_venta": PUNTOS_VENTA, "otros_cobros": OTROS_COBROS,
    })


def detalle(request, anio, mes):
    caja_mes = get_object_or_404(CajaMes, anio=anio, mes=mes)
    dias = list(caja_mes.dias.all())
    return render(request, "cajas/detalle.html", {
        "caja_mes": caja_mes, "dias": dias, "total": totales_dias(dias),
        "puntos_venta": PUNTOS_VENTA, "otros_cobros": OTROS_COBROS,
        "anterior": CajaMes.objects.filter(anio__lte=anio).exclude(anio=anio, mes__gte=mes)
                                   .order_by("-anio", "-mes").first(),
        "siguiente": CajaMes.objects.filter(anio__gte=anio).exclude(anio=anio, mes__lte=mes)
                                    .order_by("anio", "mes").first(),
    })


def meses(request):
    """Año + los 12 meses; el mes elegido muestra su tabla para rellenarla o corregirla.

    Un mes que aún no existe no se crea hasta que se guarda la tabla.
    """
    anios = anios_disponibles()
    anio = _anio_param(request, anios)
    anios = sorted(set(anios) | {anio, date.today().year}, reverse=True)
    try:
        mes = int(request.GET.get("mes", ""))
        mes = mes if 1 <= mes <= 12 else None
    except ValueError:
        mes = None

    totales = {f["mes"]: f["total_ventas"] for f in resumen_anual(anio)["filas"]}
    tarjetas = [{"mes": n, "nombre": nombre, "existe": n in totales, "total": totales.get(n)}
                for n, nombre in MESES]

    contexto = {"anio": anio, "anios": anios, "mes": mes, "tarjetas": tarjetas,
                "puntos_venta": PUNTOS_VENTA, "otros_cobros": OTROS_COBROS}
    if not mes:
        return render(request, "cajas/meses.html", contexto)

    caja_mes = CajaMes.objects.filter(anio=anio, mes=mes).first()
    num_dias = calendar.monthrange(anio, mes)[1]
    existentes = {d.dia: d for d in caja_mes.dias.all()} if caja_mes else {}
    inicial = [{c: getattr(existentes[d], c) if d in existentes else None for c in CAMPOS_IMPORTE}
               for d in range(1, num_dias + 1)]

    if request.method == "POST":
        formset = DiasFormSet(request.POST, initial=inicial)
        form_notas = NotasMesForm(request.POST, instance=caja_mes or CajaMes(anio=anio, mes=mes))
        if formset.is_valid() and form_notas.is_valid() and len(formset.forms) == num_dias:
            with transaction.atomic():
                caja_mes = form_notas.save()
                for dia, form in enumerate(formset.forms, start=1):
                    valores = {c: form.cleaned_data.get(c) or Decimal("0") for c in CAMPOS_IMPORTE}
                    CajaDia.objects.update_or_create(caja_mes=caja_mes, dia=dia, defaults=valores)
            messages.success(request, f"{caja_mes}: cambios guardados.")
            return redirect(f"{reverse('cajas:meses')}?anio={anio}&mes={mes}")
        messages.error(request, "Hay importes no válidos; revisa las casillas marcadas en rojo.")
    else:
        formset = DiasFormSet(initial=inicial)
        form_notas = NotasMesForm(instance=caja_mes)

    contexto.update(caja_mes=caja_mes, nombre_mes=MESES_NOMBRES[mes], formset=formset, form_notas=form_notas)
    return render(request, "cajas/meses.html", contexto)


@require_POST
def borrar(request, anio, mes):
    caja_mes = get_object_or_404(CajaMes, anio=anio, mes=mes)
    nombre = str(caja_mes)
    caja_mes.delete()
    messages.success(request, f"{nombre} borrado.")
    return redirect(f"{reverse('cajas:resumen')}?anio={anio}")


def importar(request):
    resultados = None
    if request.method == "POST":
        form = ImportarCajasForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = form.cleaned_data["archivo"]
            try:
                resultados = importar_libro(
                    archivo, archivo.name,
                    anio=form.cleaned_data.get("anio"),
                    mes=form.cleaned_data.get("mes"),
                    reemplazar=form.cleaned_data.get("reemplazar", False),
                )
            except ErrorImportacion as e:
                messages.error(request, str(e))
            except Exception as e:  # red de seguridad para lo no previsto
                messages.error(request, f"Error inesperado al importar: {e}")
            else:
                for res in resultados:
                    res["nombre_mes"] = MESES_NOMBRES[res["mes"]]
                importados = [r for r in resultados if r["estado"] == "importado"]
                if importados:
                    messages.success(request, f"{len(importados)} mes(es) importado(s).")
                # Si solo hay un mes importado y sin avisos, directo a su detalle
                if len(resultados) == 1 and importados and not importados[0]["avisos"]:
                    return redirect("cajas:detalle", anio=importados[0]["anio"], mes=importados[0]["mes"])
    else:
        form = ImportarCajasForm()
    return render(request, "cajas/importar.html", {"form": form, "resultados": resultados})
