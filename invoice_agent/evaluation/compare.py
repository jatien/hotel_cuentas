"""
Comparación de dos informes de invoice_agent_evaluate sobre las MISMAS facturas: el modelo base frente
al afinado, los dos medidos contra la verdad (los JSON del modelo grande o los aprobados por una persona).

Solo lee y escribe informes: no toca la base de datos, ni Google Drive, ni el modelo de producción.

    compare(base, afinado) -> dict
    render_text(c)  resumen para la terminal
    render_html(c)  informe completo (una página que se abre en el navegador)
"""

import html
from datetime import datetime

from invoice_agent.schemas import HEADER_FIELDS

FIELD_LABELS = {
    "supplier_name": "Proveedor",
    "supplier_tax_id": "NIF proveedor",
    "supplier_address": "Dirección proveedor",
    "supplier_country": "País proveedor",
    "customer_tax_id": "NIF cliente",
    "invoice_number": "Nº factura",
    "invoice_date": "Fecha",
    "due_date": "Vencimiento",
    "currency": "Moneda",
    "purchase_order": "Pedido",
    "net_subtotal": "Base imponible",
    "discount_total": "Descuentos",
    "charges_total": "Cargos",
    "tax_total": "Cuota de IVA",
    "withholding_total": "Retenciones",
    "gross_total": "Total con IVA",
    "amount_due": "Importe a pagar",
}
DOCUMENT_LABELS = {
    "lines_count_accuracy": "Nº de líneas correcto",
    "taxes_exact_match": "Desglose de impuestos exacto",
    "reconciliation_accuracy": "Cuadra (o no) como la verdad",
    "review_rate": "Acaban en revisión",
}
LOWER_IS_BETTER = {"review_rate"}


def _pct(value) -> float | None:
    return None if value is None else round(value * 100, 1)


def _delta(base, new, lower_is_better=False):
    if base is None or new is None:
        return None
    d = round(new - base, 1)
    return -d if lower_is_better else d


def compare(base: dict, trained: dict, base_label: str | None = None, trained_label: str | None = None) -> dict:
    base_docs = {d["name"]: d for d in base.get("per_document", [])}
    trained_docs = {d["name"]: d for d in trained.get("per_document", [])}
    common = sorted(set(base_docs) & set(trained_docs), key=lambda n: trained_docs[n].get("title") or n)

    fields = []
    for name in HEADER_FIELDS:
        b, t = (base.get("fields") or {}).get(name), (trained.get("fields") or {}).get(name)
        if not b and not t:
            continue
        bp, tp = _pct((b or {}).get("exact_match")), _pct((t or {}).get("exact_match"))
        fields.append({"field": name, "label": FIELD_LABELS.get(name, name), "base": bp, "trained": tp,
                       "delta": _delta(bp, tp)})

    documents = []
    for key, label in DOCUMENT_LABELS.items():
        bp = _pct((base.get("documents_metrics") or {}).get(key))
        tp = _pct((trained.get("documents_metrics") or {}).get(key))
        documents.append({"metric": key, "label": label, "base": bp, "trained": tp,
                          "delta": _delta(bp, tp, key in LOWER_IS_BETTER)})

    fixed, regressed, still_wrong, perfect = [], [], [], 0
    for name in common:
        b, t = base_docs[name], trained_docs[name]
        b_wrong = set(b.get("wrong_fields") or []) if not b.get("error") else set(HEADER_FIELDS)
        t_wrong = set(t.get("wrong_fields") or []) if not t.get("error") else set(HEADER_FIELDS)
        title = t.get("title") or name
        if t.get("error"):
            still_wrong.append({"name": name, "title": title, "error": t["error"], "diffs": {}})
            continue
        if not t_wrong:
            perfect += 1
        newly_fixed = sorted(b_wrong - t_wrong) if not b.get("error") else []
        newly_broken = sorted(t_wrong - b_wrong)
        if newly_fixed:
            fixed.append({"name": name, "title": title, "fields": newly_fixed})
        if newly_broken:
            regressed.append({"name": name, "title": title, "fields": newly_broken,
                              "diffs": {f: t["diffs"].get(f) for f in newly_broken if t.get("diffs")}})
        if t_wrong:
            still_wrong.append({"name": name, "title": title, "error": None, "diffs": t.get("diffs") or {},
                                "lines": t.get("lines")})

    return {
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "base_model": base_label or base.get("model"),
        "trained_model": trained_label or trained.get("model"),
        "documents": len(common),
        "failures": {"base": base.get("failures", 0), "trained": trained.get("failures", 0)},
        "perfect": {"trained": perfect},
        "fields": fields,
        "document_metrics": documents,
        "fixed": fixed,
        "regressed": regressed,
        "disagreements": still_wrong,
        "latency_ms": {"base": (base.get("cost_latency") or {}).get("latency_ms_mean"),
                       "trained": (trained.get("cost_latency") or {}).get("latency_ms_mean")},
    }


def _fmt(value, suffix=" %") -> str:
    return "—" if value is None else f"{value:.1f}{suffix}"


def _sign(value) -> str:
    return "—" if value is None else f"{value:+.1f}"


