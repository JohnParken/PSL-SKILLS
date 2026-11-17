"""Precharge every native reference occurrence before authoring hydration."""
from __future__ import annotations
import base64
import gzip
import hashlib
import io
from pathlib import Path
from lxml import etree

from tl_guard import ContractError, MAX_NODES, MAX_TOTAL_BYTES
from tl_package_guard import PackageBudget, _safe_xml
from tl_payload_guard import NativePayloadError, validate_native_payloads
from native_payloads import (
    NATIVE_RECORD_REF_ATTRIBUTE, TXBODY_REF_ATTRIBUTE, SHAPE_STYLE_REF_ATTRIBUTE,
    CUSTOM_GEOMETRY_REF_ATTRIBUTE, SHAPE_STYLE_ATTRIBUTE, CUSTOM_GEOMETRY_ATTRIBUTE,
    PAYLOAD_STORE_REFERENCE_PREFIX, _is_native_record_attribute,
)


def preflight_native_references(root, workspace, budget):
    """Validate bounded store bytes and charge repeated references repeatedly.

    The caller shares this budget across all edited SVG pages. Only one temporary
    hydrated element exists at a time, after its encoded allocation is charged.
    """
    from tl_roundtrip import _json
    reference_names = {NATIVE_RECORD_REF_ATTRIBUTE, TXBODY_REF_ATTRIBUTE,
                       SHAPE_STYLE_REF_ATTRIBUTE, CUSTOM_GEOMETRY_REF_ATTRIBUTE}
    if not any(reference_names & set(element.attrib) for element in root.iter()):
        return
    path = Path(workspace) / 'templates/native_payloads.json.gz'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TOTAL_BYTES:
        raise ContractError('Native payload store is not a bounded regular file')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(path.read_bytes())) as stream:
            data = stream.read(MAX_TOTAL_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise ContractError('Invalid compressed native payload store') from exc
    if len(data) > MAX_TOTAL_BYTES:
        raise ContractError('Native payload store expanded byte budget exceeded')
    document = _json(data)
    if not isinstance(document, dict):
        raise ContractError('Invalid native payload store')
    payloads = document.get('payloads', {})
    records = document.get('native_records', {})
    if not isinstance(payloads, dict) or not isinstance(records, dict):
        raise ContractError('Invalid native payload store records')
    xml_budget = budget.setdefault('xml', PackageBudget())
    bytes_budget = budget.setdefault('decoded', [0])
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        attributes = dict(element.attrib)
        record_id = attributes.pop(NATIVE_RECORD_REF_ATTRIBUTE, None)
        if record_id is not None:
            record = records.get(record_id)
            if (not isinstance(record, dict)
                    or any(not isinstance(k, str) or not _is_native_record_attribute(k)
                           or not isinstance(v, str) for k, v in record.items())):
                raise ContractError('Missing or invalid native reference record')
            if set(record) & set(attributes):
                raise ContractError('Conflicting native record attributes')
            attributes.update(record)
        budget['encoded'] = budget.get('encoded', 0) + sum(len(k) + len(v) for k, v in attributes.items()) + len(element.text or '')
        if budget['encoded'] > MAX_TOTAL_BYTES:
            raise ContractError('Native reference aggregate encoded byte budget exceeded')
        transient = etree.Element(element.tag, attributes)
        transient.text = element.text
        for ref_name, inline_name in ((TXBODY_REF_ATTRIBUTE, None),
                                      (SHAPE_STYLE_REF_ATTRIBUTE, SHAPE_STYLE_ATTRIBUTE),
                                      (CUSTOM_GEOMETRY_REF_ATTRIBUTE, CUSTOM_GEOMETRY_ATTRIBUTE)):
            reference = transient.attrib.pop(ref_name, None)
            if reference is None:
                continue
            if not reference.startswith(PAYLOAD_STORE_REFERENCE_PREFIX):
                raise ContractError('Native payload reference points outside the canonical store')
            digest = reference[len(PAYLOAD_STORE_REFERENCE_PREFIX):]
            encoded = payloads.get(digest)
            if not isinstance(encoded, str):
                raise ContractError('Native payload reference missing')
            budget['encoded'] = budget.get('encoded', 0) + len(encoded)
            if budget['encoded'] > MAX_TOTAL_BYTES:
                raise ContractError('Repeated native reference encoded byte budget exceeded')
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ContractError('Invalid native store base64') from exc
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ContractError('Native store reference digest mismatch')
            _safe_xml(raw, xml_budget)
            if xml_budget.xml_nodes > MAX_NODES:
                raise ContractError('Repeated native reference node budget exceeded')
            if inline_name is None:
                transient.text = encoded
                transient.set('data-pptx-encoding', 'base64')
            else:
                transient.set(inline_name, encoded)
        # Records are limited to the upstream record-field allowlist; semantic
        # chart/table/formula JSON cannot be smuggled through a record.
        # The expanded XML-bearing fields receive the same payload checks.
        try:
            validate_native_payloads(transient, bytes_budget)
        except NativePayloadError as exc:
            raise ContractError(str(exc)) from exc
