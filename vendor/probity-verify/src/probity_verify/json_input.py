"""Bound JSON parsing before allocation, with no duplicate-member inference."""

from __future__ import annotations

import json
from typing import Callable

from .common import CaseError


def parse_json(raw: bytes, *, max_bytes: int, max_depth: int, max_nodes: int,
               parse_float: Callable = float, ascii_only: bool = False):
    if len(raw) > max_bytes:
        raise CaseError("JSON input exceeds byte budget")
    depth = nodes = 0
    quoted = escaped = token = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
            token = False
            nodes += 1
        elif byte in (91, 123):
            depth += 1
            nodes += 1
            token = False
        elif byte in (93, 125):
            depth -= 1
            token = False
        elif byte in (9, 10, 13, 32, 44, 58):
            token = False
        elif not token:
            nodes += 1
            token = True
        if depth > max_depth or nodes > max_nodes:
            raise CaseError("JSON input exceeds structure budget")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CaseError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise CaseError(f"non-JSON number: {value}")

    try:
        # JSON loads a decoded string so it cannot infer UTF-16/UTF-32.
        # Native records restrict the same UTF-8 wire domain to ASCII bytes.
        text = raw.decode("ascii" if ascii_only else "utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant,
                          parse_float=parse_float)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CaseError):
            raise
        raise CaseError("invalid JSON input") from exc
