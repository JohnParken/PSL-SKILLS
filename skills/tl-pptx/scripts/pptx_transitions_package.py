"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_transitions import TransitionSummary
import posixpath
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from pptx_transitions_registry import AUDIO_REL_TYPE, Any, CONTENT_TYPES_NS, CONTENT_TYPES_PART, ET, LET, PML_NS, Path, RELATIONSHIPS_NS, WAV_CONTENT_TYPES, posixpath, zipfile

def _slide_relationships_part(slide_part: str) -> str:
    from pptx_transitions import (
        posixpath,
    )
    directory = posixpath.dirname(slide_part)
    filename = posixpath.basename(slide_part)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


def _resolve_slide_relationship_target(
    slide_part: str,
    target: str,
) -> str | None:
    from pptx_transitions import (
        posixpath,
    )
    normalized_target = target.replace("\\", "/")
    if not normalized_target:
        return None
    if normalized_target.startswith("/"):
        resolved = posixpath.normpath(normalized_target.lstrip("/"))
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(slide_part), normalized_target)
        )
    if resolved == ".." or resolved.startswith("../"):
        return None
    return resolved


def _transition_sound_elements(slide_root: Any) -> list[Any]:
    from pptx_transitions import (
        PML_NS,
        _qn,
        _transition_elements,
        transition_carriers,
    )
    sounds: list[Any] = []
    for carrier in transition_carriers(slide_root):
        for transition in _transition_elements(carrier):
            for sound_action in list(transition):
                if sound_action.tag != _qn(PML_NS, "sndAc"):
                    continue
                for start_sound in list(sound_action):
                    if start_sound.tag != _qn(PML_NS, "stSnd"):
                        continue
                    sounds.extend(
                        child
                        for child in list(start_sound)
                        if child.tag == _qn(PML_NS, "snd")
                    )
    return sounds


def _package_part_content_type(
    package: zipfile.ZipFile,
    target_part: str,
) -> str | None:
    """Resolve one package part's MIME type from defaults or overrides."""
    from pptx_transitions import (
        CONTENT_TYPES_NS,
        CONTENT_TYPES_PART,
        ET,
        Path,
        _qn,
    )
    content_types_root = ET.fromstring(package.read(CONTENT_TYPES_PART))
    normalized_target = "/" + target_part.lstrip("/")
    for entry in content_types_root:
        if (
            entry.tag == _qn(CONTENT_TYPES_NS, "Override")
            and entry.get("PartName") == normalized_target
        ):
            return entry.get("ContentType")
    extension = Path(target_part).suffix.lstrip(".").lower()
    for entry in content_types_root:
        if (
            entry.tag == _qn(CONTENT_TYPES_NS, "Default")
            and str(entry.get("Extension") or "").lower() == extension
        ):
            return entry.get("ContentType")
    return None


