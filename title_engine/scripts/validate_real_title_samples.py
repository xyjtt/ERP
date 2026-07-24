"""Generate and validate titles for real cabinet SKUs from the read-only work platform."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.database_credentials import load_external_credential
from rpa.keyword_manager import KeywordManager
from rpa.keyword_sources import KeywordSourceService
from rpa.product_fit import ProductContext
from rpa.title_generator import ProductInfo, TitleGenerator


CATEGORY_KEYWORDS = (
    "床头柜",
    "餐边柜",
    "电视柜",
    "鞋柜",
    "衣柜",
    "书柜",
    "斗柜",
    "橱柜",
    "储物柜",
    "文件柜",
    "柜",
)


def external_config_root() -> Path:
    configured = str(os.getenv("YYDD_1688_CONFIG_ROOT", "")).strip()
    if configured:
        return Path(configured)
    return Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "YYDD" / "1688-crawler" / "config"


def load_source_config() -> dict[str, Any]:
    path = external_config_root() / "database.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = dict(payload.get("work_platform_source") or {})
    required = ("server", "database", "driver", "credential_ref")
    missing = [name for name in required if not str(source.get(name) or "").strip()]
    if missing:
        raise RuntimeError("Missing work-platform source settings: " + ", ".join(missing))
    if source.get("read_only") is not True:
        raise RuntimeError("Work-platform source must be explicitly read_only.")
    return source


def source_connection(source: dict[str, Any]):
    import pyodbc

    username, password = load_external_credential(str(source["credential_ref"]))
    connection_string = (
        f"DRIVER={{{source['driver']}}};"
        f"SERVER={source['server']},{int(source.get('port') or 1433)};"
        f"DATABASE={source['database']};UID={username};PWD={password};"
        f"Encrypt={'yes' if source.get('encrypt') else 'no'};"
        f"TrustServerCertificate={'yes' if source.get('trust_server_certificate', True) else 'no'};"
    )
    return pyodbc.connect(connection_string, readonly=True, timeout=15)


def load_samples(limit: int) -> list[dict[str, Any]]:
    source = load_source_config()
    with source_connection(source) as connection:
        cursor = connection.cursor()
        rows = cursor.execute(
            f"""
            SELECT TOP {int(limit)} sku_id, spu, name, category, brand, boardType,
                   properties_value, l, w, h
            FROM dbo.jst_sku_View
            WHERE category LIKE N'%柜%'
              AND ISNULL(sku_id, N'') <> N''
              AND ISNULL(name, N'') <> N''
            ORDER BY autoid DESC
            """
        ).fetchall()
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in rows]


def core_keyword(sample: dict[str, Any]) -> str:
    text = " ".join(str(sample.get(name) or "") for name in ("category", "name"))
    return next((keyword for keyword in CATEGORY_KEYWORDS if keyword in text), "柜")


def sample_attributes(sample: dict[str, Any]) -> dict[str, str]:
    product_text = " ".join(
        str(sample.get(name) or "") for name in ("name", "properties_value")
    )
    dimension_match = re.search(
        r"\d+(?:\.\d+)?(?:\s*[/xX×*]\s*\d+(?:\.\d+)?){2,3}",
        product_text,
    )
    product_dimensions = ""
    if dimension_match:
        product_dimensions = re.sub(
            r"\s*[/xX×*]\s*",
            "x",
            dimension_match.group(0),
        )
    values = {
        "材质": sample.get("boardType"),
        "规格": sample.get("properties_value"),
        "尺寸": product_dimensions or "x".join(
            str(sample.get(name) or "").strip()
            for name in ("l", "w", "h")
            if str(sample.get(name) or "").strip()
        ),
    }
    return {key: str(value).strip() for key, value in values.items() if str(value or "").strip()}


def normalized_brand(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"[-－]", text, maxsplit=1)]
    if len(parts) == 2:
        source_type, brand = parts
        if source_type == "其他品牌" and brand in {"普通大货", "无", "无品牌"}:
            return ""
        if source_type in {"自有品牌", "代理品牌", "其他品牌"}:
            return brand
    return text


def validate_sample(
    sample: dict[str, Any],
    *,
    generator: TitleGenerator,
    source_service: KeywordSourceService,
    keyword_manager: KeywordManager,
) -> dict[str, Any]:
    core = core_keyword(sample)
    attributes = sample_attributes(sample)
    context = ProductContext(
        category=str(sample.get("category") or "柜类"),
        product_name=str(sample.get("name") or ""),
        core_keywords=[core],
        attributes=attributes,
    )
    candidates, summaries, _metadata = source_service.preview(context, limit_per_source=500)
    ranked = keyword_manager.rank_keywords(candidates, limit=50)
    selected_keywords = [
        keyword.word
        for keyword, _score in ranked
        if keyword.score_status == "complete"
        and keyword.source_role == "keyword_candidate"
        and float(keyword.product_fit or 0.0) >= 0.2
    ][:20]
    titles = generator.generate(
        ProductInfo(
            category=str(sample.get("category") or "柜类"),
            core_keywords=[core],
            product_name=str(sample.get("name") or ""),
            keyword_candidates=selected_keywords,
            attributes=attributes,
            brand=normalized_brand(sample.get("brand")) or None,
        ),
        num_titles=30,
    )
    title_values = [item.title for item in titles]
    checks = {
        "candidate_count_30": len(titles) == 30,
        "unique": len(title_values) == len(set(title_values)),
        "length": all(10 <= len(value) <= 30 for value in title_values),
        "core_in_first_8": all(0 <= value.find(core) < 8 for value in title_values),
        "fit_gate": all(
            float(keyword.product_fit or 0.0) >= 0.2
            for keyword, _score in ranked
            if keyword.word in selected_keywords
        ),
    }
    return {
        "sku_id": str(sample.get("sku_id") or ""),
        "spu": str(sample.get("spu") or ""),
        "product_name": str(sample.get("name") or ""),
        "category": str(sample.get("category") or ""),
        "core_keyword": core,
        "selected_source_keywords": selected_keywords,
        "source_summaries": [summary.to_dict() for summary in summaries],
        "candidate_count": len(titles),
        "checks": checks,
        "passed": all(checks.values()),
        "top_titles": [
            {"title": item.title, "length": len(item.title), "structure_score": item.score}
            for item in titles[:3]
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate 20 real cabinet title samples.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit < 1 or args.limit > 100:
        raise ValueError("limit must be between 1 and 100")
    samples = load_samples(args.limit)
    generator = TitleGenerator()
    source_service = KeywordSourceService()
    keyword_manager = KeywordManager()
    results = [
        validate_sample(
            sample,
            generator=generator,
            source_service=source_service,
            keyword_manager=keyword_manager,
        )
        for sample in samples
    ]
    report = {
        "read_only": True,
        "sample_count": len(results),
        "passed_count": sum(1 for result in results if result["passed"]),
        "failed_count": sum(1 for result in results if not result["passed"]),
        "all_passed": len(results) == args.limit and all(result["passed"] for result in results),
        "samples": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "samples"}, ensure_ascii=False, indent=2))
    return 0 if report["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
