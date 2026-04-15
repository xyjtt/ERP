from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StandardProduct:
    sku_id: str
    name: str
    brand: str | None = None
    material: str | None = None
    color: str | None = None
    size: str | None = None
    category: str | None = None
    price: float | None = None
    quantity: int = 1
    description: str | None = None
    subtitle: str | None = None
    main_images: list[str] = field(default_factory=list)
    detail_images: list[str] = field(default_factory=list)
    sku_images: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    source_product_id: str | None = None
    shop_id: int | None = None
    shop_name: str | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    publish_mode: str = "draft"
    weight: float | None = None
    length: float | None = None
    width: float | None = None
    height: float | None = None
    supplier_name: str | None = None
    supplier_id: str | None = None
    warehouse_name: str | None = None
    unit: str | None = None
    sku_type: str | None = None
    item_type: str | None = None
    custom_field_1: str | None = None
    custom_field_2: str | None = None
    custom_field_3: str | None = None
    custom_field_4: str | None = None
    custom_field_5: str | None = None
    custom_field_6: str | None = None
    custom_field_7: str | None = None
    custom_field_8: str | None = None
    custom_field_9: str | None = None
    custom_field_10: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformPublishModel:
    platform: str
    variant_id: str
    source_product_id: str
    title: str
    style_name: str
    sell_points: str
    attributes: dict[str, Any]
    platform_category: str
    price_value: str
    quantity: str
    publish_mode: str
    status: str
    description: str
    main_images: list[str]
    detail_images: list[str]
    sku_images: list[str]
    shop_id: str
    shop_name: str
    operator_id: str
    operator_name: str
    channel: str
    weight: str | None = None
    l: str | None = None
    w: str | None = None
    h: str | None = None
    supplier_name: str | None = None
    warehouse_name: str | None = None
    unit: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MappingConfig:
    mapping_code: str
    platform: str
    version: str
    source_to_standard: dict[str, Any]
    standard_to_platform: dict[str, Any]
    default_values: dict[str, Any] = field(default_factory=dict)
    required_fields: list[str] = field(default_factory=list)
    validation_rules: dict[str, Any] = field(default_factory=dict)


DEFAULT_1688_MAPPING = MappingConfig(
    mapping_code="1688_listing",
    platform="1688",
    version="v1",
    source_to_standard={
        "sku_id": "sku_id",
        "i_id": "sku_id",
        "name": "name",
        "title": "name",
        "short_name": "name",
        "brand": "brand",
        "material": "material",
        "color": "color",
        "size": "size",
        "category": "category",
        "c_id": "category",
        "category_path": "category",
        "platform_category_name": "category",
        "price": "price",
        "sale_price": "price",
        "cost_price": "price",
        "market_price": "price",
        "price_value": "price",
        "quantity": "quantity",
        "description": "description",
        "sell_points": "description",
        "subtitle": "subtitle",
        "properties_value": "attributes",
        "main_images": "main_images",
        "main_image": "main_images",
        "main_image_remote": "main_images",
        "pic_big": "main_images",
        "pic": "main_images",
        "detail_images": "detail_images",
        "detail_images_remote": "detail_images",
        "sku_images": "sku_images",
        "attributes": "attributes",
        "source_product_id": "source_product_id",
        "image_source_sku": "source_product_id",
        "sku_code": "source_product_id",
        "supplier_sku_id": "source_product_id",
        "shop_id": "shop_id",
        "shop_name": "shop_name",
        "store_name": "shop_name",
        "store_label": "shop_name",
        "operator_id": "operator_id",
        "operator_name": "operator_name",
        "created_by": "operator_name",
        "creator": "operator_name",
        "publish_mode": "publish_mode",
        "weight": "weight",
        "l": "length",
        "w": "width",
        "h": "height",
        "supplier_name": "supplier_name",
        "supplier_id": "supplier_id",
        "other_1": "custom_field_1",
        "other_2": "custom_field_2",
        "other_3": "custom_field_3",
        "other_4": "custom_field_4",
        "other_5": "custom_field_5",
        "other_6": "custom_field_6",
        "other_7": "custom_field_7",
        "other_8": "custom_field_8",
        "other_9": "custom_field_9",
        "other_10": "custom_field_10",
        "vc_name": "warehouse_name",
        "unit": "unit",
        "sku_type": "sku_type",
        "item_type": "item_type",
    },
    standard_to_platform={
        "sku_id": "variant_id",
        "source_product_id": "source_product_id",
        "name": "title",
        "brand": "attributes.brand",
        "material": "attributes.material",
        "color": "attributes.color",
        "size": "attributes.size",
        "category": "platform_category",
        "price": "price_value",
        "quantity": "quantity",
        "description": "description",
        "subtitle": "sell_points",
        "main_images": "main_images",
        "detail_images": "detail_images",
        "sku_images": "sku_images",
        "shop_id": "shop_id",
        "shop_name": "shop_name",
        "operator_id": "operator_id",
        "operator_name": "operator_name",
        "publish_mode": "publish_mode",
        "attributes": "attributes",
        "weight": "weight",
        "length": "l",
        "width": "w",
        "height": "h",
        "supplier_name": "supplier_name",
        "warehouse_name": "warehouse_name",
        "unit": "unit",
    },
    default_values={
        "style_name": "platform_mediation",
        "publish_mode": "draft",
        "status": "draft",
        "quantity": "1",
        "channel": "1688",
        "weight": "0",
        "l": "0",
        "w": "0",
        "h": "0",
    },
    required_fields=["sku_id", "name", "shop_id"],
    validation_rules={
        "price": {"min": 0, "type": "float"},
        "quantity": {"min": 1, "type": "int"},
        "weight": {"min": 0, "type": "float"},
    },
)