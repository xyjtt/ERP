"""Run one read-only live duplicate probe for guarded 1688 listing candidates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from config_loader import load_json_with_local_override
from listing_duplicate_probe import ListingCandidate, LiveListingDuplicateProbe
from sku_offline_browser import SkuOfflineBrowser


def parse_candidate(value: str) -> ListingCandidate:
    sku, separator, spu = str(value or "").partition("=")
    if not separator or not sku.strip() or not spu.strip():
        raise argparse.ArgumentTypeError("candidate must use SKU=SPU")
    return ListingCandidate(sku=sku.strip(), spu=spu.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only 1688 listing duplicate probe.")
    parser.add_argument("--candidate", action="append", type=parse_candidate, required=True)
    parser.add_argument("--expected-shop", default="木刻理想")
    parser.add_argument("--shop-alias", action="append", default=["广州淘淘家居"])
    parser.add_argument("--draft-limit", type=int, default=20)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_dir = PROJECT_ROOT / "config"
    operator_config = load_json_with_local_override(config_dir / "operator_config.json")
    system_config = load_json_with_local_override(config_dir / "systems" / "1688_sku_offline.json")
    browser = SkuOfflineBrowser(operator_config.get("browser", {}), PROJECT_ROOT)
    try:
        browser.open()
        probe = LiveListingDuplicateProbe(
            browser,
            system_config,
            expected_shop=args.expected_shop,
            expected_shop_aliases=args.shop_alias,
            draft_limit=args.draft_limit,
        )
        report = probe.run(args.candidate)
    finally:
        browser.close()
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
