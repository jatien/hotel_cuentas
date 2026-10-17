"""
Minimización de datos antes de enviar texto a un proveedor externo.

Se ocultan datos que no hacen falta para extraer la factura: IBAN y
cuentas, números de tarjeta (con dígito de Luhn) y correos electrónicos.
Los NIF y la dirección del proveedor SÍ se envían (son campos a extraer).
Las imágenes enviadas para visión no se pueden redactar: por eso solo se
envían cuando el texto no basta y ALLOW_VISION lo permite.
"""

import re

IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group())
    return "[TARJETA]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else match.group()


def redact(text: str) -> str:
    text = IBAN.sub("[IBAN]", text)
    text = CARD.sub(_card, text)
    return EMAIL.sub("[EMAIL]", text)
