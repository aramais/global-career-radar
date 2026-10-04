from __future__ import annotations

from typing import Any


def spreadsheet_safe_value(value: Any) -> Any:
    """Keep untrusted text cells from being interpreted as spreadsheet formulas.

    Numeric values retain their type. Apply this at the final CSV boundary so
    every export uses the same policy, including URLs and generated text.
    """
    if isinstance(value, str) and (
        value.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
        or value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value
