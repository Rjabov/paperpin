"""Arithmetic cross-checks (§6.6.4) — catches OCR-vs-LLM digit swaps that
string matching can't. A field failing arithmetic while geometrically verified
gets a `⚠ arithmetic` note, never a silent pass.

Relations are SCHEMA declarations (FieldSpec.proof on the result field):
  {"sum": ["subtotal", "vat_amount"]}          subtotal + vat_amount ≈ field
  {"product": ["qty", "unit_price"]}           qty × unit_price ≈ field
  {"percent_of": ["subtotal", "vat_rate"]}     subtotal × rate/100 ≈ field
The engine evaluates whatever the schema declares — invoice equations come
from the name-hint layer (schemas.enrich_spec), any other domain declares its
own. A relation that holds proves EVERY participant, tolerant to rounding.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..align.matchers import value_number_set
from ..types import FieldSpec

TOLERANCE = Decimal("0.011")


def _readings(value) -> set[Decimal]:
    return value_number_set(value)


def _close(a: Decimal, b: Decimal, scale: Decimal = Decimal(1)) -> bool:
    return abs(a - b) <= TOLERANCE * max(Decimal(1), scale)


def evaluate_relation(proof: dict, target_value, operand_values: dict
                      ) -> Optional[tuple[bool, str]]:
    """(holds?, human equation text) or None when an operand is missing.

    Ambiguous numeric strings carry interpretation SETS ('1,234' reads
    1.234 and 1234) — the relation holds when ANY combination of readings
    satisfies it, exactly like the matcher layer treats the page."""
    from itertools import product as _cartesian
    targets = _readings(target_value)
    if not targets:
        return None

    def combos(names):
        sets = [_readings(operand_values.get(n)) for n in names]
        if any(not s for s in sets):
            return None
        return list(_cartesian(*sets))

    if "sum" in proof:
        all_ops = combos(proof["sum"])
        if all_ops is None:
            return None
        for ops in all_ops:
            for target in targets:
                if _close(sum(ops, Decimal(0)), target):
                    text = " + ".join(str(v) for v in ops)
                    return True, f"{text} = {target}"
        ops = all_ops[0]
        text = " + ".join(str(v) for v in ops)
        return False, f"{text} = {sorted(targets)[0]}"
    if "product" in proof:
        all_ops = combos(proof["product"])
        if all_ops is None:
            return None
        for ops in all_ops:
            prod = Decimal(1)
            for v in ops:
                prod *= v
            for target in targets:
                if _close(prod, target, scale=max(Decimal(1), abs(target) / 100)):
                    text = " × ".join(str(v) for v in ops)
                    return True, f"{text} = {target}"
        ops = all_ops[0]
        text = " × ".join(str(v) for v in ops)
        return False, f"{text} = {sorted(targets)[0]}"
    if "percent_of" in proof:
        all_ops = combos(list(proof["percent_of"]))
        if all_ops is None:
            return None
        for base, rate in all_ops:
            expected = (base * rate / Decimal(100)).quantize(Decimal("0.01"))
            for target in targets:
                if _close(expected, target):
                    return True, f"{rate}% of {base} = {expected}"
        base, rate = all_ops[0]
        expected = (base * rate / Decimal(100)).quantize(Decimal("0.01"))
        return False, (f"{rate}% of {base} = {expected}, "
                       f"document says {sorted(targets)[0]}")
    return None


def run_arithmetic(values: dict, specs: dict[str, FieldSpec]) -> dict[str, list[str]]:
    """Evaluate every declared relation; returns field_name → notes.
    A passing relation credits the target AND every operand — the equation
    proves all its participants."""
    notes: dict[str, list[str]] = {}

    def add(field: str, msg: str) -> None:
        notes.setdefault(field, []).append(msg)

    for name, spec in specs.items():
        proof = getattr(spec, "proof", None)
        if not proof or name not in values:
            continue
        operand_names = proof.get("sum") or proof.get("product") or proof.get("percent_of") or []
        outcome = evaluate_relation(proof, values.get(name), values)
        if outcome is None:
            continue
        holds, equation = outcome
        label = " + ".join(operand_names) if "sum" in proof else (
            " × ".join(operand_names) if "product" in proof else
            f"{operand_names[1]} of {operand_names[0]}")
        if holds:
            for f in (name, *operand_names):
                add(f, f"arithmetic passed: {label} = {name}")
        else:
            for f in (name, *operand_names):
                add(f, f"⚠ arithmetic: {label} ≠ {name} ({equation})")
    return notes
