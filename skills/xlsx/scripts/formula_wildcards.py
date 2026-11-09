"""Literal, escaped and wildcard matching for formula criteria."""
from __future__ import annotations

def _wildcard_tokens(pattern: str) -> tuple[tuple[str, str], ...]:
    tokens = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if (char == "~" and index + 1 < len(pattern)
                and pattern[index + 1] in "*?~"):
            tokens.append(("literal", pattern[index + 1]))
            index += 2
        elif char == "*":
            tokens.append(("many", ""))
            index += 1
        elif char == "?":
            tokens.append(("one", ""))
            index += 1
        else:
            tokens.append(("literal", char))
            index += 1
    return tuple(tokens)


def _wildcard_matches(
    value: str,
    pattern: tuple[tuple[str, str], ...],
) -> bool:
    if all(kind == "literal" for kind, _ in pattern):
        literal = "".join(token_value for _, token_value in pattern)
        return value.casefold() == literal.casefold()

    value_index = 0
    token_index = 0
    last_many = -1
    many_match = 0
    while value_index < len(value):
        if token_index < len(pattern):
            kind, token_value = pattern[token_index]
            if (kind == "one" or (kind == "literal"
                                   and value[value_index].casefold() == token_value.casefold())):
                value_index += 1
                token_index += 1
                continue
            if kind == "many":
                last_many = token_index
                token_index += 1
                many_match = value_index
                continue
        if last_many >= 0:
            many_match += 1
            value_index = many_match
            token_index = last_many + 1
            continue
        return False
    while token_index < len(pattern) and pattern[token_index][0] == "many":
        token_index += 1
    return token_index == len(pattern)
