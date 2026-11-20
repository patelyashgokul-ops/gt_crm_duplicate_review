"""Conservative, deterministic keys for exact matching."""

import re

from odoo.addons.phone_validation.tools.phone_validation import phone_format, phone_parse


_LOCAL = re.compile(r'[A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+)*\Z')
_LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\Z')


def normalize_email(value):
    if not isinstance(value, str):
        return False
    value = value.strip()
    if len(value) > 254 or value.count('@') != 1:
        return False
    local, domain = value.split('@')
    labels = domain.split('.')
    if (len(local) > 64 or not _LOCAL.fullmatch(local)
            or len(labels) < 2 or any(len(label) > 63 or not _LABEL.fullmatch(label) for label in labels)
            or len(labels[-1]) < 2):
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
