"""Conservative, deterministic keys for exact matching."""

import re

from odoo.addons.phone_validation.tools.phone_validation import phone_format, phone_parse


_EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$", re.ASCII)


def normalize_email(value):
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not _EMAIL.fullmatch(value) or '..' in value:
        return False
    return value.casefold()


def normalize_phone(value):
    """Accept only explicit international numbers; local numbers lack context."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value.startswith('+'):
        return False
    try:
        if not phone_parse(value, None):
            return False
        result = phone_format(value, None, None, force_format='E164')
    except Exception:  # Odoo's utility raises UserError on invalid input.
        return False
    return result if re.fullmatch(r'\+[1-9]\d{6,14}', result or '') else False
