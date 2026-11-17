"""Bound native JSON and preflight original inline chart package transports."""
from __future__ import annotations

import math

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSON_NODES = 20000
MAX_JSON_DEPTH = 40
MAX_STRING_CHARACTERS = 1024 * 1024


def validate_payload_limits(value: object) -> None:
    """Validate an already-decoded native payload without recursive traversal."""
    stack = [(value, 1)]
    nodes = 0
    characters = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise RuntimeError('Native JSON exceeds its node or depth budget')
        if isinstance(item, dict):
            if 'source_package' in item:
                from .source_package_guard import validate_source_chart_package
                validate_source_chart_package(item['source_package'])
            for key, child in item.items():
                stack.extend(((key, depth + 1), (child, depth + 1)))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            characters += len(item)
            if characters > MAX_STRING_CHARACTERS:
                raise RuntimeError('Native JSON exceeds its cumulative string budget')
        elif isinstance(item, float) and not math.isfinite(item):
            raise RuntimeError('Native JSON numbers must be finite')
