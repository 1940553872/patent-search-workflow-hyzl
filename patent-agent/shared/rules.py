"""Deterministic three-valued evaluation; missing data never means false.

Input: a hard-condition tree and one normalized publication record.
Output: PASS, FAIL, or UNKNOWN. This module does not infer technical meaning.
"""

from datetime import date
from typing import Any, Mapping

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
VALUES = frozenset((PASS, FAIL, UNKNOWN))
FIELDS = frozenset(("dates.pub", "dates.app", "dates.pri", "authority", "legal.state",
                    "legal.expiry", "legal.as_of", "applicants", "kind", "ipc", "title",
                    "application_number", "family_id"))
DATE_FIELDS = frozenset(("dates.pub", "dates.app", "dates.pri", "legal.expiry", "legal.as_of"))


def conjunction(values):
    values = tuple(values)
    if FAIL in values:
        return FAIL
    return PASS if all(value == PASS for value in values) else UNKNOWN


def disjunction(values):
    values = tuple(values)
    if PASS in values:
        return PASS
    return FAIL if all(value == FAIL for value in values) else UNKNOWN


def negate(value):
    return {PASS: FAIL, FAIL: PASS}.get(value, UNKNOWN)


def parse_date(value):
    """Only complete ISO dates are comparable; partial dates stay unknown."""
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def conflicted(record: Mapping[str, Any], field: str) -> bool:
    """Recognize both dotted and nested conflict paths without discarding peers."""
    conflicts = record.get("conflicts") or {}
    if not isinstance(conflicts, Mapping):
        return bool(conflicts)
    if conflicts.get(field):
        return True
    parts = field.split(".")
    for key, value in conflicts.items():
        if value and (key == "*" or field.startswith(str(key) + ".") and not isinstance(value, Mapping)):
            return True
    current = conflicts
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return bool(current)


def get_field(record: Mapping[str, Any], field: str):
    if conflicted(record, field):
        return None
    current = record
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _compare(actual, comparison, expected, field):
    if actual is None or actual == "" or actual == [] or expected is None:
        return UNKNOWN
    if field in DATE_FIELDS:
        actual = parse_date(actual)
        if comparison == "in" and isinstance(expected, list):
            expected = [parse_date(item) for item in expected]
            if any(item is None for item in expected):
                return UNKNOWN
        else:
            expected = parse_date(expected)
        if actual is None or expected is None:
            return UNKNOWN
    try:
        if comparison == "eq":
            result = expected in actual if isinstance(actual, list) and not isinstance(expected, list) else actual == expected
        elif comparison == "ne":
            return negate(_compare(actual, "eq", expected, ""))
        elif comparison == "in":
            if not isinstance(expected, (list, tuple)):
                return UNKNOWN
            result = any(item in expected for item in actual) if isinstance(actual, list) else actual in expected
        elif comparison == "contains":
            if not isinstance(expected, str):
                return UNKNOWN
            items = actual if isinstance(actual, list) else [actual]
            if not all(isinstance(item, str) for item in items):
                return UNKNOWN
            result = any(expected in item for item in items)
        elif comparison in ("lt", "lte", "gt", "gte"):
            if isinstance(actual, (dict, list)) or isinstance(expected, (dict, list)):
                return UNKNOWN
            result = {"lt": lambda: actual < expected, "lte": lambda: actual <= expected,
                      "gt": lambda: actual > expected, "gte": lambda: actual >= expected}[comparison]()
        else:
            return UNKNOWN
    except (TypeError, ValueError):
        return UNKNOWN
    return PASS if result else FAIL


def evaluate(tree, record, feature_values=None):
    """Evaluate AND / OR / NOT recursively. Empty AND=PASS; empty OR=FAIL.

    Lists use membership for equality with a scalar, and substring matching for
    ``contains``. Feature values must already be verified by the evidence module.
    Unknown/malformed nodes return UNKNOWN instead of silently passing.
    """
    if not isinstance(tree, Mapping) or not isinstance(record, Mapping):
        return UNKNOWN
    if "op" in tree:
        operation, args = tree.get("op"), tree.get("args")
        if not isinstance(args, list):
            return UNKNOWN
        values = [evaluate(node, record, feature_values) for node in args]
        if operation == "AND":
            return conjunction(values)
        if operation == "OR":
            return disjunction(values)
        if operation == "NOT" and len(values) == 1:
            return negate(values[0])
        return UNKNOWN
    if "feature" in tree:
        value = (feature_values or {}).get(tree.get("feature"), UNKNOWN)
        return value if isinstance(value, str) and value in VALUES else UNKNOWN
    field = tree.get("field")
    if field not in FIELDS or "value" not in tree:
        return UNKNOWN
    return _compare(get_field(record, field), tree.get("cmp"), tree["value"], field)


def feature_ids(tree):
    """Return only the technical conditions used in the hard tree."""
    if not isinstance(tree, Mapping):
        return set()
    if "feature" in tree:
        return {tree["feature"]} if isinstance(tree["feature"], str) else set()
    result = set()
    for child in tree.get("args", []) if isinstance(tree.get("args", []), list) else []:
        result.update(feature_ids(child))
    return result