def render_text(c: dict) -> str:
    lines = [
        f"Facturas de prueba: {c['documents']} · fallos base {c['failures']['base']} · fallos afinado {c['failures']['trained']}"
        f" · perfectas con el afinado {c['perfect']['trained']}",
        "",
        f"  {'Campo':34} {'Base':>8} {'Afinado':>9} {'Mejora':>8}",
    ]
    for row in c["fields"] + c["document_metrics"]:
        lines.append(f"  {row['label'][:34]:34} {_fmt(row['base']):>8} {_fmt(row['trained']):>9} {_sign(row['delta']):>8}")
    lines += [
        "",
        f"Mejoran con el entrenamiento: {len(c['fixed'])} facturas · empeoran: {len(c['regressed'])}"
        f" · siguen sin coincidir con la referencia: {len(c['disagreements'])}",
        "(Mejora en puntos porcentuales; en «Acaban en revisión» mejora que baje.)",
    ]
    return "\n".join(lines)


def _cell_delta(value) -> str:
    if value is None:
        return "<td class='num'>—</td>"
    cls = "up" if value > 0 else "down" if value < 0 else ""
    return f"<td class='num {cls}'>{value:+.1f}</td>"


def render_html(c: dict) -> str:
    e = html.escape

    def rows(items):
        return "".join(
            f"<tr><td>{e(r['label'])}</td><td class='num'>{_fmt(r['base'])}</td>"
            f"<td class='num'>{_fmt(r['trained'])}</td>{_cell_delta(r['delta'])}</tr>"
            for r in items
        )

    def diff_rows(items):
        out = []
        for d in items:
            if d.get("error"):
                out.append(f"<tr><td>{e(d['title'])}</td><td colspan='3' class='bad'>{e(d['error'])}</td></tr>")
                continue
            for field, values in (d.get("diffs") or {}).items():
                values = values or {}
                out.append(
                    f"<tr><td>{e(d['title'])}</td><td>{e(FIELD_LABELS.get(field, field))}</td>"
                    f"<td><code>{e(values.get('expected') or '(vacío)')}</code></td>"
                    f"<td><code>{e(values.get('predicted') or '(vacío)')}</code></td></tr>"
                )
        return "".join(out) or "<tr><td colspan='4'>Ninguna.</td></tr>"

    fixed = "".join(
        f"<li>{e(d['title'])}: {e(', '.join(FIELD_LABELS.get(f, f) for f in d['fields']))}</li>" for d in c["fixed"]
    ) or "<li>Ninguna.</li>"
    better = sum(1 for r in c["fields"] if (r["delta"] or 0) > 0)
    worse = sum(1 for r in c["fields"] if (r["delta"] or 0) < 0)
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Afinado frente a base</title>
<style>
:root {{ --bg:#fff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --up:#1a7f37; --down:#cf222e; --tint:#f6f8fa; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d;
  --up:#3fb950; --down:#f85149; --tint:#151b23; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; max-width:980px; margin:0 auto; padding:24px 16px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:32px 0 8px; }}
.muted {{ color:var(--muted); }} table {{ border-collapse:collapse; width:100%; margin:8px 0; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ background:var(--tint); font-weight:600; }} .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.up {{ color:var(--up); font-weight:600; }} .down {{ color:var(--down); font-weight:600; }} .bad {{ color:var(--down); }}
code {{ font-size:13px; }} .wrap {{ overflow-x:auto; }}
</style></head><body>
<h1>Modelo afinado frente al base</h1>
<p class="muted">{e(c['created'])} · base <code>{e(str(c['base_model']))}</code> · afinado <code>{e(str(c['trained_model']))}</code></p>
<p>Sobre {c['documents']} facturas de prueba (nunca entrenadas), comparando con los JSON de referencia (modelo grande o
aprobados): mejora en {better} campos y empeora en {worse}. Con el afinado, {c['perfect']['trained']} facturas coinciden
en todos los campos. Fallos: base {c['failures']['base']}, afinado {c['failures']['trained']}.</p>
<p class="muted">Nada de este informe se ha guardado en Drive, en la base de datos ni en el modelo de producción.</p>
<h2>Aciertos exactos por campo</h2>
<div class="wrap"><table><tr><th>Campo</th><th class="num">Base</th><th class="num">Afinado</th><th class="num">Mejora (puntos)</th></tr>
{rows(c['fields'])}</table></div>
<h2>Por factura</h2>
<div class="wrap"><table><tr><th>Medida</th><th class="num">Base</th><th class="num">Afinado</th><th class="num">Mejora (puntos)</th></tr>
{rows(c['document_metrics'])}</table></div>
<h2>Empeoran con el entrenamiento ({len(c['regressed'])})</h2>
<div class="wrap"><table><tr><th>Factura</th><th>Campo</th><th>JSON de referencia</th><th>Modelo afinado</th></tr>
{diff_rows(c['regressed'])}</table></div>
<h2>Mejoran con el entrenamiento ({len(c['fixed'])})</h2><ul>{fixed}</ul>
<h2>Todavía no coinciden con la referencia ({len(c['disagreements'])})</h2>
<p class="muted">Cada fila es una discrepancia del modelo afinado. Puede equivocarse el modelo o el JSON de referencia:
revisar estas facturas es lo que más mejora la siguiente ronda.</p>
<div class="wrap"><table><tr><th>Factura</th><th>Campo</th><th>JSON de referencia</th><th>Modelo afinado</th></tr>
{diff_rows(c['disagreements'])}</table></div>
</body></html>
"""
