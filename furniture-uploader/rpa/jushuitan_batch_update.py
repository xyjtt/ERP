from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


ALLOWED_PLATFORMS = {"抖音", "快手", "拼多多", "淘宝", "天猫", "小红书"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="聚水潭批量将在线商品编码更新为停产/下架编码。"
    )
    parser.add_argument("--template", required=True, help="输入 Excel 数据源路径")
    parser.add_argument(
        "--output", default="logs/jushuitan_batch_update_result.csv", help="保存查询结果的输出 CSV"
    )
    parser.add_argument(
        "--config",
        default="config/systems/jushuitan_batch_update.json",
        help="批量更新任务的 selector 配置 JSON",
    )
    parser.add_argument(
        "--date",
        default=str(date.today()),
        help="统计日期新过滤值，默认今天，格式 YYYY-MM-DD 或 2026-03-18",
    )
    parser.add_argument(
        "--platform",
        default="抖音",
        choices=sorted(ALLOWED_PLATFORMS),
        help="聚水潭平台筛选值",
    )
    parser.add_argument(
        "--new-code",
        default="txcj",
        help="要批量填充的停产下架编码，例如 txcj",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="是否无头模式运行浏览器（测试可用，但建议调试时不开头）。",
    )
    parser.add_argument(
        "--login-manual",
        action="store_true",
        help="手动登录聚水潭（默认自动登录）。",
    )
    parser.add_argument(
        "--username",
        default="",
        help="自动登录聚水潭的账号（可选）。",
    )
    parser.add_argument(
        "--password",
        default="",
        help="自动登录聚水潭的密码（可选）。",
    )
    return parser.parse_args()


def _read_data_source(template_path: Path, target_date: str) -> pd.DataFrame:
    if not template_path.exists():
        raise FileNotFoundError(f"数据文件不存在: {template_path}")

    df = pd.read_excel(template_path, dtype=str)
    required_columns = [
        "统计日期新",
        "商品编码",
        "处理说明",
        "下架备注",
    ]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"模板缺少必填列: {missing}")

    df = df.assign(**{col: df[col].astype(str).str.strip() for col in required_columns})
    df = df[df["统计日期新"] == target_date]
    df = df[df["处理说明"] == "下架"]
    df = df[~df["下架备注"].str.contains("京喜需下架", na=False)]
    df = df[df["商品编码"].notna() & (df["商品编码"].astype(str).str.strip() != "")]
    return df


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _to_by(selector: dict[str, str]) -> Any:
    if not selector or not selector.get("value"):
        raise ValueError("Selector is not configured")
    by = selector.get("by", "css").lower().strip()
    mapping = {
        "css": By.CSS_SELECTOR,
        "xpath": By.XPATH,
        "id": By.ID,
        "name": By.NAME,
    }
    return mapping.get(by, By.CSS_SELECTOR)


