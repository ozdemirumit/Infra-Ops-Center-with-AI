"""
Tiny templating helper for workflow definitions.

Supports {{ path.to.value }} substitution from a context dict and a small
set of boolean predicates used in branch `when:` clauses.

Deliberately minimal — no arbitrary expression evaluation, so untrusted
YAML cannot run Python code.
"""

import re
from typing import Any


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def _lookup(path: str, ctx: dict) -> Any:
    """Walk a dotted path in the context. Returns None if any segment is missing."""
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def render(value: Any, ctx: dict) -> Any:
    """
    Replace {{ path }} placeholders inside strings, dicts, and lists.
    Non-string scalars pass through untouched.
    """
    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            v = _lookup(m.group(1).strip(), ctx)
            return "" if v is None else str(v)
        # If the whole string is a single placeholder, return the typed value
        m = _PLACEHOLDER_RE.fullmatch(value.strip())
        if m:
            v = _lookup(m.group(1).strip(), ctx)
            return v if v is not None else value
        return _PLACEHOLDER_RE.sub(_sub, value)

    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


_TRUTHY = {"true", "yes", "1", "ok", "on"}
_FALSY = {"false", "no", "0", "off", ""}


def evaluate_when(expr: str, ctx: dict) -> bool:
    """
    Evaluate a `when:` expression.

    Supported forms:
      - `path.to.value`               → truthy check
      - `path == value`               → string equality
      - `path != value`               → string inequality
      - `path contains value`         → substring
      - `path in [a, b, c]`           → list membership
      - `not path`                    → boolean negation
      - `path.status == "failed"`     → quoted strings ok

    Anything more complex is rejected (returns False).
    """
    if not expr:
        return False

    s = expr.strip()
    negate = False
    if s.startswith("not "):
        negate = True
        s = s[4:].strip()

    def _result(val: bool) -> bool:
        return (not val) if negate else val

    # path == "value"  /  path != "value"
    for op in (" == ", " != "):
        if op in s:
            left, right = s.split(op, 1)
            lv = _lookup(left.strip(), ctx)
            rv = right.strip().strip("\"'")
            equal = str(lv) == rv
            return _result(equal) if op == " == " else _result(not equal)

    # path contains "value"
    if " contains " in s:
        left, right = s.split(" contains ", 1)
        lv = _lookup(left.strip(), ctx)
        rv = right.strip().strip("\"'")
        return _result(rv in str(lv) if lv is not None else False)

    # path in [a, b]
    if " in [" in s and s.rstrip().endswith("]"):
        left, right = s.split(" in [", 1)
        lv = _lookup(left.strip(), ctx)
        items = [it.strip().strip("\"'") for it in right.rstrip("]").split(",")]
        return _result(str(lv) in items)

    # bare path → truthy
    v = _lookup(s, ctx)
    if v is None:
        return _result(False)
    if isinstance(v, bool):
        return _result(v)
    if isinstance(v, (int, float)):
        return _result(v != 0)
    sv = str(v).lower().strip()
    if sv in _TRUTHY:
        return _result(True)
    if sv in _FALSY:
        return _result(False)
    return _result(bool(v))
