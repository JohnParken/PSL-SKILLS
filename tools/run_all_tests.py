"""Run repository regression suites in separate processes; never package tests.

Use a compatible interpreter with -I -S -B. Optional report directory must be new.
A skipped test is recorded, not silently presented as complete behavior coverage.
"""
import argparse
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from xml.parsers import expat

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args(argv)
    if args.report_dir is not None:
        args.report_dir.mkdir(parents=True, exist_ok=False)
    directories = sorted((ROOT / "skills").glob("*/scripts/test"))
    directories += [ROOT / "tests", ROOT / "tests/pptx"]
    reports = []
    for directory in directories:
        relative = directory.relative_to(ROOT).as_posix()
        command = [sys.executable, "-I", "-S", "-B", str(ROOT / "tools/run_skill_tests.py"), str(directory)]
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        collected = re.search(r"Collected ([0-9]+) tests", result.stdout)
        skipped = re.search(r"skipped=([0-9]+)", result.stderr)
        item = {"suite": relative, "exit_code": result.returncode,
                "tests": int(collected[1]) if collected else 0,
                "skipped": int(skipped[1]) if skipped else 0,
                "seconds": round(time.monotonic() - started, 3)}
        reports.append(item)
        if args.report_dir is not None:
            name = relative.replace("/", "_")
            for channel in ("stdout", "stderr"):
                (args.report_dir / (name + "." + channel + ".txt")).write_text(getattr(result, channel), encoding="utf-8")
        elif result.returncode:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    report = {"platform": platform.platform(), "python": platform.python_version(), "expat": expat.EXPAT_VERSION,
              "suites": reports, "tests": sum(item["tests"] for item in reports),
              "skipped": sum(item["skipped"] for item in reports),
              "ok": all(item["exit_code"] == 0 and item["tests"] > 0 for item in reports),
              "limitations": "Regression results only; not all-platform, client visual or production host acceptance."}
    if args.report_dir is not None:
        (args.report_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