class JushuitanBatchUpdater:
    def __init__(self, config: dict[str, Any], headless: bool = False, wait_seconds: int = 20):
        self.config = config
        self.headless = headless
        self.wait_seconds = wait_seconds
        self.driver = None

    def open(self) -> None:
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )
        self.driver.implicitly_wait(10)

    def close(self) -> None:
        if self.driver:
            self.driver.quit()
            self.driver = None

    def _wait(self, selector: dict[str, str], clickable: bool = False):
        if not self.driver:
            raise RuntimeError("浏览器尚未打开")
        by = _to_by(selector)
        locator = (by, selector["value"])
        wait = WebDriverWait(self.driver, self.wait_seconds)
        if clickable:
            return wait.until(EC.element_to_be_clickable(locator))
        return wait.until(EC.presence_of_element_located(locator))

    def _click(self, selector: dict[str, str]):
        self._wait(selector, clickable=True).click()

    def _input(self, selector: dict[str, str], value: str, clear: bool = True):
        element = self._wait(selector, clickable=True)
        if clear:
            element.clear()
        element.send_keys(value)

    def _click_with_fallback(self, name: str, selector: dict[str, str], fallback_msg: str = "手动点击完成后回车继续"):
        try:
            self._click(selector)
        except Exception as exc:
            print(f"[WARN] 无法通过 selector 点击 {name}: {exc}")
            input(f"请在浏览器手动执行：{fallback_msg}，完成后按回车继续...")

    def _input_with_fallback(self, name: str, selector: dict[str, str], value: str, fallback_msg: str = "手动输入后回车继续"):
        try:
            self._input(selector, value)
        except Exception as exc:
            print(f"[WARN] 无法通过 selector 输入 {name}: {exc}")
            input(f"请在浏览器手动输入 {name}:{value}，完成后按回车继续...")

    def _auto_login(self, username: str, password: str) -> None:
        login_cfg = self.config.get("login", {})
        if not username or not password:
            raise ValueError("自动登录需要 --username 和 --password")
        if not login_cfg.get("username_selector") or not login_cfg.get("password_selector"):
            raise ValueError("配置文件缺少 login.username_selector 或 login.password_selector")
        self._input(login_cfg["username_selector"], username)
        self._input(login_cfg["password_selector"], password)
        if login_cfg.get("submit_selector"):
            self._click(login_cfg["submit_selector"])

    def run(
        self,
        sku_codes: list[str],
        platform: str,
        new_code: str,
        output_path: Path,
        login_manual: bool = True,
        username: str = "",
        password: str = "",
    ) -> None:
        run_cfg = self.config.get("selectors", {})
        login_url = self.config.get("login_url")
        manage_url = self.config.get("manage_page_url")
        if not login_url or not manage_url:
            raise ValueError("配置文件缺少 login_url 或 manage_page_url")

        print(f"[INFO] 打开登录页 {login_url}")
        self.driver.get(login_url)
        time.sleep(2)
        if login_manual:
            input("请在浏览器完成聚水潭登录，登录完成后按回车继续...")
        elif username and password:
            print("[INFO] 尝试自动登录...")
            try:
                self._auto_login(username, password)
                time.sleep(2)
                print("[INFO] 自动登录完成，继续执行。")
            except Exception as exc:
                print(f"[WARN] 自动登录失败: {exc}. 切换到手动登录。")
                input("请在浏览器完成聚水潭登录，登录完成后按回车继续...")
        else:
            input("请在浏览器完成聚水潭登录，登录完成后按回车继续...")

        print(f"[INFO] 打开商品管理页 {manage_url}")
        self.driver.get(manage_url)
        time.sleep(2)

        # 选择 SKU tab
        self._click_with_fallback("SKU tab", run_cfg.get("sku_tab", {}), "请点击 SKU tab")
        time.sleep(0.6)

        # 填写 sku 搜索条件
        self._input_with_fallback("sku 搜索条件", run_cfg.get("sku_search_input", {}), ",".join(sku_codes), "请填写商品编码并按查询")

        # 选择平台
        self._click_with_fallback("平台筛选框", run_cfg.get("platform_filter_dropdown", {}), "请打开平台下拉")
        time.sleep(0.2)
        if "platform_option" in run_cfg:
            option_selector = run_cfg["platform_option"].copy()
            option_selector["value"] = option_selector["value"].replace("{platform}", platform)
            self._click_with_fallback("平台选项", option_selector, "请手动选中平台")
        else:
            self._input_with_fallback("平台搜索", run_cfg.get("platform_search_input", {}), platform, "请手动选平台")
            self._click_with_fallback("平台搜索提交", run_cfg.get("platform_search_submit", {}), "请手动确认平台筛选")

        # 点击查询
        self._click_with_fallback("查询按钮", run_cfg.get("search_button", {}), "请手动点击查询")
        time.sleep(2)

        # 批量更新
        self._click_with_fallback("批量更新按钮", run_cfg.get("batch_update_button", {}), "请手动点击批量更新")
        time.sleep(0.8)

        # 选择批量填充
        self._click_with_fallback("批量填充", run_cfg.get("batch_fill_mode", {}), "请手动选择批量填充")
        time.sleep(0.5)

        # 输入停产下架编码
        self._input_with_fallback("批量填充值", run_cfg.get("batch_fill_value", {}), new_code, "请手动输入停产下架编码")

        # 确认提交
        self._click_with_fallback("确认提交", run_cfg.get("batch_fill_confirm", {}), "请手动确认提交")
        time.sleep(1.2)

        # 读取提示信息并打印
        msg = ""
        if run_cfg.get("batch_fill_message"):
            msg = self._wait(run_cfg["batch_fill_message"]).text
        print(f"[INFO] 批量填充提示: {msg}")
        with open(output_path.with_suffix(".message.txt"), "w", encoding="utf-8") as f:
            f.write(f"message: {msg}\n")

        # 重新查询并保存结果
        self._click(run_cfg["search_button"])
        time.sleep(2)
        rows = self.driver.find_elements(
            _to_by(run_cfg["result_row"]), run_cfg["result_row"]["value"]
        )
        extracted = []
        for row in rows:
            if run_cfg.get("result_columns"):
                row_data = {}
                for col_name, col_selector in run_cfg["result_columns"].items():
                    try:
                        cell = row.find_element(_to_by(col_selector), col_selector["value"])
                        row_data[col_name] = cell.text.strip()
                    except Exception:
                        row_data[col_name] = ""
                extracted.append(row_data)
            else:
                extracted.append({"row_text": row.text.strip()})

        if extracted:
            pd.DataFrame(extracted).to_csv(output_path, index=False, encoding="utf-8-sig")
            print(f"[INFO] 已保存查询结果：{output_path}")
        else:
            print("[WARN] 查询结果为空，未保存数据。")


def main() -> None:
    args = _parse_args()
    template_path = Path(args.template)
    config_path = Path(args.config)
    output_path = Path(args.output)

    config = _load_json(config_path)
    df = _read_data_source(template_path, args.date)
    if df.empty:
        raise ValueError("当前过滤条件没有待更新的商品编码。")

    sku_codes = sorted({str(code).strip() for code in df["商品编码"].unique() if str(code).strip()})
    if not sku_codes:
        raise ValueError("没有有效商品编码")

    if args.platform not in ALLOWED_PLATFORMS:
        raise ValueError(f"平台必须在 {sorted(ALLOWED_PLATFORMS)} 中")

    print(f"[INFO] 本次筛选到 {len(sku_codes)} 个商品编码，示例: {sku_codes[:5]}")

    updater = JushuitanBatchUpdater(config, headless=args.headless)
    updater.open()
    try:
        updater.run(
            sku_codes=sku_codes,
            platform=args.platform,
            new_code=args.new_code,
            output_path=output_path,
            login_manual=args.login_manual,
            username=args.username,
            password=args.password,
        )
    finally:
        updater.close()


if __name__ == "__main__":
    main()
