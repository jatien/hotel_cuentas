"""Utilidades compartidas por las herramientas."""

from decimal import Decimal

from django.utils import timezone

CERO = Decimal("0")
MONEDA_CONTABILIDAD = "EUR"  # cuadro_cuentas no guarda moneda: toda la contabilidad va en euros


def importe(valor) -> str:
    """Decimal con dos decimales como texto (nunca float)."""
    return str((valor or CERO).quantize(Decimal("0.01")))


def naturaleza(neto) -> str:
    """Según el convenio del libro: saldo = Debe - Haber."""
    if neto > 0:
        return "deudor"
    if neto < 0:
        return "acreedor"
    return "saldado"


def saldo_dict(debe, haber) -> dict:
    neto = (debe or CERO) - (haber or CERO)
    return {"debe": importe(debe), "haber": importe(haber), "neto": importe(neto),
            "importe": importe(abs(neto)), "naturaleza": naturaleza(neto)}


def saldo_neto(neto) -> dict:
    return {"neto": importe(neto), "importe": importe(abs(neto)), "naturaleza": naturaleza(neto)}


def consultado_en() -> str:
    return timezone.localtime().isoformat(timespec="seconds")
