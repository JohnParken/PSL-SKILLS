"""Recover bounded stored Word textbox text without claiming layout or anchors."""

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAX_TEXTBOX_STORIES = 4096
MAX_TEXTBOX_TEXT_UNITS = 8 * 1024 * 1024


def tag(name):
    return "{" + W + "}" + name


def _textboxes(node):
    """Yield outermost textbox-content subtrees across foreign wrappers."""
    for child in node:
        if child.tag == tag("txbxContent"):
            yield child
            continue
        yield from _textboxes(child)


class TextboxCollector:
    """Collect stored Word textboxes as separate, explicitly unmapped stories."""

    def __init__(self, revisions, blocks, table_collector):
        self.revisions = revisions
        self.blocks = blocks
        self.table_collector = table_collector
        self.stories = []
        self.text_units = 0

    def collect(self, root, part, base_path):
        for element_index, node in enumerate(_textboxes(root)):
            if len(self.stories) >= MAX_TEXTBOX_STORIES:
                raise ValueError("Textbox story count limit exceeded")
            paragraphs = self.blocks(node, self.revisions)
            text = "\n".join(paragraphs)
            self.text_units += len(text.encode("utf-16le")) // 2
            if self.text_units > MAX_TEXTBOX_TEXT_UNITS:
                raise ValueError("Textbox text limit exceeded")
            path = base_path.rstrip("/") + "/txbxContent[" + str(element_index) + "]"
            table_start = len(self.table_collector.tables)
            issue_start = len(self.table_collector.issues)
            self.table_collector.collect(node, part, path)
            table_issues = self.table_collector.issues[issue_start:]
            mapping_issue = {
                "stream": part,
                "location": path,
                "offset": None,
                "code": "textbox_text_recovered_unmapped",
                "message": (
                    "Stored Word textbox text was recovered separately; drawing anchor, "
                    "page position, branch selection and reading order remain unknown"
                ),
            }
            self.stories.append({
                "type": "textboxes",
                "source": {"part": part, "element_index": element_index, "path": path},
                "text": text,
                "paragraphs": paragraphs,
                "tables": self.table_collector.tables[table_start:],
                "references": [],
                "anchor_status": "stored_textbox_subtree_only",
                "page_mapping": "unknown",
                "cp_range": None,
                "status": "partial",
                "issues": [mapping_issue, *table_issues],
            })
