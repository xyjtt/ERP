from __future__ import annotations

import os
import shutil
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager


def resolve_browser_type(browser_type: str = "", browser_binary_path: str = "") -> str:
    normalized = str(browser_type).strip().lower()
    if normalized in {"chrome", "edge"}:
        return normalized

    binary_name = Path(str(browser_binary_path).strip()).name.lower()
    if "msedge" in binary_name:
        return "edge"
    return "chrome"


def open_webdriver(
    *,
    headless: bool,
    debugger_address: str,
    user_data_dir: str,
    profile_directory: str,
    browser_binary_path: str,
    browser_type: str = "",
    page_load_strategy: str = "normal",
) -> tuple[Any, bool]:
    resolved_browser_type = resolve_browser_type(
        browser_type=browser_type,
        browser_binary_path=browser_binary_path,
    )
    attached_to_existing_browser = False

    if resolved_browser_type == "edge":
        options = EdgeOptions()
        options.page_load_strategy = page_load_strategy
        if browser_binary_path:
            options.binary_location = browser_binary_path
        if debugger_address:
            options.add_experimental_option("debuggerAddress", debugger_address)
            attached_to_existing_browser = True
        else:
            if user_data_dir:
                options.add_argument(f"--user-data-dir={user_data_dir}")
            if profile_directory:
                options.add_argument(f"--profile-directory={profile_directory}")
            configure_profile_session_restore(options)
        if headless and not debugger_address:
            options.add_argument("--headless=new")
        driver = webdriver.Edge(
            service=EdgeService(
                resolve_edge_driver_path(
                    debugger_address=debugger_address,
                    browser_binary_path=browser_binary_path,
                )
            ),
            options=options,
        )
        return driver, attached_to_existing_browser

    options = ChromeOptions()
    options.page_load_strategy = page_load_strategy
    if browser_binary_path:
        options.binary_location = browser_binary_path
    if debugger_address:
        options.add_experimental_option("debuggerAddress", debugger_address)
        attached_to_existing_browser = True
    else:
        if user_data_dir:
            options.add_argument(f"--user-data-dir={user_data_dir}")
        if profile_directory:
            options.add_argument(f"--profile-directory={profile_directory}")
        configure_profile_session_restore(options)
    if headless and not debugger_address:
        options.add_argument("--headless=new")
    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()),
        options=options,
    )
    return driver, attached_to_existing_browser


def configure_profile_session_restore(options: EdgeOptions | ChromeOptions) -> None:
    options.add_argument("--disable-session-crashed-bubble")
    options.add_argument("--hide-crash-restore-bubble")
    options.add_argument("--disable-features=InfiniteSessionRestore")
    options.add_experimental_option(
        "prefs",
        {
            "profile.exit_type": "Normal",
            "profile.exited_cleanly": True,
        },
    )


def resolve_edge_driver_path(
    *,
    debugger_address: str = "",
    browser_binary_path: str = "",
) -> str:
    version = detect_edge_version(
        debugger_address=debugger_address,
        browser_binary_path=browser_binary_path,
    )
    cached_driver = find_compatible_cached_edge_driver(version) if version else ""
    if cached_driver:
        return cached_driver

    if version:
        # webdriver-manager resolves the newest Edge driver, which can be one
        # major version ahead of the browser installed on long-running Windows
        # executors. An attached CDP session requires a matching driver build.
        return download_edge_driver(version)

    return EdgeChromiumDriverManager().install()


def detect_edge_version(
    *,
    debugger_address: str = "",
    browser_binary_path: str = "",
) -> str:
    if debugger_address:
        version = detect_edge_version_from_debugger(debugger_address)
        if version:
            return version

    return detect_edge_version_from_installation(browser_binary_path)


def detect_edge_version_from_installation(browser_binary_path: str = "") -> str:
    install_roots: list[Path] = []
    binary_path = Path(str(browser_binary_path).strip())
    if binary_path.name:
        direct_version = parse_version(binary_path.parent.name)
        if direct_version:
            return direct_version
        install_roots.append(binary_path.parent)
    else:
        for env_name, suffix in (
            ("PROGRAMFILES(X86)", Path("Microsoft/Edge/Application")),
            ("PROGRAMFILES", Path("Microsoft/Edge/Application")),
            ("LOCALAPPDATA", Path("Microsoft/Edge/Application")),
        ):
            base = str(os.getenv(env_name, "")).strip()
            if base:
                install_roots.append(Path(base) / suffix)

    versions: list[str] = []
    seen: set[Path] = set()
    for install_root in install_roots:
        resolved_root = install_root.resolve(strict=False)
        if resolved_root in seen or not install_root.is_dir():
            continue
        seen.add(resolved_root)
        for candidate in install_root.iterdir():
            version = parse_version(candidate.name)
            if version and candidate.is_dir() and (candidate / "msedge.exe").is_file():
                versions.append(version)

    return max(versions, key=version_key, default="")


def find_compatible_cached_edge_driver(
    version: str,
    *,
    cache_roots: Iterable[Path] | None = None,
) -> str:
    normalized_version = parse_version(version)
    if not normalized_version:
        return ""

    if cache_roots is None:
        cache_roots = (
            Path.home() / ".cache" / "furniture-uploader" / "drivers" / "edge",
            Path.home() / ".cache" / "selenium" / "msedgedriver" / "win64",
        )

    compatibility_key = edge_driver_compatibility_key(normalized_version)
    candidates: list[tuple[bool, tuple[int, ...], Path]] = []
    for cache_root in cache_roots:
        if not cache_root.is_dir():
            continue
        for version_dir in cache_root.iterdir():
            candidate_version = parse_version(version_dir.name)
            driver_path = version_dir / "msedgedriver.exe"
            if (
                candidate_version
                and driver_path.is_file()
                and edge_driver_compatibility_key(candidate_version) == compatibility_key
            ):
                candidates.append(
                    (
                        candidate_version == normalized_version,
                        version_key(candidate_version),
                        driver_path,
                    )
                )

    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return str(candidates[0][2])


def parse_version(raw_value: str) -> str:
    value = str(raw_value).strip()
    parts = value.split(".")
    if len(parts) < 3 or any(not part.isdigit() for part in parts):
        return ""
    return ".".join(parts)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def edge_driver_compatibility_key(version: str) -> tuple[int, ...]:
    return version_key(version)[:3]


def detect_edge_version_from_debugger(debugger_address: str) -> str:
    try:
        with urllib.request.urlopen(f"http://{debugger_address}/json/version", timeout=10) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

    return extract_version_from_text(payload)


def extract_version_from_text(raw_value: str) -> str:
    marker = "Edg/"
    text = str(raw_value)
    if marker in text:
        start = text.index(marker) + len(marker)
        end = start
        while end < len(text) and (text[end].isdigit() or text[end] == "."):
            end += 1
        return text[start:end]
    return ""


def download_edge_driver(version: str) -> str:
    cache_dir = Path.home() / ".cache" / "furniture-uploader" / "drivers" / "edge" / version
    target_path = cache_dir / "msedgedriver.exe"
    if target_path.exists():
        return str(target_path)

    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_url = f"https://msedgedriver.microsoft.com/{version}/edgedriver_win64.zip"

    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / "edgedriver_win64.zip"
        with urllib.request.urlopen(archive_url, timeout=60) as response, archive_path.open("wb") as target:
            shutil.copyfileobj(response, target)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extract("msedgedriver.exe", path=temp_root)
        shutil.copy2(temp_root / "msedgedriver.exe", target_path)

    return str(target_path)
