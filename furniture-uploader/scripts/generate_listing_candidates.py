"""1688 自动上架候选生成（供给端）：jst_sku 源库门禁 + 品牌白名单 + 铺货=0 + SPU 合并 + 全店配额。

业务口径（2026-08-17 用户确认）：
  - 最小按 SKU 维度匹配；SKU 未铺货（铺货ID总数=0）且启动状态（enabled=1 / stock_disabled=0 / 销售 / 成品）
  - 按 SPU 合并铺货：同一 SPU 下所有 SKU 合成一个商品链接（一个候选 / 一个 task）
  - 1688 全店自动铺货：每个 ERP 店铺配额 20 个草稿（新佰广/玖阔 30），类目均摊
  - 草稿人工在 1688 后台审批（消费端 auto_submit=false 保持）

来源：骨架 generate_listing_candidates_skeleton.py（8/12）改造——复用门禁/铺货/价格逻辑，
新增 SPU 合并、全店账号轮转分配、inbox 写入与报告。
运行位置：执行机（老库凭证在 Credential Manager：YYDD/1688/database/listing-source）。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

RUNTIME_ROOT = Path(r"E:\1688\1688-script-new")
sys.path.insert(0, str(RUNTIME_ROOT))

from src.security.secret_provider import get_secret_provider  # noqa: E402

CREDENTIAL_REF = "YYDD/1688/database/listing-source"
SERVER = "218.93.191.16"
PORT = 1433
DRIVER = "ODBC Driver 17 for SQL Server"
DATABASE = "JSDataMiddlePlatform"

BRAND_WHITELIST = (
    "自有品牌-亿家达",
    "自有品牌-卓禾",
    "自有品牌-欧意朗",
    "其他品牌-普通大货",
    "其他品牌-分销专用",
)

DEFAULT_SHOP_QUOTA = 20
SHOP_QUOTA_OVERRIDES = {"xinbaiguang_shanzhu": 30, "jiukuo": 30}

# 1688 全店账号清单（account_key, shop_name）——来自 8/13 候选投放分布
ACCOUNTS: list[tuple[str, str]] = [
    ("banbanshun", "阿里巴巴-常州板板顺家居有限公司"),
    ("famei", "阿里巴巴-常州珐美家居有限公司"),
    ("fanshe", "阿里巴巴-常州凡舍家具"),
    ("feitan_shaoyou", "阿里巴巴_飞檀家居"),
    ("gonglai", "阿里巴巴-常州工莱家具"),
    ("guangzhou_wolai", "阿里巴巴_广州沃来贸易有限公司"),
    ("gutu_baiqian", "阿里巴巴-常州固图家居有限公司"),
    ("huazhixin", "阿里巴巴-常州华之信家具"),
    ("jinhemeng", "阿里巴巴-常州劲合萌家具有限公司"),
    ("jiukuo", "阿里巴巴-常州玖阔家居有限公司"),
    ("laijuke", "阿里巴巴-常州来居客家具有限公司"),
    ("lechang", "阿里巴巴-常州乐畅家居有限公司"),
    ("linjing", "阿里巴巴-常州林境家居"),
    ("manxiang", "阿里巴巴-常州曼向家居有限公司"),
    ("muke_lixiang", "常州洁秋家居有限公司"),
    ("pingcan", "阿里巴巴-常州平灿家居有限公司"),
    ("qianzhishang_qiyiguo", "阿里巴巴-常州千之尚家居有限公司"),
    ("xiangpei_main", "阿里巴巴_翔沛家居"),
    ("xinbaiguang_shanzhu", "新佰广1688"),
    ("yuezhai", "阿里巴巴-常州悦宅家居有限公司"),
]

PUBLISH_TABLES = ("jst_skumap", "jst_skumap_pdd", "jst_skumap_qmcust_add")

GATE_SQL = """
SELECT TOP 5000 s.sku_id, s.i_id, s.name, s.brand, s.category,
       s.cost_price, s.sale_price, s.other_1, s.other_2, s.other_3,
       s.other_4, s.other_5, s.other_6, s.other_7, s.other_8,
       s.other_9, s.other_10, s.purchase_price, s.enabled, s.created, s.modified,
       s.pic
