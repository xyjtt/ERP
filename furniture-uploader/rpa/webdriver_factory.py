from __future__ import annotations

from pathlib import Path
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
            service=EdgeService(EdgeChromiumDriverManager().install()),
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
