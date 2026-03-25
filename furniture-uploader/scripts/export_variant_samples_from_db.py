from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from jst_sku_source import JstSkuSource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export 1688 draft variant samples from jst_sku_View.")
    parser.add_argument(
        "--db-config",
        default=str(PROJECT_ROOT / "config" / "database.local.json"),
        help="Path to the database config JSON.",
    )
    parser.add_argument(
        "--shop-name",
        required=True,
        help="Target ERP / publish shop name.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Number of recommended samples to export.",
    )
    parser.add_argument(
        "--spu",
        action="append",
        default=[],
        help="Export records for a specific SPU. Repeat this flag for multiple SPUs.",
    )
    parser.add_argument(
        "--image-source-type",
        default="white-background",
        choices=["white-background", "task-detail"],
        help="Default image source type for exported variants.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "templates" / "1688_variant_db_samples.json"),
        help="Output JSON file path.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    source = JstSkuSource.from_json(args.db_config)
    source.connect()
    try:
        selected_spus = [str(item).strip() for item in args.spu if str(item).strip()]
        if selected_spus:
            records = []
            for spu in selected_spus:
                records.extend(source.fetch_records_for_spu(spu=spu, limit=args.limit))
            payload = source.export_variants(
                records=records,
                output_path=args.output,
                shop_name=args.shop_name,
                image_source_type=args.image_source_type,
            )
        else:
            payload = source.export_recommended_variants(
                output_path=args.output,
                shop_name=args.shop_name,
                limit=args.limit,
                image_source_type=args.image_source_type,
            )
    finally:
        source.close()

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[INFO] Exported {len(payload)} variants to {args.output}")


if __name__ == "__main__":
    main()
