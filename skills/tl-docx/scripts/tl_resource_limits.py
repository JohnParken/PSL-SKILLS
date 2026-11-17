"""Shared hard resource boundaries, separate from output pagination budgets.

Counters use Unicode characters, not tokens or encoded byte estimates. XML
preflight builds no DOM; report checks use an iterative bounded-depth walk.
"""
from xml.parsers import expat

from tl_contract import ContractError

LIMITS = {
    'xml_nodes': 1_000_000,
    'xml_depth': 256,
    'xml_characters': 32 * 1024 * 1024,
    'report_records': 200_000,
    'report_issues': 50_000,
    'value_nodes': 2_000_000,
    'value_depth': 128,
    'value_characters': 32 * 1024 * 1024,
}


class ResourceLimitError(ContractError):
    """Fail closed without exposing document text or filesystem paths."""

    def __init__(self, resource, limit=None):
        maximum = LIMITS[resource] if limit is None else limit
        super().__init__('resource_limit_exceeded',
                         f'Document resource limit exceeded: {resource} ({maximum}).',
                         {'action': 'reduce_document_complexity', 'resource': resource},
                         retryable=False)


def policy():
    """Bind caches to the actual current policy, including test-injected limits."""
    return {'version': 1, **LIMITS}


def require_xml_parser():
    """Do not weaken the existing Expat safety requirement for preflight."""
    if expat.version_info < (2, 6, 0):
        raise ContractError(
            'dependency_unavailable', 'Expat >=2.6.0 is required for XML resource screening.',
            {'action': 'repair_environment'})


def check_count(resource, amount):
    """Reject a resource count before appending more output objects."""
    if amount > LIMITS[resource]:
        raise ResourceLimitError(resource)


class XMLBudget:  # pylint: disable=too-few-public-methods
    """Aggregate each successfully screened part once per Package instance."""

    def __init__(self):
        self.nodes = 0
        self.characters = 0

    def screen(self, data):
        """Reject excessive complexity before python-docx allocates its DOM."""
        require_xml_parser()
        parser = expat.ParserCreate()
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        nodes, characters, depth = 0, 0, 0

        def text(value):
            nonlocal characters
            characters += len(value)
            check_count('xml_characters', self.characters + characters)

        def node():
            nonlocal nodes
            nodes += 1
            check_count('xml_nodes', self.nodes + nodes)

        def start(name, attributes):
            nonlocal depth
            node()
            depth += 1
            check_count('xml_depth', depth)
            text(name)
            for key, value in attributes.items():
                text(key)
                text(value)

        def end(_name):
            nonlocal depth
            depth -= 1

        def comment(value):
            node()
            text(value)

        def instruction(target, value):
            node()
            text(target)
            text(value)

        def reject(*_args):
            raise ValueError('DTD is not supported in document parts')

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = text
        parser.CommentHandler = comment
        parser.ProcessingInstructionHandler = instruction
        parser.StartDoctypeDeclHandler = reject
        parser.EntityDeclHandler = reject
        parser.ExternalEntityRefHandler = reject
        try:
            parser.Parse(data, True)
        except expat.ExpatError as exc:
            raise ValueError('Malformed XML part') from exc
        self.nodes += nodes
        self.characters += characters


def screen_json(raw, *, hard_limits=None):
    """Bound JSON nesting and value count before json.loads allocates objects.

    This is not a JSON parser or validity check. Escaped strings are skipped;
    json.loads remains responsible for syntax and Unicode validation afterwards.
    """
    node_limit, depth_limit = hard_limits or (LIMITS['value_nodes'], LIMITS['value_depth'])
    depth = nodes = 0
    quoted = escaped = primitive = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
            continue
        if char in b' \t\r\n,:':
            primitive = False
        elif char == 34:
            quoted = True
            primitive = False
            nodes += 1
        elif char in (123, 91):
            depth += 1
            nodes += 1
            primitive = False
            if depth > depth_limit:
                raise ResourceLimitError('value_depth', depth_limit)
        elif char in (125, 93):
            depth -= 1
            primitive = False
        elif not primitive:
            primitive = True
            nodes += 1
        if nodes > node_limit:
            raise ResourceLimitError('value_nodes', node_limit)


def validate_value(value):
    """Check nested JSON-like values without recursion or copying subtrees."""
    nodes = characters = 0
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        check_count('value_nodes', nodes)
        if isinstance(item, str):
            characters += len(item)
            check_count('value_characters', characters)
        elif isinstance(item, (dict, list, tuple)):
            check_count('value_depth', len(stack))
            if isinstance(item, dict):
                children = (child for pair in item.items() for child in pair)
            else:
                children = iter(item)
            stack.append(children)


def validate_report(data):
    """Apply the same report limits to cold extraction and reused caches."""
    records, issues, _coverage = data
    check_count('report_records', len(records))
    check_count('report_issues', len(issues))
    validate_value(data)
