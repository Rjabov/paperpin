"""Checksum proofs (§6.6.3) — mathematical certainty where formats allow.

Only algorithms we are certain of are implemented as checksums; everything
else is format-regex only, honestly labeled. An IBAN failing mod-97 as read
but passing after confusable substitution (O→0, I→1, S→5, B→8) is repaired
with low_confidence + note (E-14).
"""
from __future__ import annotations

import re
from typing import Optional

CONFUSABLES = {"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}

_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")

# format regexes (not checksums) for common EU VAT ids
VAT_FORMATS = {
    "SK": re.compile(r"^SK\d{10}$"),
    "LV": re.compile(r"^LV\d{11}$"),
    "DE": re.compile(r"^DE\d{9}$"),
    "PL": re.compile(r"^PL\d{10}$"),
    "CZ": re.compile(r"^CZ\d{8,10}$"),
    "AT": re.compile(r"^ATU\d{8}$"),
    "LT": re.compile(r"^LT(\d{9}|\d{12})$"),
    "EE": re.compile(r"^EE\d{9}$"),
    "FR": re.compile(r"^FR[A-Z0-9]{2}\d{9}$"),
    "NL": re.compile(r"^NL[A-Z0-9]{9}B\d{2}$"),
    "IT": re.compile(r"^IT\d{11}$"),
    "ES": re.compile(r"^ES[A-Z0-9]\d{7}[A-Z0-9]$"),
    "GB": re.compile(r"^GB(\d{9}|\d{12})$"),
    "IE": re.compile(r"^IE(\d{7}[A-W][A-I]?|\d[A-Z0-9+*]\d{5}[A-W])$"),
    "DK": re.compile(r"^DK\d{8}$"),
}


def _compact(value: str) -> str:
    return re.sub(r"[\s\-.]", "", str(value)).upper()


def iban_mod97(value: str) -> bool:
    s = _compact(value)
    if not _IBAN_RE.match(s):
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)  # A=10 … Z=35
    return int(digits) % 97 == 1


def iban_check(value: str) -> tuple[Optional[bool], Optional[str], Optional[str]]:
    """Returns (passed, repaired_value, note)."""
    s = _compact(value)
    if len(s) < 14:
        return None, None, None
    if not _IBAN_RE.match(s):
        return False, None, "value does not have an IBAN shape"
    if iban_mod97(s):
        return True, None, "IBAN mod-97 checksum passed"
    repaired = s[:2] + "".join(CONFUSABLES.get(c, c) for c in s[2:])
    if repaired != s and iban_mod97(repaired):
        return True, repaired, f"IBAN passed mod-97 after confusable repair ({s} → {repaired})"
    return False, None, "IBAN failed mod-97 checksum"


def ean_check_digit(value: str) -> Optional[bool]:
    """EAN-8/13, UPC-A (12) and GTIN-14 — one alternating-weight family."""
    s = _compact(value)
    if not re.fullmatch(r"\d{8}|\d{12,14}", s):
        return None
    digits = [int(c) for c in s]
    payload, check = digits[:-1], digits[-1]
    # weight 3 sits at odd distance from the check digit in every length
    total = sum(d * (3 if (len(payload) - i) % 2 == 1 else 1)
                for i, d in enumerate(payload))
    return (10 - total % 10) % 10 == check


def vat_check(value: str) -> tuple[Optional[bool], Optional[str]]:
    """Format check for many countries; true checksum where certain:
    SK (divisible by 11), PL NIP (weighted mod 11)."""
    s = _compact(value)
    country = s[:2]
    fmt = VAT_FORMATS.get(country)
    if fmt is None:
        return None, None
    if not fmt.match(s):
        return False, f"VAT id does not match the {country} format"
    if country == "SK":
        ok = int(s[2:]) % 11 == 0
        return ok, ("SK VAT checksum passed (divisible by 11)" if ok
                    else "SK VAT checksum FAILED (not divisible by 11)")
    if country == "PL":
        weights = [6, 5, 7, 2, 3, 4, 5, 6, 7]
        digits = [int(c) for c in s[2:]]
        ok = sum(w * d for w, d in zip(weights, digits[:9])) % 11 == digits[9]
        return ok, "PL NIP checksum " + ("passed" if ok else "FAILED")
    return True, f"matches the {country} VAT format (format check only)"


def date_plausible(value: str) -> Optional[bool]:
    """Basic plausibility (§6.6.3): parseable and within ±10 years of today."""
    from datetime import date

    from ..align.matchers import value_date_set
    dates = value_date_set(value)
    if not dates:
        return None
    this_year = date.today().year
    return any(this_year - 10 <= y <= this_year + 10 for (y, _m, _d) in dates)
