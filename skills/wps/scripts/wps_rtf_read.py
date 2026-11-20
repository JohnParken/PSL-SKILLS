#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded, content-identified RTF stream reader for WPS-family and RTF documents."""

from typing import Dict, Any, List

from wps_access import ParseError
from wps_rtf_parser import (
    CHARSET_CODEPAGES,
    DBCS_CODEPAGES,
    IGNORED_DESTINATIONS,
    PINNED_ISSUE_CODES,
    RELATED_DESTINATIONS,
    REVISION_CONTROLS,
    RtfStreamParser,
)

MAX_RTF_BYTES = 32 * 1024 * 1024
MAX_PARAGRAPHS = 100000
MAX_TABLES = 10000
MAX_GROUP_DEPTH = 512
MAX_TABLE_CELLS = 100000
MAX_TABLE_ROWS = 100000
MAX_CELL_DEFINITIONS = 100000
MAX_OBJECTS = 1000
MAX_RELATED_STORIES = 4096


def _finalize_rtf_result(parser: RtfStreamParser, limits: Dict[str, int]) -> Dict[str, Any]:
    parser.flush_hex()
    if parser.stack:
        parser.add_geometry_issue({
            'code': 'rtf_unclosed_groups', 'count': len(parser.stack),
            'message': 'Input ended before all groups closed',
        })
    if parser.skip_bytes:
        parser.add_geometry_issue({
            'code': 'rtf_unicode_fallback_missing',
            'message': 'Input ended inside Unicode fallback',
        })
    parser.recover_pending_row()
    parser.flush_table_if_needed()
    parser.append_paragraph()
    for resource, omitted, retained, limit in (
            ('paragraphs', parser.omitted_paragraphs, len(parser.paragraphs), limits['max_paragraphs']),
            ('tables', parser.omitted_tables, len(parser.tables), limits['max_tables'])):
        if omitted:
            parser.limit_issues.append({
                'code': 'rtf_resource_limit_exceeded', 'resource': resource,
                'limit': limit, 'retained': retained, 'omitted': omitted,
                'message': 'Only the bounded prefix of this resource is retained',
            })
    if (parser.font_encoding_unknown and parser.encoded_text_seen
            or parser.font_encoding_conflict and parser.encoded_high_bytes_seen):
        parser.add_geometry_issue({
            'code': 'rtf_font_encoding_unresolved',
            'message': 'Byte text uses the document code page; conflicting or unknown font encoding cannot be reliably applied',
        })
    if parser.merge_markers != parser.captured_merge_markers:
        parser.add_geometry_issue({
            'code': 'rtf_merge_geometry_not_reconstructed',
            'message': 'Merge markers outside a complete cell grid',
        })
    related_stories = []
    if parser.include_related:
        related_stories = _read_related_groups(
            parser.data, parser.related_groups, parser.hidden_text, parser.revisions, parser.object_policy)
        if parser.related_groups:
            parser.add_geometry_issue({
                'code': 'rtf_related_content_recovered_unmapped',
                'count': len(parser.related_groups),
                'message': ('Stored related text was returned separately; section/page/anchor mapping '
                            'and main-body reading order remain unknown'),
            })
        if parser.omitted_related_groups:
            parser.limit_issues.append({
                'code': 'rtf_related_story_limit', 'limit': limits['max_related_stories'],
                'retained': len(parser.related_groups), 'omitted': parser.omitted_related_groups,
                'message': 'Only the bounded prefix of related destination groups was retained',
            })
    visibility_result = parser.visibility.finish(parser.idx)
    full_text = '\n'.join(p['text'] for p in parser.paragraphs)
    reserved_issues = parser.limit_issues + list(parser.pinned_issues.values())
    other_issues = [item for item in parser.geometry_issues if item.get('code') not in parser.pinned_issues]
    issues = reserved_issues + other_issues[:100 - len(reserved_issues)]
    partial = bool(issues)
    return {
        'schema_version': 'rtf_readonly_v1',
        'format': 'rtf',
        'scope': 'main-plus-related' if parser.include_related else 'main-body',
        'page_properties': parser.page_properties,
        'status': 'partial' if partial else 'ok',
        'partial': partial,
        'issues': issues,
        'geometry_issues_truncated': max(0, parser.geometry_issue_count - (len(issues) - len(parser.limit_issues))),
        **({'visibility': visibility_result} if visibility_result is not None else {}),
        'field_count': parser.field_count,
        'object_policy': parser.object_policy,
        'object_count': len(parser.objects),
        'objects': parser.objects,
        'objects_decoded': False,
        'hidden_control_count': parser.hidden_control_count,
        'revision_control_count': parser.revision_control_count,
        'omitted_paragraph_count': parser.omitted_paragraphs,
        'omitted_table_count': parser.omitted_tables,
        'text': full_text,
        'paragraphs': parser.paragraphs,
        'tables': parser.tables,
        'blocks': parser.blocks,
        **({
            'related_stories': related_stories,
            'related_scope': 'stored-destination-groups-only',
            'related_page_mapping': 'unknown',
        } if parser.include_related else {}),
        'reading_assessment': {
            'status': 'partial' if partial else 'ok',
            'character_count': len(full_text),
            'paragraph_count': len(parser.paragraphs),
            'table_count': len(parser.tables),
        },
    }


