from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from image_asset_api import AIImageAssetClient, ImageAssetApiError
from parser import ProductRecord


VARIANT_MANDATORY_FIELDS = {
    "variant_id",
    "platform",
    "shop_name",
    "title",
    "price_value",
    "platform_category",
}


@dataclass
class ReleaseVariant:
    variant_id: str
    source_product_id: str
    platform: str
    shop_id: str
    shop_name: str
    operator_id: str
    operator_name: str
    channel: str
    title: str
    style_name: str
    sell_points: str
    main_images: list[str]
    sku_images: list[str]
    detail_images: list[str]
    attributes: dict[str, Any]
    detail_template_id: str
    attribute_template_id: str
    price_rule_id: str
    price_value: str
    cost_price: str
    market_price: str
    price_tail_rule: str
    publish_route: str
    route_reason: str
    publish_mode: str
    image_source_type: str
    image_source_spu: str
    image_source_sku: str
    image_candidate_id: str
    image_selection_rule: str
    status: str
    version_no: str
    created_at: str
    updated_at: str
    platform_category: str
    quantity: str
    store_label: str
    ship_from_template: str
    freight_template: str
    ship_time_template: str
    length_cm: str
    width_cm: str
    height_cm: str
    weight_g: str
    description: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ReleaseVariant":
        normalized = {str(key).strip(): value for key, value in payload.items()}
        missing = sorted(
            field_name
            for field_name in VARIANT_MANDATORY_FIELDS
            if not str(normalized.get(field_name, "")).strip()
        )
        if missing:
            raise ValueError(f"Missing mandatory variant fields: {', '.join(missing)}")

        return cls(
            variant_id=str(normalized.get("variant_id", "")).strip(),
            source_product_id=str(normalized.get("source_product_id", "")).strip(),
            platform=str(normalized.get("platform", "")).strip(),
            shop_id=str(normalized.get("shop_id", "")).strip(),
            shop_name=str(normalized.get("shop_name", "")).strip(),
            operator_id=str(normalized.get("operator_id", "")).strip(),
            operator_name=str(normalized.get("operator_name", "")).strip(),
            channel=str(normalized.get("channel", normalized.get("platform", ""))).strip(),
            title=str(normalized.get("title", "")).strip(),
            style_name=str(normalized.get("style_name", "")).strip(),
            sell_points=str(normalized.get("sell_points", "")).strip(),
            main_images=_normalize_string_list(normalized.get("main_images", [])),
            sku_images=_normalize_string_list(normalized.get("sku_images", [])),
            detail_images=_normalize_string_list(normalized.get("detail_images", [])),
            attributes=_normalize_attributes(normalized.get("attributes", {})),
            detail_template_id=str(normalized.get("detail_template_id", "")).strip(),
            attribute_template_id=str(normalized.get("attribute_template_id", "")).strip(),
            price_rule_id=str(normalized.get("price_rule_id", "")).strip(),
            price_value=str(normalized.get("price_value", "")).strip(),
            cost_price=str(normalized.get("cost_price", "")).strip(),
            market_price=str(normalized.get("market_price", "")).strip(),
            price_tail_rule=str(normalized.get("price_tail_rule", "")).strip(),
            publish_route=str(normalized.get("publish_route", "")).strip(),
            route_reason=str(normalized.get("route_reason", "")).strip(),
            publish_mode=str(normalized.get("publish_mode", "draft")).strip(),
            image_source_type=str(normalized.get("image_source_type", "")).strip(),
            image_source_spu=str(normalized.get("image_source_spu", "")).strip(),
            image_source_sku=str(normalized.get("image_source_sku", "")).strip(),
            image_candidate_id=str(normalized.get("image_candidate_id", "")).strip(),
            image_selection_rule=str(normalized.get("image_selection_rule", "")).strip(),
            status=str(normalized.get("status", "draft")).strip(),
            version_no=str(normalized.get("version_no", "1")).strip(),
            created_at=str(normalized.get("created_at", "")).strip(),
            updated_at=str(normalized.get("updated_at", "")).strip(),
            platform_category=str(normalized.get("platform_category", "")).strip(),
            quantity=str(normalized.get("quantity", "999")).strip() or "999",
            store_label=str(normalized.get("store_label", "")).strip(),
            ship_from_template=str(normalized.get("ship_from_template", "")).strip(),
            freight_template=str(normalized.get("freight_template", "")).strip(),
            ship_time_template=str(normalized.get("ship_time_template", "")).strip(),
            length_cm=str(normalized.get("length_cm", "")).strip(),
            width_cm=str(normalized.get("width_cm", "")).strip(),
            height_cm=str(normalized.get("height_cm", "")).strip(),
            weight_g=str(normalized.get("weight_g", "")).strip(),
            description=str(normalized.get("description", "")).strip(),
            raw=normalized,
        )


def load_release_variants(path: str | Path) -> list[ReleaseVariant]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows = payload.get("variants", [])
        else:
            rows = payload
    elif suffix in {".csv", ".xlsx", ".xls"}:
        try:
            import pandas as pd
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "Missing dependency 'pandas'. Install requirements before loading template files."
            ) from exc

        if suffix == ".csv":
            dataframe = pd.read_csv(file_path, dtype=str).fillna("")
        else:
            dataframe = pd.read_excel(file_path, dtype=str).fillna("")
        rows = []
        for row in dataframe.to_dict(orient="records"):
            normalized = {str(key).strip(): value for key, value in row.items()}
            if any(str(value).strip() for value in normalized.values()):
                rows.append(normalized)
    else:
        raise ValueError(f"Unsupported variant format: {suffix}")

    return [ReleaseVariant.from_payload(dict(row)) for row in rows]


