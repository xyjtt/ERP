from __future__ import annotations

import sys
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from webdriver_factory import (
    detect_edge_version_from_installation,
    extract_version_from_text,
    find_compatible_cached_edge_driver,
    open_webdriver,
    resolve_browser_type,
    resolve_edge_driver_path,
)


class WebdriverFactoryTests(unittest.TestCase):
    def test_resolve_browser_type_prefers_explicit_value(self) -> None:
        self.assertEqual(resolve_browser_type(browser_type="edge"), "edge")
        self.assertEqual(resolve_browser_type(browser_type="chrome"), "chrome")

    def test_resolve_browser_type_infers_from_binary_name(self) -> None:
        self.assertEqual(
            resolve_browser_type(browser_binary_path="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            "edge",
        )
        self.assertEqual(
            resolve_browser_type(browser_binary_path="C:/Program Files/Google/Chrome/Application/chrome.exe"),
            "chrome",
        )

    def test_extract_version_from_text_reads_edge_version(self) -> None:
        self.assertEqual(
            extract_version_from_text('{"Browser":"Edg/146.0.3856.72"}'),
            "146.0.3856.72",
        )
        self.assertEqual(
            extract_version_from_text("Microsoft Edge Edg/145.1.2.3"),
            "145.1.2.3",
        )

    def test_detect_edge_version_reads_versioned_install_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "Microsoft" / "Edge" / "Application"
            version_dir = install_root / "150.0.4078.83"
            version_dir.mkdir(parents=True)
            (version_dir / "msedge.exe").touch()

            other_program_files = Path(temp_dir) / "OtherProgramFiles"
            other_version_dir = (
                other_program_files
                / "Microsoft"
                / "Edge"
                / "Application"
                / "150.0.4078.96"
            )
            other_version_dir.mkdir(parents=True)
            (other_version_dir / "msedge.exe").touch()

            with patch.dict(
                "os.environ",
                {
                    "PROGRAMFILES": str(other_program_files),
                    "PROGRAMFILES(X86)": "",
                    "LOCALAPPDATA": "",
                },
            ):
                self.assertEqual(
                    detect_edge_version_from_installation(str(install_root / "msedge.exe")),
                    "150.0.4078.83",
                )

    def test_cached_edge_driver_uses_latest_compatible_build(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            for version in ("149.0.4022.98", "150.0.4078.48", "150.0.4078.65"):
                version_dir = cache_root / version
                version_dir.mkdir()
                (version_dir / "msedgedriver.exe").touch()

            resolved = find_compatible_cached_edge_driver(
                "150.0.4078.83",
                cache_roots=(cache_root,),
            )

            self.assertEqual(
                Path(resolved),
                cache_root / "150.0.4078.65" / "msedgedriver.exe",
            )

    @patch("webdriver_factory.resolve_edge_driver_path", return_value="C:/cache/msedgedriver.exe")
    @patch("webdriver_factory.webdriver.Edge")
    def test_profile_launch_disables_crashed_session_restore(self, edge, _resolve_driver) -> None:
        open_webdriver(
            headless=False,
            debugger_address="",
            user_data_dir="D:/profiles/wolai",
            profile_directory="Default",
            browser_binary_path="",
            browser_type="edge",
            page_load_strategy="eager",
        )

        options = edge.call_args.kwargs["options"]
        self.assertIn("--disable-session-crashed-bubble", options.arguments)
        self.assertIn("--hide-crash-restore-bubble", options.arguments)
        self.assertIn("--disable-features=InfiniteSessionRestore", options.arguments)
        self.assertEqual(options.experimental_options["prefs"]["profile.exit_type"], "Normal")
        self.assertTrue(options.experimental_options["prefs"]["profile.exited_cleanly"])

    @patch("webdriver_factory.EdgeChromiumDriverManager")
    @patch("webdriver_factory.find_compatible_cached_edge_driver")
    @patch("webdriver_factory.detect_edge_version", return_value="150.0.4078.83")
    def test_resolve_edge_driver_uses_cache_before_network(
        self,
        _detect_mock,
        cache_mock,
        manager_mock,
    ) -> None:
        cache_mock.return_value = "C:/cache/msedgedriver.exe"

        self.assertEqual(resolve_edge_driver_path(), "C:/cache/msedgedriver.exe")
        manager_mock.assert_not_called()

    @patch("webdriver_factory.EdgeChromiumDriverManager")
    @patch(
        "webdriver_factory.download_edge_driver",
        return_value="C:/cache/150.0.4078.105/msedgedriver.exe",
    )
    @patch("webdriver_factory.find_compatible_cached_edge_driver", return_value="")
    @patch("webdriver_factory.detect_edge_version", return_value="150.0.4078.105")
    def test_resolve_edge_driver_downloads_detected_version_not_latest(
        self,
        _detect_mock,
        _cache_mock,
        download_mock,
        manager_mock,
    ) -> None:
        resolved = resolve_edge_driver_path(debugger_address="127.0.0.1:9306")

        self.assertEqual(resolved, "C:/cache/150.0.4078.105/msedgedriver.exe")
        download_mock.assert_called_once_with("150.0.4078.105")
        manager_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
