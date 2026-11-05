#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Selective reading projections for WPS documents: headings, ranges, and sections."""

from typing import List, Dict, Optional, Tuple, Any
import re
from copy import deepcopy


def projection_context(result):
    keys = ('status', 'partial', 'format', 'scope', 'issues', 'diagnostics', 'omitted_regions',
            'reading_assessment', 'visibility', 'limitations', 'container_issues', 'heading_diagnostics')
    context = {key: deepcopy(result[key]) for key in keys if key in result}
    context['source_status'] = result.get('status', 'ok')
    context['projection_scope'] = 'selected-main-paragraphs-not-complete-document'
    return context


def heading_entries(result):
    paragraphs = extract_paragraphs(result)
    metadata = result.get('paragraph_metadata', result.get('paragraphs', []))
    entries = []
    for index, text in enumerate(paragraphs):
        item = metadata[index] if index < len(metadata) else None
        outline = item.get('outline_level') if isinstance(item, dict) else None
        if type(outline) is int and 0 <= outline <= 8:
            entries.append({'index': index + 1, 'level': outline + 1, 'text': text, 'basis': 'stored_outline'})
        elif outline is None:
            level, title = detect_heading(text)
            if level is not None:
                entries.append({'index': index + 1, 'level': level, 'text': title, 'basis': 'text_heuristic'})
    return entries


HEADING_PATTERNS = [
    (1, re.compile(r"^(?:#\s+|[一二三四五六七八九十百]+、|第[一二三四五六七八九十百]+[章篇])\s*(.+)$")),
    (2, re.compile(r"^(?:##\s+|[（(][一二三四五六七八九十百]+[）)]|第[一二三四五六七八九十百]+[节条])\s*(.+)$")),
    (3, re.compile(r"^(?:###\s+|\d+[\.、])\s*(.+)$")),
    (4, re.compile(r"^(?:####\s+|[（(]\d+[）)])\s*(.+)$")),
]


def extract_paragraphs(result: Dict[str, Any]) -> List[str]:
    """Extract plain text paragraph strings from arbitrary wps read result."""
    raw_paragraphs = result.get("paragraphs", [])
    if raw_paragraphs:
        out = []
        for p in raw_paragraphs:
            if isinstance(p, dict):
                out.append(str(p.get("text", "")))
            else:
                out.append(str(p))
        return out
    text = result.get("text", "")
    if text:
        return [line for line in text.split("\n") if line.strip()]
    return []


def detect_heading(text: str) -> Tuple[Optional[int], Optional[str]]:
    """Detect heading level and clean text if paragraph is a heading."""
    stripped = text.strip()
    if not stripped:
        return None, None
    for level, pattern in HEADING_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return level, stripped
    return None, None


def project_headings(result: Dict[str, Any]) -> Dict[str, Any]:
    """Filter document content down to structured headings hierarchy."""
    paragraphs = extract_paragraphs(result)
    headings = heading_entries(result)
    return {
        **projection_context(result),
        "mode": "heading_only",
        "total_paragraphs": len(paragraphs),
        "total_headings": len(headings),
        "headings": headings,
        "text": "\n".join(f"Level {h['level']}: {h['text']}" for h in headings),
    }


def project_paragraph_range(result: Dict[str, Any], range_spec: str) -> Dict[str, Any]:
    """Slice paragraphs by 1-based inclusive START:END range."""
    paragraphs = extract_paragraphs(result)
    total = len(paragraphs)
    if not re.fullmatch(r"\d+:\d+", range_spec.strip()):
        raise ValueError(f"Invalid paragraph range format: '{range_spec}', expected START:END (e.g. 1:20)")
    start_str, end_str = range_spec.strip().split(":", 1)
    start = int(start_str)
    end = int(end_str)
    if start < 1:
        raise ValueError(f"Range start must be >= 1, got {start}")
    if end < start:
        raise ValueError(f"Range end ({end}) cannot be less than start ({start})")

    start_idx = start - 1
    end_idx = min(end, total)
    sliced = paragraphs[start_idx:end_idx] if start_idx < total else []

    return {
        **projection_context(result),
        "mode": "paragraph_range",
        "range": f"{start}:{end}",
        "total_paragraphs": total,
        "returned_paragraphs": len(sliced),
        "paragraphs": [{"index": start + i, "text": t} for i, t in enumerate(sliced)],
        "text": "\n".join(sliced),
    }


def project_section(result: Dict[str, Any], section_query: str, section_index=None) -> Dict[str, Any]:
    """Extract paragraphs under a specific heading until the next same or higher level heading."""
    paragraphs = extract_paragraphs(result)
    target = section_query.strip().lower()
    if not target:
        raise ValueError('Section query must not be empty')
    headings = heading_entries(result)
    matches = [item for item in headings if item['text'].strip().lower() == target]
    if not matches:
        matches = [item for item in headings if target in item['text'].strip().lower()]
    if section_index is not None:
        matches = [item for item in matches if item['index'] == section_index]
    if len(matches) != 1:
        return {
            **projection_context(result),
            "status": result.get('status', 'ok') if result.get('status') == 'partial' else ('ambiguous' if matches else 'not_found'),
            "selection_status": 'ambiguous' if matches else 'not_found',
            "candidates": matches,
            "mode": "section",
            "section_query": section_query,
            "total_paragraphs": len(paragraphs),
            "paragraphs": [],
            "text": "",
        }

    match = matches[0]
    matched_idx, matched_level = match['index'] - 1, match['level']
    heading_map = {item['index'] - 1: item for item in headings}
    section_paragraphs = [paragraphs[matched_idx]]
    for idx in range(matched_idx + 1, len(paragraphs)):
        p_text = paragraphs[idx]
        lvl = heading_map.get(idx, {}).get('level')
        if lvl is not None and lvl <= matched_level:
            break
        section_paragraphs.append(p_text)

    return {
        **projection_context(result),
        "selection_status": "matched",
        "heading_basis": match['basis'],
        "mode": "section",
        "section_query": section_query,
        "matched_heading": paragraphs[matched_idx],
        "heading_level": matched_level,
        "total_paragraphs": len(section_paragraphs),
        "paragraphs": [{"index": matched_idx + 1 + i, "text": t} for i, t in enumerate(section_paragraphs)],
        "text": "\n".join(section_paragraphs),
    }
