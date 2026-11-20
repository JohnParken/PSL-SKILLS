"""Repository-maintenance parity check between shared WPS and ET security primitives."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

_CFB_CONSTANTS = (
    'MAX_FILE_BYTES', 'MAX_DIRECTORY_ENTRIES', 'MAX_DEPTH',
)
_OOXML_CONSTANTS = (
    'MAX_PART_BYTES', 'MAX_PACKAGE_BYTES', 'MAX_MEMBERS', 'MAX_PATH_BYTES',
    'MAX_PATH_COMPONENTS', 'MAX_CENTRAL_BYTES', 'MAX_EOCD_BYTES', 'MAX_ARCHIVE_BYTES',
    'MAX_XML_BYTES', 'MAX_XML_DEPTH', 'MAX_XML_NAMESPACE_URI_CHARS',
    'MAX_XML_ATTRIBUTES_PER_ELEMENT',
)
_DIAGNOSTIC_CONSTANTS = (
    'MAX_DIAGNOSTIC_EVENTS', 'MAX_COVERAGE_LOCATIONS', 'MAX_COVERAGE_LOCATION_KEYS',
)
# Documented intentional capacity limits where ET supports larger workbooks than WPS.
_INTENTIONAL_HIGHER_CAPS = (
    ('wps_cfb', 'MAX_STREAM_BYTES', 8 * 1024 * 1024, 32 * 1024 * 1024),
    ('wps_ooxml', 'MAX_XML_ELEMENTS', 200_000, 2_000_000),
    ('wps_ooxml', 'MAX_XML_EXPANDED_NAME_CHARS', 32 * 1024 * 1024, 128 * 1024 * 1024),
    ('wps_ooxml', 'MAX_XML_ATTRIBUTES', 200_000, 2_000_000),
    ('wps_ooxml', 'MAX_XML_NODES', 400_000, 8_000_000),
)


def _load_isolated_module(skill: str, module_name: str):
    scripts_dir = str(ROOT / 'skills' / skill / 'scripts')
    path = ROOT / 'skills' / skill / 'scripts' / f'{module_name}.py'
    unique_name = f'_parity_{skill}_{module_name}'
    spec = importlib.util.spec_from_file_location(unique_name, path)
    module = importlib.util.module_from_spec(spec)
    old_sys_path = sys.path[:]
    try:
        sys.path.insert(0, scripts_dir)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_sys_path
    return module


class WpsEtSharedPrimitivesParityTests(unittest.TestCase):
    """Guard shared security caps, XML/DTD refusal, and diagnostic budgets across WPS and ET."""

    def test_cfb_ooxml_and_diagnostic_limits_match_and_track_documented_differences(self):
        groups = (
            ('wps_cfb', _CFB_CONSTANTS),
            ('wps_ooxml', _OOXML_CONSTANTS),
            ('wps_diagnostics', _DIAGNOSTIC_CONSTANTS),
        )
        loaded = {}
        for mod_name, names in groups:
            wps_mod = _load_isolated_module('wps', mod_name)
            et_mod = _load_isolated_module('et', mod_name)
            loaded[('wps', mod_name)] = wps_mod
            loaded[('et', mod_name)] = et_mod
            for const in names:
                with self.subTest(module=mod_name, constant=const):
                    self.assertEqual(getattr(wps_mod, const), getattr(et_mod, const))
        for mod_name, const, expected_wps, expected_et in _INTENTIONAL_HIGHER_CAPS:
            with self.subTest(module=mod_name, constant=const):
                self.assertEqual(getattr(loaded[('wps', mod_name)], const), expected_wps)
                self.assertEqual(getattr(loaded[('et', mod_name)], const), expected_et)

    def test_access_modules_keep_matching_public_contract(self):
        wps_access = _load_isolated_module('wps', 'wps_access')
        et_access = _load_isolated_module('et', 'wps_access')
        for name in ('ParseError', 'require', 'diagnostic_context', 'read_input',
                     'container_mode', 'load_compound', 'resolve_chart_policy'):
            with self.subTest(symbol=name):
                self.assertTrue(hasattr(wps_access, name))
                self.assertTrue(hasattr(et_access, name))

    def test_ooxml_fromstring_rejects_doctype_entity_and_budget_equally(self):
        for skill in ('wps', 'et'):
            mod = _load_isolated_module(skill, 'wps_ooxml')
            with self.subTest(skill=skill):
                with self.assertRaises(ValueError):
                    mod.fromstring(b'<!DOCTYPE root [<!ENTITY x "1">]><root>&x;</root>')
                with self.assertRaises(ValueError):
                    mod.fromstring(b'<!ENTITY foo "bar"><root/>')
                budget = mod.XmlBudget()
                budget.nodes = mod.MAX_XML_NODES
                with self.assertRaises(ValueError):
                    mod.fromstring(b'<root><child/></root>', budget=budget)


if __name__ == '__main__':
    unittest.main()
