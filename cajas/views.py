from datetime import date

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import CajaDiaFormSet, ImportarCajasForm, NotasMesForm, NuevoMesForm
from .importacion import ErrorImportacion, importar_libro
from .models import MESES_NOMBRES, OTROS_COBROS, PUNTOS_VENTA, CajaMes
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


def editar(request, anio, mes):
    caja_mes = get_object_or_404(CajaMes, anio=anio, mes=mes)
    caja_mes.crear_dias()   # por si el mes se creó incompleto
    queryset = caja_mes.dias.all()

    if request.method == "POST":
        formset = CajaDiaFormSet(request.POST, queryset=queryset)
        form_notas = NotasMesForm(request.POST, instance=caja_mes)
        if formset.is_valid() and form_notas.is_valid():
            with transaction.atomic():
                formset.save()
                form_notas.save()
            messages.success(request, f"{caja_mes}: cambios guardados.")
            return redirect("cajas:detalle", anio=anio, mes=mes)
        messages.error(request, "Hay importes no válidos; revisa las casillas marcadas en rojo.")
    else:
        formset = CajaDiaFormSet(queryset=queryset)
        form_notas = NotasMesForm(instance=caja_mes)

    return render(request, "cajas/editar.html", {
        "caja_mes": caja_mes, "formset": formset, "form_notas": form_notas,
        "puntos_venta": PUNTOS_VENTA, "otros_cobros": OTROS_COBROS,
    })


def nuevo(request):
    if request.method == "POST":
        form = NuevoMesForm(request.POST)
        if form.is_valid():
            anio, mes = form.cleaned_data["anio"], form.cleaned_data["mes"]
            caja_mes, creado = CajaMes.objects.get_or_create(anio=anio, mes=mes)
            if creado:
                caja_mes.crear_dias()
                messages.success(request, f"{caja_mes} creado. Rellena las cajas de cada día.")
                return redirect("cajas:editar", anio=anio, mes=mes)
            messages.info(request, f"{caja_mes} ya existía.")
            return redirect("cajas:detalle", anio=anio, mes=mes)
    else:
        form = NuevoMesForm()
    return render(request, "cajas/nuevo.html", {"form": form})


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
