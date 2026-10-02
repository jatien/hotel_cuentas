"""Validación de NIF, NIE y CIF españoles, y NIF-IVA de otros países de la UE."""
import re

from django.core.exceptions import ValidationError

LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
LETRAS_CONTROL_CIF = "JABCDEFGHI"


def normalizar_nif(valor):
    nif = re.sub(r"[\s.\-/]", "", valor or "").upper()
    if nif.startswith("ES") and len(nif) > 9:  # NIF-IVA español: ESB12345678
        nif = nif[2:]
    return nif


def _control_cif(siete_digitos):
    pares = sum(int(d) for d in siete_digitos[1::2])
    impares = sum(sum(divmod(int(d) * 2, 10)) for d in siete_digitos[0::2])
    return (10 - (pares + impares) % 10) % 10


def validar_nif(valor):
    """Devuelve el NIF normalizado o lanza ValidationError. Cadena vacía si no hay valor."""
    nif = normalizar_nif(valor)
    if not nif:
        return ""

    if re.fullmatch(r"\d{8}[A-Z]", nif):  # DNI
        if LETRAS_DNI[int(nif[:8]) % 23] != nif[8]:
            raise ValidationError(f"La letra del NIF {nif} no es correcta.")
        return nif

    if re.fullmatch(r"[XYZ]\d{7}[A-Z]", nif):  # NIE
        numero = int(str("XYZ".index(nif[0])) + nif[1:8])
        if LETRAS_DNI[numero % 23] != nif[8]:
            raise ValidationError(f"La letra del NIE {nif} no es correcta.")
        return nif

    if re.fullmatch(r"[ABCDEFGHJKLMNPQRSUVW]\d{7}[0-9A-J]", nif):  # CIF y NIF especiales
        c = _control_cif(nif[1:8])
        letra, digito = LETRAS_CONTROL_CIF[c], str(c)
        if nif[0] in "KLMNPQRSW":
            validos = {letra}
        elif nif[0] in "ABEH":
            validos = {digito}
        else:
            validos = {letra, digito}
        if nif[8] not in validos:
            raise ValidationError(f"El dígito de control del CIF {nif} no es correcto.")
        return nif

    if re.fullmatch(r"[A-Z]{2}[0-9A-Z]{2,13}", nif):  # NIF-IVA de otro país (DE..., FR..., IE...)
        return nif

    raise ValidationError(f"«{valor}» no parece un NIF, NIE o CIF válido.")