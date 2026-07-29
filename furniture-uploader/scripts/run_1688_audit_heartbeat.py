from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from sku_replace_audit import SkuReplaceAuditRepository
from stop_sale_audit import StopSaleAuditRepository, resolve_stop_sale_app_config


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send one bounded 1688 audit heartbeat")
    parser.add_argument("--kind", choices=("stop_sale", "sku_replace"), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--shared-runtime-root", required=True)
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    config = resolve_stop_sale_app_config(args.shared_runtime_root)
    repository = (
        StopSaleAuditRepository(config)
        if args.kind == "stop_sale"
        else SkuReplaceAuditRepository(config)
    )
    repository.heartbeat_run(args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
