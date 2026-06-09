"""Enmascaramiento básico de PII de alto riesgo.

Se aplica al mensaje **antes** de enviarlo al LLM (un tercero) y antes de
guardarlo, para no exponer identificadores financieros sensibles.

Criterio (importante): se enmascara SOLO lo de altísimo riesgo — números de
tarjeta (PAN) y de cuenta bancaria —, conservando los últimos 4 dígitos. NO se
enmascaran nombres, correos ni montos, porque el modelo los necesita para
clasificar y redactar la respuesta; enmascarar de más degradaría la clasificación.

DEMO: usa expresiones regulares. En producción se usaría un servicio dedicado de
detección de PII (Microsoft Presidio, AWS Comprehend PII, Google Cloud DLP), que
es mucho más robusto que un regex.
"""

from __future__ import annotations

import re

# Tarjeta (PAN): 13-19 dígitos, en grupos separados por espacio/guion o seguidos.
_RE_TARJETA = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")

# Cuenta bancaria tras una palabra clave (cuenta/ahorros/corriente): grupo de dígitos.
_RE_CUENTA = re.compile(
    r"(?i)\b(cuenta|ahorros|corriente)(\D{0,15}?)(\d(?:[\d -]{4,}\d))"
)


def _enmascarar_digitos(texto: str) -> str:
    """Reemplaza todos los dígitos por '*' salvo los últimos 4."""
    solo_digitos = re.sub(r"\D", "", texto)
    if len(solo_digitos) <= 4:
        return texto
    return "*" * (len(solo_digitos) - 4) + solo_digitos[-4:]


def enmascarar_pii(texto: str | None) -> str | None:
    """Devuelve el texto con tarjetas y cuentas bancarias enmascaradas."""
    if not texto:
        return texto
    texto = _RE_TARJETA.sub(lambda m: _enmascarar_digitos(m.group()), texto)
    texto = _RE_CUENTA.sub(
        lambda m: m.group(1) + m.group(2) + _enmascarar_digitos(m.group(3)), texto
    )
    return texto
