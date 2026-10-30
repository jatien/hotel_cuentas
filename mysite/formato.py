"""Formato español de números para las plantillas (1.234.567,89).

Compartido por los filtros de `analisis` y `cajas`, que antes tenían cada uno su copia.
"""


def numero_es(numero: float, decimales: int) -> str:
    """1234567.8 -> «1.234.567,8» (separador de miles punto, decimal coma)."""
    texto = f"{numero:,.{decimales}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
