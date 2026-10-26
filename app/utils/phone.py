"""Kenyan phone number normalisation.

Canonical storage format is E.164: +254XXXXXXXXX (e.g. +254712345678).
Accepted inputs: 0712345678, 0112345678, 712345678, 254712345678,
+254712345678, 00254712345678 (spaces, dashes, dots and brackets ignored).
"""
import re

_SEPARATORS = re.compile(r"[\s\-().]")
_KE_MOBILE = re.compile(r"(?:\+?254|0)?([17][0-9]{8})")


def normalize_ke_phone(raw) -> str | None:
    """Return the +254... form of a Kenyan mobile number, or None if invalid."""
    if raw is None:
        return None
    cleaned = _SEPARATORS.sub("", str(raw))
    if cleaned.startswith("00254"):
        cleaned = cleaned[2:]
    match = _KE_MOBILE.fullmatch(cleaned)
    return f"+254{match.group(1)}" if match else None


def mask_phone(phone: str | None) -> str:
    """Mask a phone number for logs and screens, e.g. +254******678."""
    if not phone or len(phone) < 8:
        return "***"
    return phone[:4] + "*" * (len(phone) - 7) + phone[-3:]
