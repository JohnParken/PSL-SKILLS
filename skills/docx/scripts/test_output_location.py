"""Tests for DOCX output location resolution."""

import builtins
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).parent))

from output_location import OutputLocation, resolve_output_location


def encode_config(parameters: object) -> str:
    return json.dumps({"commonParam": parameters})


class OutputLocationTests(unittest.TestCase):
    def assert_valid(self, config: str, expected_id: str) -> None:
        result = resolve_output_location(config)
        self.assertEqual(
            result,
            OutputLocation(Path(expected_id) / "output", False, None),
        )

    def assert_fallback(self, config: str | None) -> OutputLocation:
        result = resolve_output_location(config)
        self.assertEqual(result.output_dir, Path("."))
        self.assertTrue(result.used_fallback)
        self.assertIsInstance(result.fallback_reason, str)
        self.assertTrue(result.fallback_reason)
        return result

    def test_decoded_common_param_accepts_unrelated_and_extra_fields(self) -> None:
        parameters = [
            {"name": "locale", "value": "en", "extra": True},
            {"name": "locale", "value": "fr"},
            {"name": "idRecord", "value": "rec_A-19", "source": "router"},
        ]
        self.assert_valid(encode_config(parameters), "rec_A-19")

    def test_string_encoded_common_param_is_supported(self) -> None:
        inner = json.dumps([{"name": "idRecord", "value": "folder_7"}])
        self.assert_valid(json.dumps({"commonParam": inner}), "folder_7")

    def test_nonnegative_integers_are_converted_to_decimal(self) -> None:
        self.assert_valid(encode_config([{"name": "idRecord", "value": 0}]), "0")
        self.assert_valid(encode_config([{"name": "idRecord", "value": 125}]), "125")

    def test_missing_and_empty_configuration_fall_back(self) -> None:
        missing = self.assert_fallback(None)
        empty = self.assert_fallback("")
        self.assertEqual(missing.fallback_reason, "missing_router_config")
        self.assertEqual(empty.fallback_reason, "missing_router_config")

    def test_invalid_outer_and_inner_json_fall_back(self) -> None:
        outer = self.assert_fallback("not-json")
        inner = self.assert_fallback(json.dumps({"commonParam": "not-json"}))
        self.assertEqual(outer.fallback_reason, "invalid_router_config_json")
        self.assertEqual(inner.fallback_reason, "invalid_common_param_json")

    def test_invalid_root_and_common_param_shapes_fall_back(self) -> None:
        invalid_configs = [
            "[]",
            "null",
            "{}",
            json.dumps({"commonParam": {"name": "idRecord", "value": "a"}}),
            json.dumps({"commonParam": 4}),
            json.dumps({"commonParam": None}),
        ]
        for config in invalid_configs:
            with self.subTest(config=config):
                self.assert_fallback(config)

    def test_invalid_parameter_entries_fall_back(self) -> None:
        invalid_lists = [
            ["not an object"],
            [{}],
            [{"value": "a"}],
            [{"name": "", "value": "a"}],
            [{"name": 3, "value": "a"}],
            [{"name": "locale"}],
        ]
        for parameters in invalid_lists:
            with self.subTest(parameters=parameters):
                self.assert_fallback(encode_config(parameters))

    def test_missing_or_duplicate_id_record_falls_back(self) -> None:
        missing = self.assert_fallback(encode_config([{"name": "locale", "value": "en"}]))
        duplicate = self.assert_fallback(
            encode_config(
                [
                    {"name": "idRecord", "value": "first"},
                    {"name": "idRecord", "value": "second"},
                ]
            )
        )
        self.assertEqual(missing.fallback_reason, "missing_id_record")
        self.assertEqual(duplicate.fallback_reason, "duplicate_id_record")

    def test_duplicate_json_object_keys_are_rejected_at_both_levels(self) -> None:
        duplicate_outer = '{"commonParam": [], "commonParam": []}'
        duplicate_inner = json.dumps(
            {
                "commonParam": (
                    '[{"name":"idRecord","value":"one","value":"two"}]'
                )
            }
        )
        self.assertEqual(
            self.assert_fallback(duplicate_outer).fallback_reason,
            "invalid_router_config_json",
        )
        self.assertEqual(
            self.assert_fallback(duplicate_inner).fallback_reason,
            "invalid_common_param_json",
        )

    def test_nonstandard_json_constants_are_rejected_at_both_levels(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(level="outer", constant=constant):
                config = f'{{"commonParam":{constant}}}'
                self.assertEqual(
                    self.assert_fallback(config).fallback_reason,
                    "invalid_router_config_json",
                )
            with self.subTest(level="inner", constant=constant):
                inner = f'[{{"name":"idRecord","value":{constant}}}]'
                config = json.dumps({"commonParam": inner})
                self.assertEqual(
                    self.assert_fallback(config).fallback_reason,
                    "invalid_common_param_json",
                )

    def test_invalid_id_record_types_fall_back(self) -> None:
        values = [None, True, False, -1, 1.25, [], {}]
        for value in values:
            with self.subTest(value=value):
                self.assert_fallback(encode_config([{"name": "idRecord", "value": value}]))

    def test_invalid_id_record_paths_and_characters_fall_back(self) -> None:
        values = [
            "",
            " ",
            " leading",
            "trailing ",
            "../record",
            "record/child",
            r"record\child",
            "/absolute",
            "C:record",
            ".",
            "..",
            "has.dot",
            "has:colon",
            "café",
            "x" * 129,
        ]
        for value in values:
            with self.subTest(value=value):
                self.assert_fallback(encode_config([{"name": "idRecord", "value": value}]))

    def test_windows_device_identifiers_are_rejected_case_insensitively(self) -> None:
        device_names = ["CON", "PRN", "AUX", "NUL"]
        device_names.extend(f"COM{number}" for number in range(1, 10))
        device_names.extend(f"LPT{number}" for number in range(1, 10))
        for value in device_names:
            for spelling in (value, value.lower()):
                with self.subTest(value=spelling):
                    self.assert_fallback(
                        encode_config([{"name": "idRecord", "value": spelling}])
                    )

    def test_overly_nested_outer_and_inner_json_fall_back(self) -> None:
        nested_value = "[" * 2000 + "0" + "]" * 2000
        self.assert_fallback(nested_value)
        self.assert_fallback(json.dumps({"commonParam": nested_value}))

    def test_failure_reason_is_stable_and_does_not_echo_configuration(self) -> None:
        secret_value = "private-secret/segment"
        config = encode_config([{"name": "idRecord", "value": secret_value}])
        first = self.assert_fallback(config)
        second = self.assert_fallback(config)
        self.assertEqual(first.fallback_reason, second.fallback_reason)
        self.assertNotIn(secret_value, first.fallback_reason)
        self.assertNotIn("private-secret", first.fallback_reason)

    def test_resolutions_are_independent_across_calls(self) -> None:
        first_config = encode_config([{"name": "idRecord", "value": "alpha"}])
        second_config = encode_config([{"name": "idRecord", "value": "beta"}])
        expected_first = OutputLocation(Path("alpha/output"), False, None)
        expected_second = OutputLocation(Path("beta/output"), False, None)

        self.assertEqual(resolve_output_location(first_config), expected_first)
        self.assertTrue(resolve_output_location("invalid").used_fallback)
        self.assertEqual(resolve_output_location(second_config), expected_second)
        self.assertEqual(resolve_output_location(first_config), expected_first)

    def test_resolution_does_not_access_environment_or_filesystem(self) -> None:
        class ForbiddenEnvironment:
            def __getattribute__(self, name: str) -> object:
                raise AssertionError(f"environment access attempted: {name}")

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("filesystem access attempted")

        config = encode_config([{"name": "idRecord", "value": "record_1"}])
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(os, "environ", ForbiddenEnvironment()))
            stack.enter_context(mock.patch.object(os, "getenv", side_effect=forbidden))
            stack.enter_context(mock.patch.object(builtins, "open", side_effect=forbidden))
            for name in ("open", "mkdir", "makedirs", "stat", "lstat", "remove", "unlink"):
                if hasattr(os, name):
                    stack.enter_context(mock.patch.object(os, name, side_effect=forbidden))
            for name in (
                "open",
                "read_text",
                "read_bytes",
                "write_text",
                "write_bytes",
                "mkdir",
                "stat",
                "exists",
            ):
                stack.enter_context(mock.patch.object(Path, name, side_effect=forbidden))
            self.assertEqual(
                resolve_output_location(config),
                OutputLocation(Path("record_1/output"), False, None),
            )


if __name__ == "__main__":
    unittest.main()