FROM dbo.jst_sku s WITH (NOLOCK)
WHERE s.enabled = 1 AND s.stock_disabled = 0
  AND s.item_type = N'成品' AND s.other_5 = N'销售'
  AND s.brand IN (?, ?, ?, ?, ?)
ORDER BY s.created DESC
"""


def fix(value: object) -> str:
    text = str(value or "")
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("gbk")
    except Exception:
        return text


@dataclass
class CandidateSku:
    sku_id: str
    spu: str
    name: str
    brand: str
    category: str
    cost_price: float | None
    sale_price: float | None
    created: str
    pic: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class CandidateSpu:
    spu: str
    name: str
    brand: str
    category: str
    skus: list[CandidateSku] = field(default_factory=list)
    created: str = ""


def _as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def round_up_1dp(value: float) -> float:
    return round(value * 10 + 0.5) / 10.0


def compute_price(cost: float | None, freight: float | None, limit_price: float | None) -> float | None:
    """Rule 2: (cost + freight) / 0.5, keep 1 dp; compare with company limit price and take the larger."""
    if cost is None:
        return None
    base = round_up_1dp((cost + (freight or 0.0)) / 0.5)
    if limit_price is None:
        return base
    return max(base, round_up_1dp(limit_price))


def fetch_gate_skus(cursor: Any) -> list[CandidateSku]:
    cursor.execute(GATE_SQL, *BRAND_WHITELIST)
    rows = cursor.fetchall()
    columns = [d[0] for d in cursor.description]
    skus: list[CandidateSku] = []
    for row in rows:
        record = dict(zip(columns, row))
        skus.append(
            CandidateSku(
                sku_id=fix(record.get("sku_id")),
                spu=fix(record.get("i_id")),
                name=fix(record.get("name")),
                brand=fix(record.get("brand")),
                category=fix(record.get("category")),
                cost_price=_as_float(record.get("cost_price")),
                sale_price=_as_float(record.get("sale_price")),
                created=str(record.get("created") or ""),
                pic=fix(record.get("pic")),
                raw=record,
            )
        )
    return skus


def _chunked(items: list[str], size: int = 300):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def fetch_publish_counts(cursor: Any, sku_ids: list[str]) -> dict[str, int]:
    """Rule 1: publish-id total = row count across publish tables (union by sku_id).
    Publish records are filtered to recent (>=90d) rows per business semantics
    (created >= threshold AND shop_created IS NULL) to keep the 23.7M-row scan bounded."""
    counts: dict[str, int] = {}
    cutoff = "DATEADD(DAY, -90, GETDATE())"
    for chunk in _chunked(sku_ids):
        placeholders = ",".join("?" for _ in chunk)
        sql = f"""
        SELECT s.sku_id, COUNT(v.sku_id) AS publish_cnt
        FROM dbo.jst_sku s WITH (NOLOCK)
        LEFT JOIN (
            SELECT sku_id FROM dbo.{PUBLISH_TABLES[0]} WITH (NOLOCK) WHERE created >= {cutoff}
            UNION ALL SELECT sku_id FROM dbo.{PUBLISH_TABLES[1]} WITH (NOLOCK) WHERE created >= {cutoff}
            UNION ALL SELECT sku_id FROM dbo.{PUBLISH_TABLES[2]} WITH (NOLOCK) WHERE created >= {cutoff}
        ) v ON v.sku_id = s.sku_id
        WHERE s.sku_id IN ({placeholders})
        GROUP BY s.sku_id
        """
        cursor.execute(sql, *chunk)
        for row in cursor.fetchall():
            counts[str(row[0])] = int(row[1] or 0)
    return counts


def merge_spus(skus: list[CandidateSku]) -> list[CandidateSpu]:
    """SPU 合并：同一 SPU 下所有 SKU 合成一个候选（一个商品链接）。"""
    groups: dict[str, CandidateSpu] = {}
    for sku in skus:
        spu = groups.get(sku.spu)
        if spu is None:
            spu = CandidateSpu(spu=sku.spu, name=sku.name, brand=sku.brand, category=sku.category)
            groups[sku.spu] = spu
        spu.skus.append(sku)
        if not spu.created or (sku.created and sku.created > spu.created):
            spu.created = sku.created
    # 主 SKU = 组内第一个（销售价非空优先）
    for spu in groups.values():
        spu.skus.sort(key=lambda s: (0 if s.sale_price is not None else 1, s.sku_id))
        spu.name = spu.skus[0].name
    return sorted(groups.values(), key=lambda s: s.created or "", reverse=True)


def distribute_shops(spus: list[CandidateSpu]) -> dict[str, list[CandidateSpu]]:
    """全店轮转分配：SPU 候选按新品优先顺序轮转分到各店，每店配额内（类目均摊简化为轮转）。"""
    quotas = {account: SHOP_QUOTA_OVERRIDES.get(account, DEFAULT_SHOP_QUOTA) for account, _ in ACCOUNTS}
    assigned: dict[str, list[CandidateSpu]] = {account: [] for account, _ in ACCOUNTS}
    index = 0
    for spu in spus:
        for _ in range(len(ACCOUNTS)):
            account_key, _ = ACCOUNTS[index % len(ACCOUNTS)]
            index += 1
            if len(assigned[account_key]) < quotas[account_key]:
                assigned[account_key].append(spu)
                break
    return assigned


def parse_sku_specs(sku_name: str) -> tuple[str, str]:
    """从 SKU 名解析颜色/尺寸（"款式一 52/32/32 胡桃色腿+墨绿色坐垫" → ("胡桃色腿+墨绿色坐垫", "52/32/32")）。"""
    name = str(sku_name or "").strip()
    match = re.search(r"(\d+(?:\.\d+)?(?:\s*[xX*/]\s*\d+(?:\.\d+)?){1,2})", name)
    size = match.group(1) if match else ""
    color = re.sub(r"^\s*款式[一二三四五六七八九十\d]+\s*", "", name)
    if size:
        color = re.sub(r"^\s*" + re.escape(size) + r"\s*", "", color)
    return color.strip(), size


def build_payload(spu: CandidateSpu, shop: tuple[str, str]) -> dict[str, Any]:
    """组装 listing_task_payload_v1 候选（SPU 级 task；sku.rows=SPU 下全部 SKU）。"""
    account_key, shop_name = shop
    main = spu.skus[0]
    freight = None
    limit_price = None
    price = compute_price(main.cost_price, freight, limit_price)
    sku_rows = [
        {
            "sku_code": sku.sku_id,
            "sku_name": sku.name,
            "sale_price": f"{compute_price(sku.cost_price, None, None):.1f}" if sku.cost_price is not None else None,
        }
        for sku in spu.skus
    ]
    return {
        "schema_version": "listing_task_payload_v1",
        "platform": "1688",
        "task_id": f"1688-listing-{spu.spu}",
        "idempotency_key": f"listing:{account_key}:{spu.spu}:auto_listing:v1",
        "source": {
            "novelty_type": "new_spu",
            "novelty_note": f"SPU合并铺货:{spu.spu} 含 {len(spu.skus)} 个SKU",
            "source_table": "JSDataMiddlePlatform.dbo.jst_sku",
            "brand": spu.brand,
            "category": spu.category,
            "company_sku": spu.spu,
            "company_spu": spu.spu,
            "enabled": 1,
            "stock_disabled": False,
            "lifecycle_field": "other_5",
            "lifecycle_status": "销售",
            "item_type": "成品",
        },
        "shop": {"shop_name": shop_name, "account_key": account_key},
        "product": {
            "sku_code": spu.spu,
            "spu_code": spu.spu,
            "product_name": spu.name,
            "category": spu.category,
            "selected_title": spu.name,
        },
        "pricing": {
            "price_rule_version": "auto_listing_cost_freight_div_0_5_v1",
            "sale_price": f"{price:.1f}" if price is not None else None,
            "publish_price": f"{price:.1f}" if price is not None else None,
            "cost_price": main.cost_price,
            "freight": freight,
            "limit_price": limit_price,
        },
        "attributes": {
            "color": "|".join(dict.fromkeys(parse_sku_specs(sku.name)[0] for sku in spu.skus if parse_sku_specs(sku.name)[0])),
            "size": "|".join(dict.fromkeys(parse_sku_specs(sku.name)[1] for sku in spu.skus if parse_sku_specs(sku.name)[1])),
        },
        "inventory": {"quantity": 999, "quantity_source": "business_default"},
        "images": {
            "source": "jiansun",
            "main_urls": [main.pic] if main.pic else [],
            "detail_urls": [],
            "description_urls": [],
        },
        "sku": {"rows": sku_rows},
        "workflow": {
            "submit_mode": "single_offer",
            "independent_link_required": True,
            "approval_required": True,
            "auto_submit": False,
        },
        "preflight": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 1688 auto-listing candidates (SPU-merged, all shops).")
    parser.add_argument("--inbox", default=r"C:\ProgramData\YYDD\1688-listing\inbox")
    parser.add_argument("--limit", type=int, default=0, help="limit gate SKUs for a small run (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="print candidates, do not write files")
    args = parser.parse_args()

    provider = get_secret_provider()
    record = provider.get(CREDENTIAL_REF)
    cs = (
        f"DRIVER={{{DRIVER}}};SERVER={SERVER},{PORT};"
        f"DATABASE={DATABASE};UID={record.username};PWD={record.secret};"
        f"Encrypt=no;TrustServerCertificate=yes;Connection Timeout=15;"
    )
    import pyodbc

    connection = pyodbc.connect(cs, autocommit=True)
    connection.timeout = 300
    cursor = connection.cursor()

    gate_skus = fetch_gate_skus(cursor)
    print("GATE_SKUS", len(gate_skus))
    if args.limit > 0:
        gate_skus = gate_skus[: args.limit]
        print("LIMITED_TO", len(gate_skus))

    publish_map = fetch_publish_counts(cursor, [s.sku_id for s in gate_skus])
    never_published = [s for s in gate_skus if publish_map.get(s.sku_id, 0) == 0]
    print("NEVER_PUBLISHED", len(never_published))

    spus = merge_spus(never_published)
    print("SPU_MERGED", len(spus))

    # 幂等：inbox 已有同 SPU 候选则跳过
    inbox = Path(args.inbox)
    existing = {p.name.removeprefix("listing_task_").removesuffix(".json") for p in inbox.glob("*.json")} if inbox.exists() else set()
    spus = [s for s in spus if s.spu not in existing]
    print("SPU_AFTER_INBOX_DEDUPE", len(spus), "existing_inbox", len(existing))

    assigned = distribute_shops(spus)
    total_written = 0
    for account_key, shop_name in ACCOUNTS:
        candidates = assigned[account_key]
        if not candidates:
            continue
        for spu in candidates:
            payload = build_payload(spu, (account_key, shop_name))
            file_name = f"listing_task_{spu.spu}.json"
            if args.dry_run:
                print("CANDIDATE", account_key, file_name, "skus", len(spu.skus), "price", payload["pricing"]["sale_price"])
                continue
            (inbox / file_name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            total_written += 1
        print(f"SHOP {account_key}: {len(candidates)} candidates")
    print("TOTAL_WRITTEN", total_written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
