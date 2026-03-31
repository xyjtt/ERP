from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


PRODUCT_ID_PATTERN = re.compile(r'queryString":"id=(\d+)&operator=edit"')
SKU_STATUS_PATTERN = re.compile(r'"sku_cargoNumber":"([^"]+)","sku_status":(-?\d+)')
STORE_CODE_PATTERNS = [
    re.compile(r'"p-3151":"([^"]+)"'),
    re.compile(r'"p-1398":"([^"]+)"'),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export online 1688 SKU candidates from html snapshots.")
    parser.add_argument(
        "--snapshot-dir",
        default="logs/html_snapshots",
        help="Directory containing html snapshots.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output CSV path.",
    )
    parser.add_argument(
        "--store-name",
        default="速班达家居",
        help="Store name to write in output rows.",
    )
    parser.add_argument(
        "--all-snapshots",
        action="store_true",
        help="Use all snapshots instead of only the latest snapshot per product.",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_store_item_code(text: str) -> str:
    for pattern in STORE_CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def extract_product_id(path: Path, text: str) -> str:
    match = PRODUCT_ID_PATTERN.search(text)
    if match:
        return str(match.group(1) or "").strip()
    # Fallback to file name prefix: <product_id>_<sku>_<timestamp>.html
    name_prefix = path.name.split("_", 1)[0]
    return name_prefix if name_prefix.isdigit() else ""


def collect_candidates(snapshot_dir: Path, store_name: str, *, use_all_snapshots: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    snapshots = sorted(snapshot_dir.glob("*.html"))
    if not use_all_snapshots:
        latest_by_product: dict[str, Path] = {}
        for snapshot in snapshots:
            product_id = snapshot.name.split("_", 1)[0]
            if not product_id.isdigit():
                continue
            existing = latest_by_product.get(product_id)
            if existing is None or snapshot.stat().st_mtime >= existing.stat().st_mtime:
                latest_by_product[product_id] = snapshot
        snapshots = sorted(latest_by_product.values(), key=lambda path: path.name)

    for snapshot in snapshots:
        text = read_text(snapshot)
        product_id = extract_product_id(snapshot, text)
        if not product_id:
            continue
        store_item_code = extract_store_item_code(text)
        for sku_code, status_text in SKU_STATUS_PATTERN.findall(text):
            status = int(status_text)
            if status != 1:
                continue
            normalized_sku = str(sku_code or "").strip()
            if not normalized_sku:
                continue
            dedupe_key = (product_id, normalized_sku)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append(
                {
                    "店铺名称": store_name,
                    "平台": "Alibaba",
                    "商品ID": product_id,
                    "平台店铺商品编码": store_item_code,
                    "线上商品编码": normalized_sku,
                    "处理说明": "全渠道下架",
                    "可替换商品编码": "",
                    "是否换图": "否",
                }
            )

    rows.sort(key=lambda item: (item["商品ID"], item["线上商品编码"]))
    return rows


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "店铺名称",
        "平台",
        "商品ID",
        "平台店铺商品编码",
        "线上商品编码",
        "处理说明",
        "可替换商品编码",
        "是否换图",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    snapshot_dir = Path(args.snapshot_dir).resolve()
    output_path = Path(args.output).resolve()
    if not snapshot_dir.exists():
        raise FileNotFoundError(f"Snapshot directory not found: {snapshot_dir}")

    rows = collect_candidates(
        snapshot_dir,
        str(args.store_name).strip() or "速班达家居",
        use_all_snapshots=bool(args.all_snapshots),
    )
    write_csv(rows, output_path)
    print(
        {
            "snapshot_dir": str(snapshot_dir),
            "output": str(output_path),
            "candidate_count": len(rows),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
