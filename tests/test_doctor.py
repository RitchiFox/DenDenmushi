"""Run the prerequisite checker with synthetic toolchains, never real hardware."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def run_doctor(self, mode="working", sdk=None):
        with tempfile.TemporaryDirectory(prefix="doctor tools with spaces ") as folder:
            root = Path(folder)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            temp_dir = root / "temporary files"
            temp_dir.mkdir()
            trace = root / "typecheck.json"
            commands = {
                "uname": 'if [ "$1" = -m ]; then echo arm64; else echo Darwin; fi\n',
                "sw_vers": 'echo 26.0\n',
            }
            for name, body in commands.items():
                path = bin_dir / name
                path.write_text("#!/bin/sh\n" + body)
                path.chmod(0o755)
            xcrun = bin_dir / "xcrun"
            mock = bin_dir / "xcrun_mock.py"
            mock.write_text('''
import json, os, pathlib, sys
args = sys.argv[1:]
mode = os.environ["DOCTOR_TEST_MODE"]
pathlib.Path(os.environ["TMPDIR"], "xcrun_db").touch()
if args[:1] == ["--find"]:
    sys.exit(1 if mode == "missing" else 0)
# Swift's driver can leave scratch directories even for --version.
scratch = pathlib.Path(os.environ["TMPDIR"]) / "TemporaryDirectory.compiler"
scratch.mkdir(exist_ok=True)
if args == ["swiftc", "--version"]:
    print("Apple Swift version " + ("5.9" if mode == "old" else "6.4"))
    sys.exit(0)
if args[:1] == ["swiftc"] and "-typecheck" in args:
    source = pathlib.Path(args[-1]).read_text()
    pathlib.Path(os.environ["DOCTOR_TEST_TRACE"]).write_text(json.dumps({
        "args": args, "source": source, "sdk": os.environ.get("SDKROOT")
    }))
    if mode == "broken":
        print("error: plugin for module 'SwiftUIMacros' not found", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
sys.exit("unexpected xcrun invocation")
''')
            # A shebang cannot quote an interpreter path containing spaces.
            xcrun.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                             " " + shlex.quote(str(mock)) + ' "$@"\n')
            xcrun.chmod(0o755)
            env = os.environ.copy()
            for name in ("BASH_ENV", "SDKROOT"):
                env.pop(name, None)
            env.update(PATH=str(bin_dir) + os.pathsep + env["PATH"],
                       TMPDIR=str(temp_dir), DOCTOR_TEST_MODE=mode,
                       DOCTOR_TEST_TRACE=str(trace))
            if sdk is not None:
                env["SDKROOT"] = sdk
            result = subprocess.run(["bash", str(ROOT / "scripts/doctor.sh")],
                                    env=env, capture_output=True, text=True)
            call = json.loads(trace.read_text()) if trace.exists() else None
            self.assertEqual(list(temp_dir.iterdir()), [], "probe must clean up")
            return result, call

    def test_rejects_swift6_with_missing_swiftui_macro(self):
        result, call = self.run_doctor("broken")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MISSING: SwiftUI", result.stdout)
        self.assertIn("Xcode / Command Line Tools", result.stdout)
        self.assertIn("SwiftUIMacros", result.stderr)
        self.assertNotIn("OK: SwiftUI", result.stdout)
        self.assertIsNotNone(call)

    def test_typechecks_state_with_build_flags_and_cleans_up(self):
        result, call = self.run_doctor()
        # OBS/HAL presence on the test host must not affect this assertion.
        self.assertIn("OK: SwiftUI", result.stdout)
        self.assertIn("import SwiftUI", call["source"])
        self.assertIn("@State", call["source"])
        self.assertIn("-parse-as-library", call["args"])
        self.assertIn("-module-cache-path", call["args"])
        self.assertEqual(call["args"][call["args"].index("-swift-version") + 1], "5")
        self.assertEqual(call["args"][call["args"].index("-target") + 1], "arm64-apple-macosx14.0")

    def test_honors_explicit_sdk_without_changing_it(self):
        result, call = self.run_doctor(sdk="/example/Compatible SDK.sdk")
        self.assertIn("OK: SwiftUI", result.stdout)
        self.assertEqual(call["sdk"], "/example/Compatible SDK.sdk")

    def test_skips_probe_without_supported_compiler(self):
        for mode in ("old", "missing"):
            with self.subTest(mode=mode):
                result, call = self.run_doctor(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MISSING: Swift", result.stdout)
                self.assertIsNone(call)
