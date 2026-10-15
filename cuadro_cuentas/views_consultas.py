"""Hojas de trabajo, extractos de cuentas y buscador de apuntes."""
from datetime import date

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .consultas import buscar_apuntes, extracto, sumas_y_saldos
from .forms_consultas import BuscadorForm, HojaForm
from .models import Asiento, CuentaContable, Ejercicio, Hoja

LIMITE_RESULTADOS = 300


def _ejercicio_elegido(request):
    ejercicios = Ejercicio.objects.all()
    anio = request.GET.get("ejercicio")
    return ejercicios, (ejercicios.filter(anio=anio).first() if anio else ejercicios.first())


def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


# ===================== Hojas =====================

def hojas(request):
    """Lista de hojas: crear, cambiar nombre/color y borrar. Borrar una hoja no borra sus
    asientos: se quedan «sin hoja»."""
    url = reverse("cuadro_cuentas:hojas")
    nueva = HojaForm(prefix="nueva")

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "crear":
            nueva = HojaForm(request.POST, prefix="nueva")
            if nueva.is_valid():
                hoja = nueva.save()
                messages.success(request, f"Hoja «{hoja}» creada.")
                return redirect(url)
        elif accion in ("editar", "borrar"):
            hoja = get_object_or_404(Hoja, pk=request.POST.get("id"))
            if accion == "borrar":
                n = hoja.asientos.count()
                hoja.delete()
                messages.success(request, f"Hoja «{hoja}» borrada. Sus {n} asientos quedan sin hoja.")
                return redirect(url)
            form = HojaForm(request.POST, instance=hoja, prefix=f"h{hoja.id}")
            if form.is_valid():
                form.save()
                messages.success(request, f"Hoja «{hoja}» guardada.")
                return redirect(url)
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
            return redirect(url)

    lista = Hoja.objects.annotate(n=Count("asientos"))
    return render(request, "cuadro_cuentas/hojas.html", {
        "nueva": nueva,
        "hojas": [(h, HojaForm(instance=h, prefix=f"h{h.id}")) for h in lista],
        "sin_hoja": Asiento.objects.filter(hoja__isnull=True).count(),
    })


# ===================== Extractos de cuentas =====================

def extractos(request):
    """Sin cuenta: sumas y saldos de todas las subcuentas (filtrables por prefijo).
    Con cuenta: su extracto con saldo acumulado línea a línea."""
    ejercicios, ejercicio = _ejercicio_elegido(request)
    cuenta = request.GET.get("cuenta", "").strip()
    filtros = {
        "solo_contabilizados": request.GET.get("solo_contabilizados") == "1",
        "incluir_cierre": request.GET.get("incluir_cierre") == "1",
    }
    contexto = {"ejercicios": ejercicios, "ejercicio": ejercicio, "cuenta": cuenta, **filtros,
                "cuentas": CuentaContable.objects.only("codigo", "nombre")}

    if ejercicio:
        if cuenta and cuenta.isdigit():
            desde, hasta = _fecha(request.GET.get("desde")), _fecha(request.GET.get("hasta"))
            contexto.update({
                "extracto": extracto(ejercicio, cuenta, desde, hasta, **filtros),
                "desde": desde, "hasta": hasta,
            })
        else:
            prefijo = request.GET.get("prefijo", "").strip()
            filas, totales = sumas_y_saldos(ejercicio, prefijo if prefijo.isdigit() else "", **filtros)
            contexto.update({"filas": filas, "totales": totales, "prefijo": prefijo})
    return render(request, "cuadro_cuentas/extractos.html", contexto)


# ===================== Buscador de apuntes =====================

def buscador(request):
    form = BuscadorForm(request.GET or None)
    contexto = {"form": form, "resultados": None,
                "cuentas": CuentaContable.objects.only("codigo", "nombre")}
    if form.is_bound and form.is_valid() and form.tiene_criterios():
        resultados = buscar_apuntes(form.cleaned_data)
        total = resultados.count()
        contexto.update({"resultados": resultados[:LIMITE_RESULTADOS], "total": total,
                         "limite": LIMITE_RESULTADOS})
    return render(request, "cuadro_cuentas/buscador.html", contexto)
