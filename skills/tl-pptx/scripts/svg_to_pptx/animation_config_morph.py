"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .animation_config import MorphPair
from typing import Any
from pptx_transitions import normalize_transition_effect_request

def _morph_scope_errors(
    slide_name: object,
    slide_cfg: dict[str, Any],
) -> list[str]:
    """Validate one destination slide's deterministic Morph declaration."""
    from .animation_config import (
        _unknown_field_errors,
        normalize_transition_effect_request,
    )
    if 'morph' not in slide_cfg:
        return []
    label = f'slide "{slide_name}" morph'
    morph = slide_cfg['morph']
    if not isinstance(morph, dict):
        return [f'animations.json {label} must be an object']

    errors = _unknown_field_errors(
        morph,
        frozenset({'from', 'pairs'}),
        label,
    )
    source_slide = morph.get('from')
    if not isinstance(source_slide, str) or not source_slide.strip():
        errors.append(
            f'animations.json {label} field "from" must be a non-empty slide stem'
        )

    pairs = morph.get('pairs')
    if not isinstance(pairs, dict) or not pairs:
        errors.append(
            f'animations.json {label} field "pairs" must be a non-empty object'
        )
    else:
        source_groups: dict[str, str] = {}
        destination_groups: dict[str, str] = {}
        for key, pair in pairs.items():
            pair_label = f'{label} pair "{key}"'
            if (
                not isinstance(key, str)
                or not key.strip()
                or key != key.strip()
                or key.startswith('!!')
                or any(ord(char) < 32 for char in key)
            ):
                errors.append(
                    f'animations.json {pair_label} key must be a trimmed, '
                    'non-empty name without the !! prefix or control characters'
                )
            if not isinstance(pair, dict):
                errors.append(f'animations.json {pair_label} must be an object')
                continue
            errors.extend(
                _unknown_field_errors(
                    pair,
                    frozenset({'from', 'to'}),
                    pair_label,
                )
            )
            for field in ('from', 'to'):
                value = pair.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        f'animations.json {pair_label} field "{field}" must '
                        'be a non-empty top-level group id'
                    )
            source_group = pair.get('from')
            if isinstance(source_group, str) and source_group.strip():
                previous = source_groups.setdefault(source_group, str(key))
                if previous != str(key):
                    errors.append(
                        f'animations.json {label} source group '
                        f'"{source_group}" is assigned to both "{previous}" '
                        f'and "{key}"'
                    )
            destination_group = pair.get('to')
            if isinstance(destination_group, str) and destination_group.strip():
                previous = destination_groups.setdefault(
                    destination_group,
                    str(key),
                )
                if previous != str(key):
                    errors.append(
                        f'animations.json {label} destination group '
                        f'"{destination_group}" is assigned to both "{previous}" '
                        f'and "{key}"'
                    )

    transition = slide_cfg.get('transition')
    if not isinstance(transition, dict) or 'effect' not in transition:
        errors.append(
            f'animations.json {label} requires an explicit slide transition '
            'effect "morph"'
        )
    else:
        try:
            effect, options = normalize_transition_effect_request(
                transition.get('effect'),
                transition.get('effect_options'),
            )
        except ValueError:
            pass
        else:
            if effect != 'morph':
                errors.append(
                    f'animations.json {label} requires transition effect "morph"'
                )
            elif options.get('morph_by', 'object') != 'object':
                errors.append(
                    f'animations.json {label} requires Morph by object'
                )
    return errors


def _resolve_morph_pairs(
    slide_order: list[str],
    config: dict[str, Any],
) -> tuple[list[MorphPair], list[str]]:
    """Resolve sidecar Morph declarations against the actual slide order."""
    from .animation_config import (
        MorphPair,
        _morph_scope_errors,
    )
    slides = config.get('slides', {})
    if not isinstance(slides, dict):
        return [], ['animations.json field "slides" must be an object']

    order_by_slide = {slide_name: index for index, slide_name in enumerate(slide_order)}
    pairs: list[MorphPair] = []
    errors: list[str] = []
    assignments: dict[str, dict[str, str]] = {}
    keys: dict[str, dict[str, str]] = {}
    declared_keys_by_destination: dict[str, set[str]] = {}

    for destination_slide, slide_cfg in slides.items():
        if not isinstance(slide_cfg, dict) or 'morph' not in slide_cfg:
            continue
        scope_errors = _morph_scope_errors(destination_slide, slide_cfg)
        if scope_errors:
            errors.extend(scope_errors)
            continue
        destination_index = order_by_slide.get(str(destination_slide))
        if destination_index is None:
            errors.append(
                'animations.json morph destination slide is missing: '
                f'{destination_slide}'
            )
            continue
        if destination_index == 0:
            errors.append(
                'animations.json first slide cannot declare an incoming Morph: '
                f'{destination_slide}'
            )
            continue

        morph = slide_cfg['morph']
        source_slide = str(morph['from'])
        expected_source = slide_order[destination_index - 1]
        if source_slide != expected_source:
            errors.append(
                f'animations.json slide "{destination_slide}" morph.from must '
                f'reference the immediately preceding slide "{expected_source}", '
                f'not "{source_slide}"'
            )
            continue

        declared_keys_by_destination[str(destination_slide)] = set(
            morph['pairs']
        )
        for key, pair in morph['pairs'].items():
            resolved = MorphPair(
                source_slide=source_slide,
                destination_slide=str(destination_slide),
                key=str(key),
                source_group_id=str(pair['from']),
                destination_group_id=str(pair['to']),
            )
            pair_conflict = False
            for slide_name, group_id in (
                (resolved.source_slide, resolved.source_group_id),
                (resolved.destination_slide, resolved.destination_group_id),
            ):
                slide_assignments = assignments.setdefault(slide_name, {})
                previous_key = slide_assignments.setdefault(group_id, resolved.key)
                if previous_key != resolved.key:
                    errors.append(
                        f'animations.json Morph group "{slide_name}/{group_id}" '
                        f'is assigned to both "{previous_key}" and "{resolved.key}"'
                    )
                    pair_conflict = True
                slide_keys = keys.setdefault(slide_name, {})
                previous_group = slide_keys.setdefault(resolved.key, group_id)
                if previous_group != group_id:
                    errors.append(
                        f'animations.json Morph key "{resolved.key}" maps to both '
                        f'"{slide_name}/{previous_group}" and '
                        f'"{slide_name}/{group_id}"'
                    )
                    pair_conflict = True
            if not pair_conflict:
                pairs.append(resolved)

    for destination_slide, declared_keys in declared_keys_by_destination.items():
        destination_index = order_by_slide[destination_slide]
        source_slide = slide_order[destination_index - 1]
        shared_keys = (
            set(keys.get(source_slide, {}))
            & set(keys.get(destination_slide, {}))
        )
        unexpected_keys = sorted(shared_keys - declared_keys)
        if unexpected_keys:
            errors.append(
                f'animations.json slide "{destination_slide}" Morph would '
                'force undeclared adjacent key(s): '
                + ', '.join(f'"{key}"' for key in unexpected_keys)
            )
    return pairs, list(dict.fromkeys(errors))


def resolve_morph_pairs(
    slide_order: list[str],
    config: dict[str, Any] | None,
) -> tuple[MorphPair, ...]:
    """Return validated deterministic Morph pairs in authored order."""
    from .animation_config import (
        _resolve_morph_pairs,
    )
    if not config:
        return ()
    pairs, errors = _resolve_morph_pairs(slide_order, config)
    if errors:
        raise ValueError('; '.join(errors))
    return tuple(pairs)


