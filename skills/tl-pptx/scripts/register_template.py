"""Template registration compatibility facade (callable API; TL entry owns publication)."""
from register_template_parts.library import (
    is_safe_template_id as is_safe_template_id,
    get_library_paths as get_library_paths,
    get_library_scope as get_library_scope,
)
from register_template_parts.specification import (
    _read_spec as _read_spec,
    _extract_section_field as _extract_section_field,
    _clean_field_value as _clean_field_value,
    _find_first_color as _find_first_color,
    _extract_primary_color as _extract_primary_color,
    _summary_from_use_cases as _summary_from_use_cases,
    _has_kind_qualified_spec as _has_kind_qualified_spec,
    _qualified_template_specs as _qualified_template_specs,
    validate_qualified_spec_identity as validate_qualified_spec_identity,
    _validate_spec_shape as _validate_spec_shape,
    _has_qualified_roster_spec as _has_qualified_roster_spec,
    _template_content_dir as _template_content_dir,
    _list_pages as _list_pages,
    _derive_page_types as _derive_page_types,
    _numbered_section as _numbered_section,
    _markdown_subsection as _markdown_subsection,
    _markdown_table_rows as _markdown_table_rows,
    _style_value_is_substantive as _style_value_is_substantive,
    _style_field_is_declared as _style_field_is_declared,
)
from register_template_parts.brand import (
    _validate_brand_spec as _validate_brand_spec,
)
from register_template_parts.style import (
    _validate_style_spec as _validate_style_spec,
)
from register_template_parts.svg import (
    _validate_svg_template_spec as _validate_svg_template_spec,
    validate_shadowed_deck_spec as validate_shadowed_deck_spec,
)
from register_template_parts.entries import (
    _extract_entry as _extract_entry,
    _resolve_spec_path as _resolve_spec_path,
    validate_brand_workspace as validate_brand_workspace,
    validate_style_workspace as validate_style_workspace,
)
from register_template_parts.registration import (
    _load_index as _load_index,
    _write_index as _write_index,
    _enumerate_ids as _enumerate_ids,
    _print_completion_card as _print_completion_card,
    main as main,
)
from register_template_parts.common import (
    KIND_CONFIG as KIND_CONFIG,
    SCRIPT_DIR as SCRIPT_DIR,
    SKILL_DIR as SKILL_DIR,
    SpecParseError as SpecParseError,
    TEMPLATES_DIR as TEMPLATES_DIR,
    _BRAND_ALLOWED_FRONTMATTER_FIELDS as _BRAND_ALLOWED_FRONTMATTER_FIELDS,
    _BRAND_ASSET_REF_RE as _BRAND_ASSET_REF_RE,
    _BRAND_FORBIDDEN_SECTIONS as _BRAND_FORBIDDEN_SECTIONS,
    _BRAND_PROVENANCE_VALUES as _BRAND_PROVENANCE_VALUES,
    _BRAND_REQUIRED_SECTIONS as _BRAND_REQUIRED_SECTIONS,
    _HEX_COLOR_RE as _HEX_COLOR_RE,
    _PORTABLE_STYLE_ID_RE as _PORTABLE_STYLE_ID_RE,
    _SPEC_NAME_RE as _SPEC_NAME_RE,
    _STYLE_ALLOWED_FRONTMATTER_FIELDS as _STYLE_ALLOWED_FRONTMATTER_FIELDS,
    _STYLE_CUSTOM_FIELDS as _STYLE_CUSTOM_FIELDS,
    _STYLE_FORBIDDEN_FIELDS as _STYLE_FORBIDDEN_FIELDS,
    _STYLE_FORBIDDEN_SECTIONS as _STYLE_FORBIDDEN_SECTIONS,
    _STYLE_REQUIRED_FIELDS as _STYLE_REQUIRED_FIELDS,
    _STYLE_REQUIRED_SECTIONS as _STYLE_REQUIRED_SECTIONS,
    _STYLE_REVIEW_TRIGGER_MARKER as _STYLE_REVIEW_TRIGGER_MARKER,
    annotations as annotations,
)
