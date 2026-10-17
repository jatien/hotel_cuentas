"""
Reglas fiscales por jurisdicción, separadas de la aritmética.

La validación aritmética (base × tipo = cuota, sumas, totales) es igual en
todas partes y vive en validation.py. Lo que cambia por jurisdicción es qué
tipos de impuesto y qué porcentajes son habituales; eso vive aquí, en un
registro de regímenes que se puede ampliar sin tocar la validación.

Un porcentaje desconocido es solo un AVISO: puede ser un tipo histórico,
un régimen especial o una lectura errónea, y lo decide una persona.
"""

from dataclasses import dataclass, field
from decimal import Decimal


def _rates(*values: str) -> frozenset[Decimal]:
    return frozenset(Decimal(v) for v in values)


@dataclass(frozen=True)
class TaxRegime:
    code: str
    name: str
    countries: frozenset[str]
    known_rates: dict[str, frozenset[Decimal]] = field(default_factory=dict)

    def rate_is_known(self, kind: str, rate: Decimal) -> bool | None:
        """True/False si el régimen tiene lista para ese tipo de impuesto; None si no la tiene."""
        rates = self.known_rates.get(kind)
        if rates is None:
            return None
        return any(abs(rate - known) <= Decimal("0.005") for known in rates)


SPAIN = TaxRegime(
    code="ES",
    name="España (IVA, IGIC, IPSI, recargo de equivalencia, IRPF)",
    countries=frozenset({"ES"}),
    known_rates={
        # IVA vigente (0, 4, 5, 10, 21) e históricos (7, 8, 16, 18) por facturas antiguas.
        "vat": _rates("0", "4", "5", "7", "8", "10", "16", "18", "21"),
        "igic": _rates("0", "1", "3", "5", "7", "9.5", "13.5", "15", "20"),
        "ipsi": _rates("0.5", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"),
        "surcharge": _rates("0", "0.5", "0.62", "1", "1.4", "1.75", "4", "5.2"),
        "withholding": _rates("1", "2", "7", "15", "19", "24"),
    },
)

# Resto de países: solo aritmética, sin lista de tipos (no se presupone ningún modelo de IVA).
GENERIC = TaxRegime(code="GENERIC", name="Genérico (solo comprobaciones aritméticas)", countries=frozenset())

REGIMES: list[TaxRegime] = [SPAIN]


def regime_for(supplier_country: str | None) -> TaxRegime:
    """Régimen por país del proveedor; sin país se asume el del hotel (España)."""
    country = supplier_country or "ES"
    for regime in REGIMES:
        if country in regime.countries:
            return regime
    return GENERIC
