# -*- coding: utf-8 -*-
"""Read-only live source preview for one product context."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpa.keyword_manager import KeywordManager
from rpa.keyword_sources import KeywordSourceService
from rpa.product_fit import ProductContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview live 1688 keyword sources without writing data.")
    parser.add_argument("--category", required=True)
    parser.add_argument("--product-name", default="")
    parser.add_argument("--core-keyword", action="append", default=[])
    parser.add_argument("--attribute", action="append", default=[], help="Repeat key=value attributes.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--source-limit", type=int, default=500)
    return parser


def parse_attributes(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        key, separator, item_value = str(value).partition("=")
        if not separator or not key.strip() or not item_value.strip():
            raise ValueError(f"Invalid attribute, expected key=value: {value}")
        result[key.strip()] = item_value.strip()
    return result


def main() -> int:
    args = build_parser().parse_args()
    context = ProductContext(
        category=args.category,
        product_name=args.product_name,
        core_keywords=args.core_keyword,
        attributes=parse_attributes(args.attribute),
    )
    candidates, summaries, fit_metadata = KeywordSourceService().preview(
        context,
        limit_per_source=max(args.limit, args.source_limit),
    )
    ranked = KeywordManager().rank_keywords(candidates, limit=max(1, min(args.limit, 200)))
    output = {
        "read_only": True,
        "score_metadata": KeywordManager().get_score_metadata(),
        "product_fit_metadata": fit_metadata,
        "source_summaries": [summary.to_dict() for summary in summaries],
        "keywords": [keyword.to_dict() for keyword, _score in ranked],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
