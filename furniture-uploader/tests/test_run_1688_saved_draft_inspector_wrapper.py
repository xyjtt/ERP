from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT_ROOT / "scripts" / "run_1688_saved_draft_inspector.ps1"


@unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
class SavedDraftInspectorWrapperTests(unittest.TestCase):
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
            passed_payload = Path(passed[passed.index("--payload") + 1])
            passed_runtime_root = Path(passed[passed.index("--shared-runtime-root") + 1])
            self.assertTrue(os.path.samefile(passed_payload, payload))
            self.assertEqual(passed[passed.index("--output") + 1], str(output.resolve()))
            self.assertTrue(os.path.samefile(passed_runtime_root, runtime_root))
            self.assertIn("--open-from-management", passed)
            self.assertIn(" ", str(passed_payload))
            self.assertTrue(passed_payload.drive)
            self.assertEqual(marker["exit_code"], 0)
            self.assertTrue(marker["output_exists"])
            self.assertFalse(marker["draft_saved"])
            self.assertFalse(marker["offer_submitted"])


if __name__ == "__main__":
    unittest.main()
