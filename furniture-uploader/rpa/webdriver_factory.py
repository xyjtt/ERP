from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

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
) -> tuple[Any, bool]:
    resolved_browser_type = resolve_browser_type(
        browser_type=browser_type,
        browser_binary_path=browser_binary_path,
    )
    attached_to_existing_browser = False

    if resolved_browser_type == "edge":
        options = EdgeOptions()
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
    if headless and not debugger_address:
        options.add_argument("--headless=new")
    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()),
        options=options,
    )
    return driver, attached_to_existing_browser


def resolve_edge_driver_path(
    *,
    debugger_address: str = "",
    browser_binary_path: str = "",
) -> str:
    try:
        return EdgeChromiumDriverManager().install()
    except Exception:
        version = detect_edge_version(
            debugger_address=debugger_address,
            browser_binary_path=browser_binary_path,
        )
        if not version:
            raise
        return download_edge_driver(version)


def detect_edge_version(
    *,
    debugger_address: str = "",
    browser_binary_path: str = "",
) -> str:
    if debugger_address:
        version = detect_edge_version_from_debugger(debugger_address)
        if version:
            return version

    binary_path = Path(str(browser_binary_path).strip())
    if binary_path.name:
        version = extract_version_from_text(binary_path.name)
        if version:
            return version

    return ""


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