def parse_rtf_bytes(data: bytes, max_bytes: int = MAX_RTF_BYTES, *, hidden_text='include', revisions=None,
                    object_policy='placeholder', include_related=False) -> Dict[str, Any]:
    """Parse raw bytes of an RTF document into structured paragraphs and tables."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("RTF input must be bytes or bytearray")
    if len(data) > max_bytes:
        raise ValueError(f"RTF file exceeds size limit of {max_bytes} bytes")
    object_policy = 'placeholder' if object_policy is None else object_policy
    if object_policy not in ('reject', 'placeholder'):
        raise ParseError('unsupported', {'stage': 'options'}, 'Unknown RTF object policy')
    if type(include_related) is not bool:
        raise ParseError('unsupported', {'stage': 'options'}, 'include_related must be boolean')
    if not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError("Invalid RTF signature: missing {\\rtf header")
    limits = {
        'max_paragraphs': MAX_PARAGRAPHS,
        'max_tables': MAX_TABLES,
        'max_group_depth': MAX_GROUP_DEPTH,
        'max_table_cells': MAX_TABLE_CELLS,
        'max_table_rows': MAX_TABLE_ROWS,
        'max_cell_definitions': MAX_CELL_DEFINITIONS,
        'max_objects': MAX_OBJECTS,
        'max_related_stories': MAX_RELATED_STORIES,
    }
    parser = RtfStreamParser(
        data, hidden_text=hidden_text, revisions=revisions,
        object_policy=object_policy, include_related=include_related, limits=limits,
    ).parse()
    return _finalize_rtf_result(parser, limits)


def _related_text(candidate):
    chunks = []
    for block in candidate.get('blocks', []):
        if block.get('type') == 'paragraph':
            chunks.append(candidate['paragraphs'][block['index']]['text'])
        elif block.get('type') == 'table':
            table = candidate['tables'][block['index']]
            chunks.extend('\t'.join(cell['text'] for cell in row) for row in table['rows'])
    return '\n'.join(chunks)


def _read_related_groups(data, groups, hidden_text, revisions, object_policy):
    stories = []
    for group in groups:
        prefix = bytearray(b'{\\rtf1')
        codepage = group['codepage']
        if group['codepage_declared'] and codepage.startswith('cp') and codepage[2:].isdigit():
            prefix.extend(('\\ansicpg' + codepage[2:]).encode('ascii'))
        prefix.extend(('\\uc' + str(group['uc']) + ' ').encode('ascii'))
        payload = bytes(prefix) + data[group['content_start']:group['content_end']] + b'}'
        mapping_issue = {
            'code': 'rtf_related_mapping_unknown',
            'destination': group['destination'],
            'message': ('Related destination text was parsed in isolation; inherited layout, '
                        'section/page/anchor mapping and main-body order remain unknown'),
        }
        try:
            candidate = parse_rtf_bytes(
                payload,
                hidden_text=hidden_text,
                revisions=revisions,
                object_policy=object_policy,
                include_related=False,
            )
            story_issues = [mapping_issue, *candidate.get('issues', [])]
            story = {
                'type': group['destination'],
                'source': {
                    'byte_start': group['group_start'],
                    'byte_end': group['group_end'],
                    'destination': group['destination'],
                },
                'text': _related_text(candidate),
                'paragraphs': [item['text'] for item in candidate.get('paragraphs', [])],
                'tables': candidate.get('tables', []),
                'references': [],
                'anchor_status': 'stored_destination_group_only',
                'page_mapping': 'unknown',
                'cp_range': None,
                'status': 'partial',
                'issues': story_issues,
            }
        except (ParseError, ValueError) as exc:
            story = {
                'type': group['destination'],
                'source': {
                    'byte_start': group['group_start'],
                    'byte_end': group['group_end'],
                    'destination': group['destination'],
                },
                'text': None,
                'paragraphs': [],
                'tables': [],
                'references': [],
                'anchor_status': 'stored_destination_group_only',
                'page_mapping': 'unknown',
                'cp_range': None,
                'status': getattr(exc, 'status', 'corrupt'),
                'issues': [mapping_issue, {
                    'code': 'rtf_related_content_unreadable',
                    'message': str(exc),
                }],
            }
        stories.append(story)
    return stories


def require_publishable_candidate(candidate):
    """Related-content omissions are disclosed; recovered corruption cannot publish."""
    allowed = {'rtf_destination_skipped', 'rtf_field_cached_result_unverified'}
    if candidate.get('status') not in ('ok', 'partial'):
        raise RuntimeError('Candidate readback rejected')
    issues = candidate.get('issues', [])
    if candidate.get('status') == 'partial' and (candidate.get('geometry_issues_truncated') or not issues
                                                or any(item.get('code') not in allowed for item in issues)):
        raise RuntimeError('Candidate readback requires structural or text recovery; publication stopped')
