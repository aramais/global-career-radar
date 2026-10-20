"""Score numbers supported by JSON and ranking, without integer-to-float coercion."""

from __future__ import annotations

import math


def is_finite_score(value: object) -> bool:
    """Integers are always finite; reject booleans and non-finite floats."""
    return type(value) is int or (type(value) is float and math.isfinite(value))
