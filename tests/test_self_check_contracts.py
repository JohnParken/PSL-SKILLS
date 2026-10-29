"""Regression coverage for checks that report environment readiness only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
HISTORICAL_NAME = "preexisting-invalid.docx"
HISTORICAL_CONTENT = b"preserve this pre-existing task file\n"
CONTRACT_MARKER = "check_kind=environment artifact_validation=not_run"


IMPORT_AND_INVOKE = textwrap.dedent(
    """
    import importlib.util
    import sys

    script_path = sys.argv[1]
    spec = importlib.util.spec_from_file_location("self_check_target", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    """
)


class SelfCheckContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="self-check-contract-", dir=REPOSITORY_ROOT
        )
        self.addCleanup(self._temporary_directory.cleanup)
        self.task_directory = Path(self._temporary_directory.name)
        self.historical_file = self.task_directory / HISTORICAL_NAME
        self.historical_file.write_bytes(HISTORICAL_CONTENT)

    def _script(self, skill: str, name: str) -> Path:
        return REPOSITORY_ROOT / "skills" / skill / "scripts" / name

    def _environment(self) -> dict[str, str]:
        environment = {"PATH": os.environ.get("PATH", os.defpath)}
        if os.name == "nt" and "SYSTEMROOT" in os.environ:
            environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        return environment

    def _run_cli(self, script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [PYTHON, "-I", "-S", "-B", str(script), *arguments],
            cwd=self.task_directory,
            env=self._environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def _run_injected(
        self, script: Path, *, injection: str, invoke: str
    ) -> subprocess.CompletedProcess[str]:
        source = IMPORT_AND_INVOKE + textwrap.dedent(injection) + "\n" + textwrap.dedent(invoke)
        return subprocess.run(
            [PYTHON, "-I", "-S", "-B", "-c", source, str(script)],
            cwd=self.task_directory,
            env=self._environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def _assert_no_new_task_files(self) -> None:
        actual_paths = sorted(
            path.relative_to(self.task_directory).as_posix()
            for path in self.task_directory.rglob("*")
        )
        self.assertEqual(actual_paths, [HISTORICAL_NAME])
        self.assertEqual(self.historical_file.read_bytes(), HISTORICAL_CONTENT)

    def _assert_text_success(
        self, result: subprocess.CompletedProcess[str], success_marker: str
    ) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(CONTRACT_MARKER, result.stdout)
        self.assertIn(success_marker, result.stdout)
        self.assertNotIn("check-failed", result.stdout + result.stderr)
        self.assertNotIn("environment-failed", result.stdout + result.stderr)
        self._assert_no_new_task_files()

    def _assert_text_failure(
        self, result: subprocess.CompletedProcess[str], failure_marker: str, code: int = 1
    ) -> None:
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertIn(CONTRACT_MARKER, result.stderr)
        self.assertIn(failure_marker, result.stderr)
        self._assert_no_new_task_files()

    def _assert_json_check(
        self,
        result: subprocess.CompletedProcess[str],
        *,
        code: int,
        status: str,
        expected_fields: dict[str, object] | None = None,
        forbidden_fields: tuple[str, ...] = ("input", "output"),
    ) -> dict[str, object]:
        self.assertEqual(result.returncode, code, result.stderr or result.stdout)
        self.assertEqual(result.stderr, "")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"check output must be one parseable JSON document: {exc}")
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("status"), status)
        self.assertEqual(payload.get("check_kind"), "environment")
        self.assertEqual(payload.get("artifact_validation"), "not_run")
        for field in forbidden_fields:
            self.assertNotIn(field, payload)
        for field, value in (expected_fields or {}).items():
            self.assertEqual(payload.get(field), value)
        self._assert_no_new_task_files()
        return payload

    def test_docx_reader_check_success_and_runtime_failure(self) -> None:
        script = self._script("docx", "read_docx.py")
        self._assert_text_success(self._run_cli(script, "--check"), "read-check-ok")
        result = self._run_injected(
            script,
            injection="module.check_runtime = lambda: False",
            invoke='raise SystemExit(module.main(["--check"]))',
        )
        self._assert_text_failure(result, "check-failed:")

    def test_docx_automatic_runner_check_success_and_environment_failure(self) -> None:
        script = self._script("docx", "run_docx_task.py")
        self._assert_text_success(self._run_cli(script, "check"), "check-ok")
        result = self._run_injected(
            script,
            injection=(
                "module.task_environment = lambda: {"
                "'status': 'failed', 'errors': [{'check': 'injected', "
                "'reason': 'injected failure'}], 'diagnostics': ''}"
            ),
            invoke='raise SystemExit(module.main(["check"]))',
        )
        self._assert_text_failure(result, "auto-cleanup-check-failed:")

    def test_docx_creation_check_success_and_runtime_failure(self) -> None:
        script = self._script("docx", "create_docx.py")
        self._assert_text_success(self._run_cli(script, "--check"), "check-ok")
        result = self._run_injected(
            script,
            injection="module.check_runtime = lambda: False",
            invoke='raise SystemExit(module.main(["--check"]))',
        )
        self._assert_text_failure(result, "check-failed:")

    def test_docx_archive_check_success_and_runtime_failure(self) -> None:
        script = self._script("docx", "archive_deliverables.py")
        self._assert_text_success(self._run_cli(script, "--check"), "check-ok")
        result = self._run_injected(
            script,
            injection=textwrap.dedent(
                """
                import create_docx
                create_docx.check_runtime = lambda: False
                """
            ),
            invoke='raise SystemExit(module.main(["--check"]))',
        )
        self._assert_text_failure(result, "check-failed:")

    def test_wps_check_success_dependency_failure_and_old_python_status(self) -> None:
        script = self._script("wps", "read_wps.py")
        self._assert_text_success(self._run_cli(script, "--check"), "environment-ok")

        dependency_failure = self._run_injected(
            script,
            injection=textwrap.dedent(
                """
                import builtins
                original_import = builtins.__import__
                def fail_biff8(name, *args, **kwargs):
                    if name == "wps_biff8":
                        raise ImportError("injected dependency failure")
                    return original_import(name, *args, **kwargs)
                builtins.__import__ = fail_biff8
                """
            ),
            invoke='sys.argv = [script_path, "--check"]\nraise SystemExit(module.main())',
        )
        self._assert_text_failure(dependency_failure, "environment-failed:")

        old_python = self._run_injected(
            script,
            injection="module.sys.version_info = (3, 8, 0, 'final', 0)",
            invoke='sys.argv = [script_path, "--check"]\nraise SystemExit(module.main())',
        )
        self._assert_text_failure(old_python, "environment-failed:", code=2)

    def test_ofd_check_text_success_and_handled_json_failures(self) -> None:
        script = self._script("ofd", "read_ofd.py")
        self._assert_text_success(self._run_cli(script, "--check"), "environment-ok")

        failed = self._run_injected(
            script,
            injection="module.environment = lambda: (_ for _ in ()).throw(module.ReadError('injected'))",
            invoke='raise SystemExit(module.main(["--check"]))',
        )
        failure_payload = self._assert_json_check(
            failed,
            code=1,
            status="failed",
            expected_fields={"schema_version": "ofd-read-2"},
        )
        self.assertEqual(failure_payload["diagnostics"][0]["code"], "read_error")

        unsupported = self._run_injected(
            script,
            injection=(
                "module.confined_environment = lambda: "
                "(_ for _ in ()).throw(module.ReadError('unsupported: injected'))"
            ),
            invoke='raise SystemExit(module.main(["--check"]))',
        )
        unsupported_payload = self._assert_json_check(
            unsupported,
            code=4,
            status="unsupported",
            expected_fields={"schema_version": "ofd-read-2"},
        )
        self.assertEqual(unsupported_payload["diagnostics"][0]["code"], "read_error")

    def test_xlsx_check_ready_and_handled_environment_failure(self) -> None:
        script = self._script("xlsx", "stdlib_xlsx.py")
        ready = self._assert_json_check(
            self._run_cli(script, "check"),
            code=0,
            status="ready",
            expected_fields={"dependencies": "standard_library_only"},
        )
        self.assertIn("python", ready)

        failed = self._run_injected(
            script,
            injection=textwrap.dedent(
                """
                def fail_create(cls, spec, appearance=None):
                    raise ValueError("injected environment failure")
                module.Workbook.create = classmethod(fail_create)
                sys.argv = [script_path, "check"]
                """
            ),
            invoke="raise SystemExit(module.main())",
        )
        self._assert_json_check(
            failed,
            code=1,
            status="failed",
            expected_fields={"output_written": False, "message": "injected environment failure"},
        )

    def test_artifact_lifecycle_check_and_handled_environment_failure(self) -> None:
        script = self._script("artifact-lifecycle", "lifecycle.py")
        self._assert_json_check(
            self._run_cli(script, "check"),
            code=0,
            status="environment-ok",
            expected_fields={"task_writable": True},
        )

        failed = self._run_injected(
            script,
            injection="module.environment_check = lambda: (_ for _ in ()).throw(RuntimeError('injected failure'))",
            invoke='raise SystemExit(module.main(["check"]))',
        )
        self._assert_json_check(
            failed,
            code=1,
            status="error",
            expected_fields={"message": "injected failure"},
        )


if __name__ == "__main__":
    unittest.main()