def _validate_transition_sound_package_parts(
    package: zipfile.ZipFile,
    package_names: set[str],
    slide_part: str,
    slide_xml: bytes,
) -> list[str]:
    """Validate transition-sound relationships and embedded WAV targets."""
    from pptx_transitions import (
        AUDIO_REL_TYPE,
        CONTENT_TYPES_PART,
        ET,
        LET,
        Path,
        RELATIONSHIPS_NS,
        WAV_CONTENT_TYPES,
        _package_part_content_type,
        _qn,
        _resolve_slide_relationship_target,
        _slide_relationships_part,
        _transition_sound_elements,
        parse_source_xml,
    )
    errors: list[str] = []
    slide_root = (
        LET.fromstring(slide_xml)
        if LET is not None
        else parse_source_xml(slide_xml)
    )
    sounds = _transition_sound_elements(slide_root)
    if not sounds:
        return errors

    if CONTENT_TYPES_PART not in package_names:
        return ["transition sound is missing [Content_Types].xml"]

    relationships_part = _slide_relationships_part(slide_part)
    if relationships_part not in package_names:
        return [
            f"transition sound is missing slide relationships: "
            f"{relationships_part}"
        ]
    try:
        relationships_root = ET.fromstring(package.read(relationships_part))
    except (KeyError, ET.ParseError) as exc:
        return [f"unable to read {relationships_part}: {exc}"]

    relationships: dict[str, Any] = {}
    duplicate_ids: set[str] = set()
    for relationship in relationships_root:
        relationship_id = str(relationship.get("Id") or "")
        if not relationship_id:
            continue
        if relationship_id in relationships:
            duplicate_ids.add(relationship_id)
        relationships[relationship_id] = relationship
    if duplicate_ids:
        errors.append(
            f"{relationships_part} contains duplicate relationship id(s): "
            + ", ".join(sorted(duplicate_ids))
        )

    for sound in sounds:
        relationship_id = sound.get(_qn(RELATIONSHIPS_NS, "embed"))
        if not relationship_id:
            errors.append("p:snd must declare a non-empty r:embed")
            continue
        relationship = relationships.get(relationship_id)
        if relationship is None:
            errors.append(
                f"p:snd references missing slide relationship: "
                f"{relationship_id}"
            )
            continue
        if relationship.get("Type") != AUDIO_REL_TYPE:
            errors.append(
                f"transition sound relationship {relationship_id} must use "
                "the OOXML audio relationship type"
            )
        if relationship.get("TargetMode") == "External":
            errors.append(
                f"transition sound relationship {relationship_id} must be internal"
            )
            continue
        target = str(relationship.get("Target") or "")
        target_part = _resolve_slide_relationship_target(slide_part, target)
        if target_part is None:
            errors.append(
                f"transition sound relationship {relationship_id} has an "
                f"invalid target: {target!r}"
            )
            continue
        if Path(target_part).suffix.lower() != ".wav":
            errors.append(
                f"transition sound relationship {relationship_id} must target "
                f"a .wav part: {target_part}"
            )
        if target_part not in package_names:
            errors.append(
                f"transition sound relationship {relationship_id} target is "
                f"missing: {target_part}"
            )
            continue
        content_type = _package_part_content_type(package, target_part)
        if str(content_type or "").lower() not in WAV_CONTENT_TYPES:
            errors.append(
                f"transition sound target {target_part} must declare a WAV "
                f"content type; found {content_type!r}"
            )
        payload = package.read(target_part)
        if not (
            len(payload) >= 12
            and payload[:4] in {b"RIFF", b"RF64"}
            and payload[8:12] == b"WAVE"
        ):
            errors.append(
                f"transition sound target {target_part} is not RIFF/RF64 WAVE"
            )

    return errors


def validate_pptx_transition_package(
    pptx_path: Path,
    *,
    require_use_timings: bool = False,
) -> dict[str, TransitionSummary]:
    """Read back and validate every slide transition in a written PPTX.

    Return summaries keyed by package part name. Raise ``ValueError`` when the
    ZIP, slide transition structure, MCE bindings, or required presentation
    timing metadata is invalid.
    """
    from pptx_transitions import (
        ET,
        _validate_package_use_timings,
        _validate_transition_sound_package_parts,
        read_slide_transition_xml,
        validate_slide_transition_xml,
        zipfile,
    )
    errors: list[str] = []
    summaries: dict[str, TransitionSummary] = {}
    try:
        with zipfile.ZipFile(pptx_path, "r") as package:
            names = package.namelist()
            part_counts: dict[str, int] = {}
            for name in names:
                part_counts[name] = part_counts.get(name, 0) + 1
            duplicate_names = sorted(
                name for name, count in part_counts.items() if count > 1
            )
            if duplicate_names:
                errors.append(
                    "duplicate package parts: " + ", ".join(duplicate_names)
                )

            package_names = set(names)
            slide_names = sorted(
                name
                for name in names
                if name.startswith("ppt/slides/slide")
                and name.endswith(".xml")
            )
            for slide_name in slide_names:
                slide_xml = package.read(slide_name)
                for problem in validate_slide_transition_xml(slide_xml):
                    errors.append(f"{slide_name}: {problem}")
                try:
                    summaries[slide_name] = read_slide_transition_xml(slide_xml)
                except Exception as exc:
                    errors.append(f"{slide_name}: transition read-back failed: {exc}")
                try:
                    sound_errors = _validate_transition_sound_package_parts(
                        package,
                        package_names,
                        slide_name,
                        slide_xml,
                    )
                except Exception as exc:
                    errors.append(
                        f"{slide_name}: transition sound read-back failed: {exc}"
                    )
                else:
                    for problem in sound_errors:
                        errors.append(f"{slide_name}: {problem}")

            if require_use_timings:
                errors.extend(_validate_package_use_timings(package, names))
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        errors.append(f"unable to read PPTX transition package: {exc}")

    if errors:
        raise ValueError("; ".join(errors))
    return summaries


