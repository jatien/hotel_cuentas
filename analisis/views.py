from datetime import date

from django.shortcuts import render

from .resumen import MESES_LARGOS, anios_disponibles, calcular_resumen


def _entero(valor, minimo, maximo):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return None
    return n if minimo <= n <= maximo else None


def _floats(serie, hasta=12):
    """Lista de floats; los meses posteriores a `hasta` van como null (Chart.js no los pinta)."""
    return [round(float(v), 2) if i < hasta else None for i, v in enumerate(serie)]


def _ultimo_mes_con_datos(m) -> int:
    """Número del último mes con algún ingreso o gasto (0 si no hay ninguno)."""
    ultimo = 0
    for i in range(12):
        if any(m[k][i] for k in ("ingresos", "gastos", "inversiones")):
            ultimo = i + 1
    return ultimo


def resumen(request):
    anios = anios_disponibles()
    anio = _entero(request.GET.get("anio"), 2000, 2100) or anios[0]
    mes = _entero(request.GET.get("mes"), 1, 12)
    incluir_previsto = request.GET.get("previsto") == "1"
    con_iva = request.GET.get("iva") == "con"

    r = calcular_resumen(anio, mes=mes, incluir_previsto=incluir_previsto, con_iva=con_iva)
    m = r["mensual"]

    # Datos para Chart.js (se pasan con json_script, nunca interpolados en el JS)
    graficos = {
        "meses": r["meses"],
        "mensual": {k: _floats(m[k], _ultimo_mes_con_datos(m)) for k in
                    ("ingresos", "proveedores", "acreedores", "nominas", "beneficio", "acumulado")},
        "departamentos": {
            "etiquetas": [d["nombre"] for d in r["departamentos"]],
            "proveedores": _floats(d["proveedores"] for d in r["departamentos"]),
            "acreedores": _floats(d["acreedores"] for d in r["departamentos"]),
            "nominas": _floats(d["nominas"] for d in r["departamentos"]),
            "ingresos": _floats(d["ingresos"] for d in r["departamentos"]),
        },
        "cuentas": {
            "etiquetas": [f'{c["cuenta"]} · {c["nombre"]}' for c in r["cuentas_gasto"]],
            "valores": _floats(c["importe"] for c in r["cuentas_gasto"]),
        },
        "canales": {
            "etiquetas": [c["nombre"] for c in r["canales"]],
            "valores": _floats(c["ingresos"] for c in r["canales"]),
        },
    }

    # Filas de la cuenta de resultados mensual: (etiqueta, clave, estilo)
    filas = [
        ("Estancias", "estancias", ""),
        ("Extras de reservas", "extras", ""),
        ("Tasas turísticas", "tasas", ""),
        ("Bar (estimado)", "bar", ""),
        ("Comedor (estimado)", "comedor", ""),
    ]
    if incluir_previsto:
        filas.append(("Reservas futuras (previsto)", "previsto", ""))
    filas += [
        ("Total ingresos", "ingresos", "subtotal"),
        ("Proveedores", "proveedores", ""),
        ("Acreedores", "acreedores", ""),
        ("Nóminas (coste empresa)", "coste_nomina", ""),
        ("Extras de personal", "extras_personal", ""),
        ("Total gastos", "gastos", "subtotal"),
        ("Beneficio", "beneficio", "total"),
        ("Beneficio acumulado", "acumulado", "nota"),
        ("Inversiones (no restan)", "inversiones", "nota"),
        ("IVA repercutido", "iva_repercutido", "nota separador"),
        ("IVA soportado", "iva_soportado", "nota"),
        ("IVA a ingresar", "iva_liquidar", "nota"),
    ]
    tabla_mensual = []
    for etiqueta, clave, estilo in filas:
        total = r["totales"].get(clave) if clave != "acumulado" else m["acumulado"][-1]
        tabla_mensual.append({"etiqueta": etiqueta, "valores": m[clave], "total": total, "estilo": estilo})

    return render(request, "analisis/resumen.html", {
        **r,
        "anios": anios,
        "meses_opciones": list(enumerate(MESES_LARGOS, start=1)),
        "nombre_periodo": MESES_LARGOS[mes - 1] if mes else f"Año {anio}",
        "graficos": graficos,
        "tabla_mensual": tabla_mensual,
        "ano_en_curso": anio == date.today().year,
        "texto_iva": "con IVA" if con_iva else "sin IVA",
    })