def build_products_from_variants(
    variants: list[ReleaseVariant],
    *,
    image_client: AIImageAssetClient | None,
    project_root: str | Path,
) -> list[ProductRecord]:
    project_root = Path(project_root)
    products: list[ProductRecord] = []
    for variant in variants:
        image_enrichment_error = ""
        try:
            local_main_images, local_detail_images = _resolve_variant_images(
                variant,
                image_client=image_client,
                project_root=project_root,
            )
        except (ImageAssetApiError, ValueError) as exc:
            local_main_images, local_detail_images = [], []
            image_enrichment_error = str(exc)
        products.append(
            ProductRecord.from_row(
                {
                    "task_id": variant.variant_id,
                    "channel": variant.channel or variant.platform,
                    "store_name": variant.shop_name,
                    "store_label": variant.store_label or variant.shop_name,
                    "outer_sku": variant.image_source_sku or variant.source_product_id or variant.variant_id,
                    "title": variant.title,
                    "subtitle": variant.sell_points,
                    "brand": str(variant.attributes.get("brand", "")).strip(),
                    "material": str(variant.attributes.get("material", variant.attributes.get("材质", ""))).strip(),
                    "size": str(variant.attributes.get("size", variant.attributes.get("尺寸", ""))).strip(),
                    "color": str(variant.attributes.get("color", variant.attributes.get("颜色", ""))).strip(),
                    "price": variant.price_value,
                    "quantity": variant.quantity or "999",
                    "platform_category": variant.platform_category,
                    "main_image": local_main_images[0] if local_main_images else "",
                    "detail_images": "|".join(local_detail_images),
                    "description": variant.description or variant.sell_points,
                    "ship_from_template": variant.ship_from_template,
                    "freight_template": variant.freight_template,
                    "ship_time_template": variant.ship_time_template,
                    "length_cm": variant.length_cm,
                    "width_cm": variant.width_cm,
                    "height_cm": variant.height_cm,
                    "weight_g": variant.weight_g,
                    "link_owner": variant.operator_id or variant.operator_name,
                    "operator_name": variant.operator_name,
                    "source_record_id": variant.source_product_id or variant.variant_id,
                    "style_name": variant.style_name,
                    "price_rule_id": variant.price_rule_id,
                    "publish_route": variant.publish_route,
                    "variant_id": variant.variant_id,
                    "attributes_json": json.dumps(variant.attributes, ensure_ascii=False),
                    "image_enrichment_error": image_enrichment_error,
                }
            )
        )
    return products


def _resolve_variant_images(
    variant: ReleaseVariant,
    *,
    image_client: AIImageAssetClient | None,
    project_root: Path,
) -> tuple[list[str], list[str]]:
    main_images = list(variant.main_images)
    detail_images = list(variant.detail_images)

    needs_remote_lookup = (not main_images) and variant.image_source_type
    if needs_remote_lookup:
        if not image_client:
            raise ValueError(f"Variant '{variant.variant_id}' requires image API enrichment but no client was provided.")
        bundle = image_client.fetch_bundle(
            resource_type=variant.image_source_type,
            sku=variant.image_source_sku,
            spu=variant.image_source_spu,
        )
        if not main_images:
            main_images = bundle.main_urls
        if not detail_images:
            detail_images = bundle.detail_urls

    asset_dir = project_root / ".tmp" / "variant_assets" / variant.variant_id
    local_main_images = _materialize_image_values(
        main_images,
        asset_dir=asset_dir / "main",
        prefix="main",
        image_client=image_client,
    )
    local_detail_images = _materialize_image_values(
        detail_images,
        asset_dir=asset_dir / "detail",
        prefix="detail",
        image_client=image_client,
    )
    return local_main_images, local_detail_images


def _materialize_image_values(
    image_values: list[str],
    *,
    asset_dir: Path,
    prefix: str,
    image_client: AIImageAssetClient | None,
) -> list[str]:
    if not image_values:
        return []
    remote_urls = [value for value in image_values if _is_remote_url(value)]
    local_values = [str(Path(value)) for value in image_values if not _is_remote_url(value)]
    if remote_urls:
        if image_client:
            local_values.extend(
                image_client.download_urls(remote_urls, target_dir=asset_dir, prefix=prefix)
            )
        else:
            local_values.extend(
                _download_public_urls(remote_urls, target_dir=asset_dir, prefix=prefix)
            )
    return local_values


def _normalize_string_list(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    text = str(raw_value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text.split("|") if item.strip()]


def _normalize_attributes(raw_value: Any) -> dict[str, Any]:
    if raw_value is None:
        return {}
    if isinstance(raw_value, dict):
        return {str(key).strip(): value for key, value in raw_value.items()}
    text = str(raw_value).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return {str(key).strip(): value for key, value in parsed.items()}
    return {}


def _is_remote_url(value: str) -> bool:
    return str(value).strip().lower().startswith(("http://", "https://"))


def _download_public_urls(
    urls: list[str],
    *,
    target_dir: Path,
    prefix: str,
) -> list[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []
    for index, url in enumerate(urls, start=1):
        clean_url = str(url).strip()
        if not clean_url:
            continue
        target_path = target_dir / f"{prefix}_{index:02d}{_guess_extension(clean_url)}"
        request = Request(clean_url, method="GET")
        with urlopen(request, timeout=30) as response:
            target_path.write_bytes(response.read())
        saved_paths.append(str(target_path))
    return saved_paths


def _guess_extension(url: str) -> str:
    lowered = url.lower()
    for extension in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if extension in lowered:
            return extension
    return ".jpg"
