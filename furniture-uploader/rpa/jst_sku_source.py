from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from database import DatabaseConfig, _require_pyodbc
from jst_attribute_mapper import (
    build_attribute_snapshot,
    build_description,
    build_platform_attributes,
    build_style_name,
    build_variant_title,
    compute_price_value,
    infer_platform_category_key,
)


@dataclass
class JstSkuRecord:
    raw: dict[str, Any]

    @property
    def sku_id(self) -> str:
        return str(self.raw.get("sku_id") or "").strip()

    @property
    def spu(self) -> str:
        return str(self.raw.get("spu") or "").strip()

    @property
    def category(self) -> str:
        return str(self.raw.get("category") or "").strip()

    def to_variant_payload(
        self,
        *,
        shop_name: str,
        platform: str = "1688",
        channel: str = "1688",
        image_source_type: str = "white-background",
    ) -> dict[str, Any]:
        attributes = build_platform_attributes(self.raw)
        raw_pic = str(self.raw.get("pic") or "").strip()
        return {
            "variant_id": f"DB-{self.sku_id or self.spu}",
            "source_product_id": self.spu or self.sku_id,
            "platform": platform,
            "channel": channel,
            "shop_name": shop_name,
            "title": build_variant_title(self.raw),
            "style_name": build_style_name(self.raw),
            "sell_points": build_description(self.raw),
            "attributes": attributes,
            "attributes_json": build_attribute_snapshot(self.raw),
            "platform_category": infer_platform_category_key(self.category, str(self.raw.get("name") or "")),
            "price_rule_id": "cost_div_0_75_v1",
            "price_value": compute_price_value(self.raw),
            "quantity": "50",
            "publish_route": "platform-direct",
            "publish_mode": "draft",
            "image_source_type": image_source_type,
            "image_source_spu": self.spu,
            "image_source_sku": self.sku_id,
            "main_images": [raw_pic] if raw_pic else [],
            "description": build_description(self.raw),
        }


class JstSkuSource:
    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self.connection = None

    @classmethod
    def from_json(cls, path: str | Path) -> "JstSkuSource":
        return cls(DatabaseConfig.from_json(path))

    def connect(self) -> None:
        pyodbc = _require_pyodbc()
        self.connection = pyodbc.connect(self.config.connection_string(), readonly=True)

    def close(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def fetch_sample_records(self, *, limit: int = 10) -> list[JstSkuRecord]:
        self._require_connection()
        sql = f"""
        SELECT TOP {int(limit)}
            autoid,
            sku_id,
            spu,
            name,
            category,
            cost_price,
            sale_price,
            purchase_price,
            brand,
            boardType,
            properties_value,
            pictureId,
            pic,
            l,
            w,
            h,
            weight,
            enabled,
            status
        FROM jst_sku_View
        ORDER BY autoid DESC
        """
        cursor = self.connection.cursor()
        rows = cursor.execute(sql).fetchall()
        columns = [column[0] for column in cursor.description]
        return [JstSkuRecord({columns[index]: row[index] for index in range(len(columns))}) for row in rows]

    def fetch_recommended_records(self, *, limit: int = 3) -> list[JstSkuRecord]:
        candidates = self.fetch_sample_records(limit=120)
        filtered = [
            record
            for record in candidates
            if record.spu
            and "家具" in record.category
            and "配件" not in record.category
            and str(record.raw.get("pictureId") or "").strip()
            and compute_price_value(record.raw) not in {"", "0"}
        ]
        return filtered[:limit]

    def fetch_records_for_spu(self, *, spu: str, limit: int = 3) -> list[JstSkuRecord]:
        self._require_connection()
        sql = f"""
        SELECT TOP {int(limit)}
            autoid,
            sku_id,
            spu,
            name,
            category,
            cost_price,
            sale_price,
            purchase_price,
            brand,
            boardType,
            properties_value,
            pictureId,
            pic,
            l,
            w,
            h,
            weight,
            enabled,
            status
        FROM jst_sku_View
        WHERE spu = ?
        ORDER BY autoid DESC
        """
        cursor = self.connection.cursor()
        rows = cursor.execute(sql, spu).fetchall()
        columns = [column[0] for column in cursor.description]
        return [JstSkuRecord({columns[index]: row[index] for index in range(len(columns))}) for row in rows]

    def export_variants(
        self,
        *,
        records: list[JstSkuRecord],
        output_path: str | Path,
        shop_name: str,
        image_source_type: str = "white-background",
    ) -> list[dict[str, Any]]:
        payload = [
            record.to_variant_payload(
                shop_name=shop_name,
                image_source_type=image_source_type,
            )
            for record in records
        ]
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def export_recommended_variants(
        self,
        *,
        output_path: str | Path,
        shop_name: str,
        limit: int = 3,
        image_source_type: str = "white-background",
    ) -> list[dict[str, Any]]:
        records = self.fetch_recommended_records(limit=limit)
        return self.export_variants(
            records=records,
            output_path=output_path,
            shop_name=shop_name,
            image_source_type=image_source_type,
        )

    def _require_connection(self) -> None:
        if not self.connection:
            raise RuntimeError("Database connection is not open.")
