"""Hojas de trabajo, extractos de cuentas y buscador de apuntes."""
from datetime import date

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .activo import ejercicio_activo
from .consultas import buscar_apuntes, contrapartidas_de_apuntes, extracto, sumas_y_saldos
from .exportar import exportar, formato_pedido
from .forms_consultas import BuscadorForm, HojaForm
from .forms_contabilidad import PUNTEO_FILTRO
from .models import Apunte, Asiento, CuentaContable, Hoja

LIMITE_RESULTADOS = 300


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

COLUMNAS_MAYOR = ["Fecha", "Asiento", "Apunte", "Número", "Documento", "Cuenta", "Título", "Concepto",
                  "Debe", "Haber", "Saldo", "Contrapartida", "Punteo"]


def extractos(request):
    """Libro mayor. Sin cuenta: sumas y saldos de todas las subcuentas (filtrables por prefijo).
    Con cuenta: saldo inicial, movimientos con saldo acumulado, totales y saldo final."""
    ejercicios, ejercicio = ejercicio_activo(request)
    cuenta = request.GET.get("cuenta", "").strip()
    filtros = {
        "solo_contabilizados": request.GET.get("solo_contabilizados") == "1",
        "incluir_cierre": request.GET.get("incluir_cierre") == "1",
    }
    punteo = request.GET.get("punteo", "")
    contexto = {"ejercicios": ejercicios, "ejercicio": ejercicio, "cuenta": cuenta, **filtros,
                "cuentas": CuentaContable.objects.only("codigo", "nombre"), "punteo": punteo,
                "punteos_filtro": PUNTEO_FILTRO,
                "punteos": [c for c in Apunte.Punteo.choices if c[0] in Apunte.PUNTEO_MANUAL]}

    if ejercicio:
        if cuenta and cuenta.isdigit():
            desde, hasta = _fecha(request.GET.get("desde")), _fecha(request.GET.get("hasta"))
            ext = extracto(ejercicio, cuenta, desde, hasta, punteo=punteo or None, **filtros)
            if formato := formato_pedido(request):
                filas = [[l["apunte"].asiento.fecha, l["apunte"].asiento.numero, l["apunte"].orden,
                          l["apunte"].asiento.numero_externo, l["apunte"].asiento.get_tipo_documento_display()
                          if l["apunte"].asiento.tipo_documento else "", l["apunte"].cuenta.codigo,
                          l["apunte"].cuenta.nombre, l["apunte"].concepto or l["apunte"].asiento.concepto,
                          l["apunte"].debe, l["apunte"].haber, l["saldo"],
                          l["contrapartida"].detalle, l["apunte"].get_punteo_display()] for l in ext.lineas]
                filas.insert(0, [desde or ejercicio.fecha_inicio, "", "", "", "", "", "", "Saldo inicial",
                                 "", "", ext.saldo_inicial, "", ""])
                return exportar(formato, f"mayor_{cuenta}_{ejercicio.anio}", COLUMNAS_MAYOR, filas)
            contexto.update({
                "extracto": ext,
                "desde": desde, "hasta": hasta,
                "consulta": request.GET.urlencode(),
            })
        else:
            prefijo = request.GET.get("prefijo", "").strip()
            filas, totales = sumas_y_saldos(ejercicio, prefijo if prefijo.isdigit() else "", **filtros)
            if formato := formato_pedido(request):
                return exportar(formato, f"saldos_{ejercicio.anio}", ["Cuenta", "Título", "Debe", "Haber", "Saldo"],
                                [[f["codigo"], f["nombre"], f["debe"], f["haber"], f["saldo"]] for f in filas])
            contexto.update({"filas": filas, "totales": totales, "prefijo": prefijo,
                             "consulta": request.GET.urlencode()})
    return render(request, "cuadro_cuentas/extractos.html", contexto)


# ===================== Buscador de apuntes =====================

def buscador(request):
    form = BuscadorForm(request.GET or None)
    contexto = {"form": form, "resultados": None,
                "cuentas": CuentaContable.objects.only("codigo", "nombre")}
    if form.is_bound and form.is_valid() and form.tiene_criterios():
        resultados = buscar_apuntes(form.cleaned_data)
        if formato := formato_pedido(request):
            lista = list(resultados)
            contras = contrapartidas_de_apuntes(lista)
            return exportar(formato, "apuntes", ["Ejercicio", "Asiento", "Apunte", "Fecha", "Número", "Cuenta", "Título",
                                                 "Concepto", "Debe", "Haber", "Contrapartida", "Estado", "Punteo"],
                            [[a.asiento.ejercicio.anio, a.asiento.numero, a.orden, a.asiento.fecha,
                              a.asiento.numero_externo, a.cuenta.codigo, a.cuenta.nombre,
                              a.concepto or a.asiento.concepto, a.debe, a.haber, contras[a.id].detalle,
                              a.asiento.get_estado_display(), a.get_punteo_display()] for a in lista])
        total = resultados.count()
        pagina = list(resultados[:LIMITE_RESULTADOS])
        contras = contrapartidas_de_apuntes(pagina)
        contexto.update({"resultados": [(a, contras[a.id]) for a in pagina], "total": total,
                         "limite": LIMITE_RESULTADOS, "consulta": request.GET.urlencode()})
    return render(request, "cuadro_cuentas/buscador.html", contexto)
