"""Offline structural checks shared by human and machine v3 records.

The emitted JSON Schema is authoritative for the record structure. Runtime-only
checks (source/quote binding, enabled roles and chronology) live in ``schema.py``.
Diagnostics contain field paths and rule names, never submitted field values.
"""

from __future__ import annotations

import math
from functools import cache

from jsonschema import Draft202012Validator, FormatChecker, validators


def _path(parts: list[str | int]) -> str:
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts)


def _json_errors(value: object, parts: list[str | int]) -> list[dict]:
    if isinstance(value, float) and not math.isfinite(value):
        return [{"code": "invalid_json", "path": _path(parts),
                 "message": f"{_path(parts)} must be a finite JSON number."}]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            return [{"code": "invalid_json", "path": _path(parts),
                     "message": f"{_path(parts)} must have string object keys."}]
        return [error for key, item in value.items()
                for error in _json_errors(item, [*parts, key])]
    if isinstance(value, list):
        return [error for index, item in enumerate(value)
                for error in _json_errors(item, [*parts, index])]
    if value is not None and not isinstance(value, (str, int, float)):
        return [{"code": "invalid_json", "path": _path(parts),
                 "message": f"{_path(parts)} must be a JSON value."}]
    return []


@cache
def _validator(kind: str):
    from job_intake.labeling.schema import _valid_date, gold_schema, prediction_schema

    builders = {"gold": gold_schema, "prediction": prediction_schema}
    if kind not in builders:
        raise ValueError("Unknown v3 schema kind")
    checker = FormatChecker()
    # These formats do not depend on jsonschema's optional format packages. Keep
    # the existing ISO/RFC3339 syntax in the human validator as their source.
    checker.checks("date")(lambda value: not isinstance(value, str)
                           or (len(value) == 10 and _valid_date(value)))
    checker.checks("date-time")(lambda value: not isinstance(value, str)
                                or (len(value) > 10 and _valid_date(value)))
    # Parsed JSON decisions must be real integers: neither bool nor 1.0 is an int
    # in the existing gold/runtime contract. Other numeric fields permit floats.
    strict_integer = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int,
    )
    validator_class = validators.extend(Draft202012Validator, type_checker=strict_integer)
    return validator_class(builders[kind](), format_checker=checker)


def json_schema_errors(value: object, *, kind: str) -> list[dict]:
    """Validate a JSON record, with deterministic, value-free field diagnostics."""
    if errors := _json_errors(value, []):
        return errors
    errors = []
    for finding in _validator(kind).iter_errors(value):
        parts = list(finding.absolute_path)
        if finding.validator == "required" and isinstance(finding.instance, dict):
            missing = sorted(set(finding.validator_value) - set(finding.instance))
            # jsonschema emits one error per missing property. Its message carries
            # input data for other keywords, so derive this path without messages.
            missing_name = next((name for name in missing
                                 if finding.message == f"'{name}' is a required property"), None)
            if missing_name is not None:
                parts.append(missing_name)
        path = _path(parts)
        errors.append({"code": "schema_violation", "path": path,
                       "message": f"{path} violates {finding.validator}."})
    return sorted(errors, key=lambda error: (error["path"], error["message"]))
