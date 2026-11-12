"""ET protocol frame budgets and current failure guidance; synthetic only."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import et_cli
import et_cli_protocol
from wps_access import ParseError, diagnostic_context
from wps_biff8_records import frame_records
from wps_biff8_errors import ResourceLimitError


class ProtocolBudgetTests(unittest.TestCase):
    def invoke(self, result=None, *, command='read', failure=None, identity=7, budget=512):
        request = {'protocol': et_cli.PROTOCOL, 'id': identity, 'operation': command,
                   'parameters': {'raw': b'x', 'source': 'sample.et'}}
        stdin = type('Input', (), {'buffer': io.BytesIO(et_cli_protocol.dumps(request))})()
        stdout = type('Output', (), {'buffer': io.BytesIO()})()
        with (
            patch.object(sys, 'argv', ['et_cli.py', command]),
            patch.object(sys, 'stdin', stdin),
            patch.object(sys, 'stdout', stdout),
            patch.object(et_cli, 'MAX_FRAME', budget),
            patch.object(et_cli_protocol, 'MAX_FRAME', budget),
            patch.object(et_cli, 'read_bytes', return_value=result, side_effect=failure),
        ):
            code = et_cli.main()
        raw = stdout.buffer.getvalue()
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual(raw.count(b'\n'), 1)
        self.assertLessEqual(len(raw.rstrip(b'\n')), budget)
        return code, et_cli_protocol.loads(raw.rstrip(b'\n'))

    def test_base64_expansion_returns_structured_failure(self):
        code, reply = self.invoke({'status': 'ok', 'value': 'x' * 400})
        self.assertEqual(code, 1)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['id'], 7)
        self.assertEqual(reply['error']['status'], 'resource_limit')
        self.assertNotIn('result', reply)

    def test_exact_raw_budget_and_one_byte_over(self):
        empty = {'protocol': et_cli.PROTOCOL, 'id': 7, 'ok': True,
                 'result': {'extraction': b'', 'exit_code': 0}}
        limit = (512 - len(et_cli_protocol.dumps(empty))) // 4 * 3
        base = {'status': 'ok', 'value': ''}
        size = len(et_cli.encode_result(base).encode('utf-8'))
        for extra in (0, 1):
            with self.subTest(extra=extra):
                value = {**base, 'value': 'x' * (limit - size + extra)}
                code, reply = self.invoke(value)
                if extra:
                    self.assertEqual(code, 1)
                    self.assertFalse(reply['ok'])
                else:
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(reply['result']['extraction']), value)

    def test_multibyte_and_partial_preserved_without_compaction(self):
        value = {'status': 'partial', 'value': '中文', 'raw_hex': '1234', 'issues': []}
        code, reply = self.invoke(value)
        self.assertEqual(code, 3)
        self.assertEqual(reply['result']['exit_code'], 3)
        self.assertEqual(json.loads(reply['result']['extraction']), value)

    def test_large_metadata_returns_structured_failure(self):
        result = {'schema_version': 'biff8_readonly_v1', 'biff_parsed': True,
                  'status': 'partial', 'sheets': [{'name': 'Data'}],
                  'issues': [{'message': 'x' * 2000}]}
        code, reply = self.invoke(result, command='metadata', budget=1024)
        self.assertEqual(code, 1)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['error']['status'], 'resource_limit')
        self.assertEqual(reply['error']['location']['stage'], 'protocol_output')

    def test_large_failure_diagnostics_are_explicitly_omitted(self):
        failure = ParseError('unsupported', {'large': 'x' * 2000}, 'x' * 2000)
        code, reply = self.invoke(failure=failure, budget=1024)
        self.assertEqual(code, 1)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['id'], 7)
        self.assertTrue(reply['error']['location']['diagnostics_omitted'])

    def test_small_failure_diagnostics_preserve_original_fields(self):
        failure = ParseError('unsupported', {'stage': 'container'}, 'unknown')
        code, reply = self.invoke(failure=failure)
        self.assertEqual(code, 1)
        self.assertEqual(reply['error']['status'], 'unsupported')
        self.assertEqual(reply['error']['location'], {'stage': 'container'})
        self.assertEqual(reply['error']['message'], 'unknown')

    def test_invalid_request_id_not_echoed_as_success(self):
        code, reply = self.invoke(identity='invalid')
        self.assertEqual(code, 1)
        self.assertFalse(reply['ok'])
        self.assertIsNone(reply['id'])

    def test_final_response_serialization_failure_is_caught(self):
        original = et_cli.dumps
        def failing_dumps(value):
            if value.get('ok') and value.get('result', {}).get('extraction'):
                raise ValueError('simulated final framing failure')
            return original(value)
        with patch.object(et_cli, 'dumps', side_effect=failing_dumps):
            code, reply = self.invoke({'status': 'ok'})
        self.assertEqual(code, 1)
        self.assertEqual(reply['error']['location']['stage'], 'protocol_output')


class DiagnosticGuidanceTests(unittest.TestCase):
    def test_stream_limit_message_uses_actual_budget(self):
        with self.assertRaises(ResourceLimitError) as caught:
            frame_records(b'x' * 5, max_stream_bytes=4, max_records=10)
        self.assertEqual(str(caught.exception), 'BIFF stream exceeds configured limit of 4 bytes')

    def test_dxf_guidance_has_no_obsolete_strict_refusal(self):
        context = diagnostic_context('corrupt', {'record_id': '0x88d'})
        self.assertEqual(context['record_name'], 'DXF')
        self.assertIn('compatibility options do not change policy', context['policy_scope'])
        self.assertNotIn('strict refuses', context['policy_scope'])


if __name__ == '__main__':
    unittest.main()
