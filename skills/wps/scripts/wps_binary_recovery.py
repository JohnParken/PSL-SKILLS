"""Narrow maximum-mode recovery of independently bounded MS-DOC stories.

Shared stream ownership and resource limits remain fatal. Recovery is explicitly
allowlisted; new parser failures cannot silently become successful partial reads.
"""
from wps_access import ParseError

_HEADER_ERRORS = frozenset({
    "Header slot/section count mismatch", "Invalid header coverage/terminal CP",
    "Invalid header CP range/order", "Missing header story guard paragraph",
    "Header/footer requires content and guard paragraphs",
})
_NOTE_ERRORS = frozenset({
    "Incomplete note Ref/Txt table pair", "Note tables without story text",
    "Malformed note Txt PLC length", "Note Ref/Txt count mismatch",
    "Note text coverage/terminal CP mismatch", "Note text CPs must strictly increase",
    "Invalid or duplicate note anchor CP", "Note text must end with paragraph mark",
    "Auto note anchor requires U+0002",
    "Incomplete comment Ref/Txt/author tables", "Malformed comment Txt PLC",
    "Comment Ref/Txt count mismatch", "Empty comment table coverage mismatch",
    "Author names without comments", "Missing comment authors",
    "Comment author exceeds 55 UTF-16 units", "Invalid comment author UTF-16",
    "Duplicate comment author name", "Invalid comment text CP coverage",
    "Invalid or duplicate comment anchor CP", "Comment initials length exceeds 9",
    "Invalid comment initials UTF-16", "Comment author index out of range",
    "Comment reference/text requires U+0005", "Comment text must end with paragraph mark",
})
_STORY_ERRORS = frozenset({
    "Invalid UTF-16 story text", "Field separator without start or repeated separator",
    "Field end without start", "Unclosed field in story",
})


class _WithoutHeaderSlots:
    """Read-only view hiding only PlcfHdd to retain verified SEPX ownership."""

    def __init__(self, word):
        self._word = word

    def number(self, offset, width=4):
        return 0 if offset == 246 and width == 4 else self._word.number(offset, width)

    def __getattr__(self, name):
        return getattr(self._word, name)


def _issue(exc, story, code):
    return {**exc.location, "code": code, "story_type": story,
            "message": str(exc), "source_status": exc.status,
            "recovery": "stored_text_only_without_verified_mapping"}


def read_mapping(operation, *args, maximum, story, issues, **kwargs):
    """Keep raw story text only for known semantic mapping failures."""
    try:
        return operation(*args, **kwargs)
    except ParseError as exc:
        allowed = _HEADER_ERRORS if story == "headers_footers" else _NOTE_ERRORS
        if not maximum or exc.status != "corrupt" or str(exc) not in allowed:
            raise
        recovered = None
        if story == "headers_footers":
            # These errors occur after section/SEPX validation. Repeat that
            # validation without slots, so later readers still receive ranges.
            recovered = operation(_WithoutHeaderSlots(args[0]), *args[1:], **kwargs)
            recovered["mapping_status"] = "header_slots_unresolved"
        issues.setdefault(story, []).append(_issue(exc, story, "binary_story_mapping_unresolved"))
        return recovered


def read_story(operation, start, end, story, *, maximum):
    """Isolate unreadable optional text; never return malformed field instructions."""
    try:
        return operation(start, end, story)
    except ParseError as exc:
        if not maximum or exc.status != "corrupt" or str(exc) not in _STORY_ERRORS:
            raise
        issue = _issue(exc, story, "binary_related_story_unreadable")
        issue["recovery"] = "story_omitted_main_and_other_stories_retained"
        return {"text": None, "paragraphs": [], "issues": [issue],
                "status": "partial", "partial": True, "paragraph_sources": [],
                "paragraph_source_status": "unavailable_unreadable_story"}
