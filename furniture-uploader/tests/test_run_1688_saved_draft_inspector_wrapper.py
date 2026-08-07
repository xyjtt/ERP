from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT_ROOT / "scripts" / "run_1688_saved_draft_inspector.ps1"


@unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
class SavedDraftInspectorWrapperTests(unittest.TestCase):
    def test_empty_expected_shop_is_omitted_from_python_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ERP Root"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            runtime_root = Path(temp_dir) / "Runtime Root"
            runtime_root.mkdir()
            payload = root / "payload.json"
            payload.write_text("{}", encoding="utf-8")
            output = root / "inspection.json"
            (scripts / "inspect_1688_saved_draft.py").write_text(
                textwrap.dedent(
                    """
                    import json
                    from pathlib import Path
                    import sys

                    args = sys.argv[1:]
                    output = Path(args[args.index("--output") + 1])
                    output.write_text(json.dumps(args), encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(WRAPPER),
                    "-ProjectRoot",
                    str(root),
                    "-Payload",
                    str(payload),
                    "-Output",
                    str(output),
                    "-DraftId",
                    "draft-1",
                    "-SharedRuntimeRoot",
                    str(runtime_root),
                    "-PythonExe",
                    sys.executable,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            passed = json.loads(output.read_text(encoding="utf-8"))
            self.assertNotIn("--expected-shop", passed)

    def test_paths_with_drive_backslashes_and_spaces_are_passed_as_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ERP Root With Spaces"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            runtime_root = Path(temp_dir) / "Runtime Root With Spaces"
            runtime_root.mkdir()
            payload = root / "payload folder" / "draft payload.json"
            payload.parent.mkdir()
            payload.write_text("{}", encoding="utf-8")
            output = root / "output folder" / "draft evidence.json"
            fake_inspector = scripts / "inspect_1688_saved_draft.py"
            fake_inspector.write_text(
                textwrap.dedent(
                    """
                    import json
                    from pathlib import Path
                    import sys

                    args = sys.argv[1:]
                    output = Path(args[args.index("--output") + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )

            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WRAPPER),
                "-ProjectRoot",
                str(root),
                "-Payload",
                str(payload),
                "-Output",
                str(output),
                "-DraftId",
                "6a69bee6e4b01cad1b297a52",
                "-ExpectedShop",
                "木刻理想",
                "-AccountKey",
                "muke_lixiang",
                "-ExpectedCdpPort",
                "9306",
                "-SharedRuntimeRoot",
                str(runtime_root),
                "-OfferId",
                "1072868453052",
                "-OfferUrl",
                "https://detail.1688.com/offer/1072868453052.html",
                "-PythonExe",
                sys.executable,
                "-OpenFromManagement",
                "-OfferOnly",
            ]
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            passed = json.loads(output.read_text(encoding="utf-8"))
            marker = json.loads(
                Path(f"{output}.launcher.json").read_text(encoding="utf-8-sig")
            )
            self.assertNotIn("-c", passed)
            self.assertIn("-u", marker["arguments"])
            passed_payload = Path(passed[passed.index("--payload") + 1])
            passed_runtime_root = Path(passed[passed.index("--shared-runtime-root") + 1])
            self.assertTrue(os.path.samefile(passed_payload, payload))
            self.assertEqual(passed[passed.index("--output") + 1], str(output.resolve()))
            self.assertTrue(os.path.samefile(passed_runtime_root, runtime_root))
            self.assertIn("--open-from-management", passed)
            self.assertIn("--offer-only", passed)
            self.assertEqual(
                passed[passed.index("--login-timeout-seconds") + 1],
                "300",
            )
            self.assertIn(" ", str(passed_payload))
            self.assertTrue(passed_payload.drive)
            self.assertEqual(
                marker["artifact_version"],
                "erp_saved_draft_inspector_launcher_v2",
            )
            self.assertEqual(marker["status"], "completed")
            self.assertEqual(marker["stage"], "inspector_exited")
            self.assertTrue(marker["child_started"])
            self.assertIsInstance(marker["session_id"], int)
            self.assertEqual(marker["exit_code"], 0)
            self.assertTrue(marker["output_exists"])
            self.assertFalse(marker["draft_saved"])
            self.assertFalse(marker["offer_submitted"])

    def test_running_marker_exists_before_blocking_inspector_returns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ERP Root"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            runtime_root = Path(temp_dir) / "Runtime Root"
            runtime_root.mkdir()
            payload = root / "payload.json"
            payload.write_text("{}", encoding="utf-8")
            output = root / "artifacts" / "inspection.json"
            (scripts / "inspect_1688_saved_draft.py").write_text(
                textwrap.dedent(
                    """
                    import json
                    from pathlib import Path
                    import sys
                    import time

                    args = sys.argv[1:]
                    time.sleep(1.5)
                    output = Path(args[args.index("--output") + 1])
                    output.write_text(json.dumps({"status": "done"}), encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WRAPPER),
                "-ProjectRoot",
                str(root),
                "-Payload",
                str(payload),
                "-Output",
                str(output),
                "-DraftId",
                "draft-1",
                "-ExpectedShop",
                "木刻理想",
                "-SharedRuntimeRoot",
                str(runtime_root),
                "-PythonExe",
                sys.executable,
            ]
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            marker_path = Path(f"{output}.launcher.json")
            running_marker = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if marker_path.is_file():
                    candidate = json.loads(marker_path.read_text(encoding="utf-8-sig"))
                    if candidate.get("stage") == "inspector_running":
                        running_marker = candidate
                        break
                time.sleep(0.05)

            self.assertIsNotNone(running_marker)
            self.assertEqual(running_marker["status"], "running")
            self.assertIsNone(running_marker["finished_at"])
            self.assertTrue(running_marker["child_started"])
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr or stdout)
            final_marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
            self.assertEqual(final_marker["status"], "completed")
            self.assertEqual(final_marker["stage"], "inspector_exited")

    def test_preflight_failure_updates_existing_launcher_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_root = root / "Runtime"
            runtime_root.mkdir()
            payload = root / "payload.json"
            payload.write_text("{}", encoding="utf-8")
            output = root / "artifacts" / "inspection.json"
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WRAPPER),
                "-ProjectRoot",
                str(root / "missing project"),
                "-Payload",
                str(payload),
                "-Output",
                str(output),
                "-DraftId",
                "draft-1",
                "-ExpectedShop",
                "木刻理想",
                "-SharedRuntimeRoot",
                str(runtime_root),
                "-PythonExe",
                sys.executable,
            ]
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            marker = json.loads(
                Path(f"{output}.launcher.json").read_text(encoding="utf-8-sig")
            )
            self.assertEqual(marker["status"], "failed")
            self.assertEqual(marker["stage"], "preflight_failed")
            self.assertFalse(marker["child_started"])
            self.assertFalse(marker["output_exists"])

    def test_native_stderr_does_not_abort_before_real_exit_code_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ERP Root"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            runtime_root = Path(temp_dir) / "Runtime Root"
            runtime_root.mkdir()
            payload = root / "payload.json"
            payload.write_text("{}", encoding="utf-8")
            output = root / "artifacts" / "inspection.json"
            (scripts / "inspect_1688_saved_draft.py").write_text(
                textwrap.dedent(
                    """
                    import sys

                    print("Traceback (most recent call last):", file=sys.stderr)
                    print("RuntimeError: diagnostic retained", file=sys.stderr)
                    raise SystemExit(7)
                    """
                ),
                encoding="utf-8",
            )
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WRAPPER),
                "-ProjectRoot",
                str(root),
                "-Payload",
                str(payload),
                "-Output",
                str(output),
                "-DraftId",
                "draft-1",
                "-ExpectedShop",
                "shop-1",
                "-SharedRuntimeRoot",
                str(runtime_root),
                "-PythonExe",
                sys.executable,
            ]

            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

            self.assertEqual(completed.returncode, 7)
            marker = json.loads(
                Path(f"{output}.launcher.json").read_text(encoding="utf-8-sig")
            )
            stderr_bytes = Path(f"{output}.stderr.log").read_bytes()
            stderr = stderr_bytes.decode(
                "utf-16" if stderr_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig",
                errors="replace",
            )
            self.assertEqual(marker["status"], "failed")
            self.assertEqual(marker["stage"], "inspector_exited")
            self.assertEqual(marker["exit_code"], 7)
            self.assertEqual(marker["launcher_error"], "")
            self.assertIn("Traceback (most recent call last):", stderr)
            self.assertIn("RuntimeError: diagnostic retained", stderr)


if __name__ == "__main__":
    unittest.main()
