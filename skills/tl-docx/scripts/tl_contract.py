"""Stable CLI failure categories and replayable read continuations.

Continuation values are data, never shell syntax. The source is the validated
caller-supplied task-relative input, not a cache or installation path.
"""

import argparse
import errno
import json
from zipfile import BadZipFile


class ContractError(ValueError):
    """A failure classified at its actual validation boundary."""

    def __init__(self, code, message, next_action, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.next_action = next_action
        self.retryable = retryable


class UsageError(SystemExit):
    """Preserve parser callers' SystemExit contract without echoing input."""

    def __init__(self):
        super().__init__(2)


class ContractParser(argparse.ArgumentParser):
    """Keep help unchanged, but do not print arbitrary invalid argument values."""

    def error(self, message):
        raise UsageError()


def failure(exc, action):
    """Return bounded diagnostics without exposing filesystem exception paths.

    retryable means a corrected request/environment may be retried as directed,
    not that a write may be automatically repeated. Unknown errors fail closed.
    """
    if isinstance(exc, ContractError):
        code, message, retryable, next_action = exc.code, str(exc), exc.retryable, exc.next_action
    elif isinstance(exc, UsageError):
        code, message, retryable, next_action = ('invalid_arguments', 'Invalid command arguments.', False,
                                                {'action': 'check_help'})
    elif isinstance(exc, ImportError):
        code, message, retryable, next_action = ('dependency_unavailable', 'A required component is unavailable.', False,
                                                {'action': 'repair_environment'})
    elif isinstance(exc, FileNotFoundError):
        code, message, retryable, next_action = ('input_unavailable', 'A required task file is unavailable.', False,
                                                {'action': 'verify_task_input'})
    elif isinstance(exc, PermissionError):
        code, message, retryable, next_action = ('access_denied', 'Required file access was denied.', False,
                                                {'action': 'verify_access'})
    elif isinstance(exc, BadZipFile):
        code, message, retryable, next_action = ('invalid_package', 'The input package is invalid.', False,
                                                {'action': 'verify_task_input'})
    elif isinstance(exc, OSError):
        transient_read = action == 'read' and exc.errno in (errno.EAGAIN, errno.EINTR, errno.EBUSY)
        code, message, retryable, next_action = ('io_error', 'A filesystem operation failed.', transient_read,
                                                {'action': 'retry_read' if transient_read else 'inspect_operation_state'})
    elif isinstance(exc, (ValueError, TypeError, KeyError, IndexError, SyntaxError, OverflowError)):
        code, message, retryable, next_action = ('invalid_input', str(exc), False,
                                                {'action': 'review_input'})
    else:
        code, message, retryable, next_action = ('operation_failed', 'The operation could not be completed.', False,
                                                {'action': 'inspect_operation_state'})
    safe_message = message.encode('utf-8', errors='replace').decode('utf-8')
    result = {'status': 'error', 'action': action, 'error': safe_message, 'error_code': code,
              'retryable': retryable, 'next_action': next_action}
    if safe_message != message:
        result['error_encoding_replaced'] = True
    # Failures remain valid JSON even at the minimum supported read budget.
    # Preserve ordinary diagnostics but explicitly mark oversized text.
    while len((json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')) > 512:
        result['error'] = result['error'][:len(result['error']) // 2]
        result['error_truncated'] = True
    return result


def read_continuation(args, budget, cache=None):
    """Capture all effective read selectors without output/publication effects.

    Omitted options use the CLI's documented defaults. Cursor is filled by the
    page frame. Cache continuation always uses the current token, never --cache.
    """
    parameters = {'max_output_bytes': budget}
    for name in ('format', 'revisions', 'include_related', 'section', 'section_occurrence',
                 'paragraph_range', 'tables'):
        value = getattr(args, name, None)
        if value is not None and not (name == 'format' and value == 'json'):
            parameters[name] = value
    for name in ('content', 'full', 'details', 'headings', 'summary', 'report', 'compact_json', 'fragment_records'):
        if getattr(args, name, False):
            parameters[name] = True
    for name in ('xml_part', 'table_header'):
        value = getattr(args, name, None)
        if value:
            parameters[name] = list(value)
    if cache is not None and 'token' in cache:
        parameters['cache_token'] = cache['token']
    return {'action': 'read', 'source': args.source, 'parameters': parameters}


def continuation_frame(request, cursor):
    """Fill one page's cursor without mutating the shared request template."""
    return {**request, 'parameters': {**request['parameters'], 'cursor': cursor}}
