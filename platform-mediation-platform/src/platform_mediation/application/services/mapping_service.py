from __future__ import annotations

from typing import Any

from platform_mediation.domain.standard_product_model import (
    DEFAULT_1688_MAPPING,
    MappingConfig,
    PlatformPublishModel,
    StandardProduct,
)


class MappingService:
    def __init__(self) -> None:
        self._mappings: dict[str, MappingConfig] = {
            DEFAULT_1688_MAPPING.mapping_code: DEFAULT_1688_MAPPING,
        }

    def register_mapping(self, config: MappingConfig) -> None:
        key = f"{config.mapping_code}:{config.version}"
        self._mappings[key] = config

    def get_mapping(self, mapping_code: str, version: str = "v1") -> MappingConfig | None:
        key = f"{mapping_code}:{version}"
        return self._mappings.get(key) or self._mappings.get(mapping_code)

    def transform_source_to_standard(
        self,
        source_data: dict[str, Any],
        mapping: MappingConfig,
    ) -> StandardProduct:
        result: dict[str, Any] = {}
        for source_key, standard_key in mapping.source_to_standard.items():
            value = self._extract_value(source_data, source_key)
            if value is not None:
                self._set_nested_value(result, standard_key, value)

        for key, default_value in mapping.default_values.items():
            if key not in result or result[key] is None:
                result[key] = default_value

        return StandardProduct(
            sku_id=str(result.get("sku_id") or source_data.get("sku_id") or ""),
            name=str(result.get("name") or source_data.get("name") or source_data.get("title") or ""),
            brand=result.get("brand"),
            material=result.get("material"),
            color=result.get("color"),
            size=result.get("size"),
            category=result.get("category"),
            price=self._parse_float(result.get("price")),
            quantity=self._parse_int(result.get("quantity"), default=1),
            description=result.get("description"),
            subtitle=result.get("subtitle"),
            main_images=self._normalize_list(result.get("main_images")),
            detail_images=self._normalize_list(result.get("detail_images")),
            sku_images=self._normalize_list(result.get("sku_images")),
            attributes=result.get("attributes") or {},
            source_product_id=result.get("source_product_id"),
            shop_id=self._parse_int(result.get("shop_id")),
            shop_name=result.get("shop_name"),
            operator_id=result.get("operator_id"),
            operator_name=result.get("operator_name"),
            publish_mode=result.get("publish_mode") or "draft",
            weight=self._parse_float(result.get("weight")),
            length=self._parse_float(result.get("length")),
            width=self._parse_float(result.get("width")),
            height=self._parse_float(result.get("height")),
            supplier_name=result.get("supplier_name"),
            supplier_id=result.get("supplier_id"),
            warehouse_name=result.get("warehouse_name"),
            unit=result.get("unit"),
            sku_type=result.get("sku_type"),
            item_type=result.get("item_type"),
            custom_field_1=result.get("custom_field_1"),
            custom_field_2=result.get("custom_field_2"),
            custom_field_3=result.get("custom_field_3"),
            custom_field_4=result.get("custom_field_4"),
            custom_field_5=result.get("custom_field_5"),
            custom_field_6=result.get("custom_field_6"),
            custom_field_7=result.get("custom_field_7"),
            custom_field_8=result.get("custom_field_8"),
            custom_field_9=result.get("custom_field_9"),
            custom_field_10=result.get("custom_field_10"),
            extra=result,
        )

    def transform_standard_to_platform(
        self,
        standard: StandardProduct,
        mapping: MappingConfig,
    ) -> PlatformPublishModel:
        source_dict = {
            "sku_id": standard.sku_id,
            "name": standard.name,
            "brand": standard.brand,
            "material": standard.material,
            "color": standard.color,
            "size": standard.size,
            "category": standard.category,
            "price": standard.price,
            "quantity": standard.quantity,
            "description": standard.description,
            "subtitle": standard.subtitle,
            "main_images": standard.main_images,
            "detail_images": standard.detail_images,
            "sku_images": standard.sku_images,
            "attributes": standard.attributes,
            "source_product_id": standard.source_product_id,
            "shop_id": standard.shop_id,
            "shop_name": standard.shop_name,
            "operator_id": standard.operator_id,
            "operator_name": standard.operator_name,
            "publish_mode": standard.publish_mode,
            "weight": standard.weight,
            "length": standard.length,
            "width": standard.width,
            "height": standard.height,
            "supplier_name": standard.supplier_name,
            "supplier_id": standard.supplier_id,
            "warehouse_name": standard.warehouse_name,
            "unit": standard.unit,
            "sku_type": standard.sku_type,
            "item_type": standard.item_type,
        }

        result: dict[str, Any] = {}
        for standard_key, platform_key in mapping.standard_to_platform.items():
            value = source_dict.get(standard_key)
            if value is not None:
                self._set_nested_value(result, platform_key, value)

        for key, default_value in mapping.default_values.items():
            if key not in result or result[key] is None:
                result[key] = default_value

        attributes = result.get("attributes") or {}
        for attr_key in ["brand", "material", "color", "size"]:
            attr_value = source_dict.get(attr_key)
            if attr_value and attr_key not in attributes:
                attributes[attr_key] = attr_value
        result["attributes"] = attributes

        return PlatformPublishModel(
            platform=mapping.platform,
            variant_id=str(result.get("variant_id") or standard.sku_id),
            source_product_id=str(result.get("source_product_id") or standard.source_product_id or standard.sku_id),
            title=str(result.get("title") or standard.name),
            style_name=str(result.get("style_name") or "platform_mediation"),
            sell_points=str(result.get("sell_points") or standard.subtitle or ""),
            attributes=attributes,
            platform_category=str(result.get("platform_category") or standard.category or ""),
            price_value=str(result.get("price_value") or str(standard.price or "")),
            quantity=str(result.get("quantity") or str(standard.quantity)),
            publish_mode=str(result.get("publish_mode") or standard.publish_mode),
            status=str(result.get("status") or "draft"),
            description=str(result.get("description") or standard.description or ""),
            main_images=self._normalize_list(result.get("main_images")),
            detail_images=self._normalize_list(result.get("detail_images")),
            sku_images=self._normalize_list(result.get("sku_images")),
            shop_id=str(result.get("shop_id") or str(standard.shop_id or "")),
            shop_name=str(result.get("shop_name") or standard.shop_name or ""),
            operator_id=str(result.get("operator_id") or standard.operator_id or ""),
            operator_name=str(result.get("operator_name") or standard.operator_name or ""),
            channel=str(result.get("channel") or mapping.platform),
            weight=str(result.get("weight") or str(standard.weight or "")),
            l=str(result.get("l") or str(standard.length or "")),
            w=str(result.get("w") or str(standard.width or "")),
            h=str(result.get("h") or str(standard.height or "")),
            supplier_name=str(result.get("supplier_name") or standard.supplier_name or ""),
            warehouse_name=str(result.get("warehouse_name") or standard.warehouse_name or ""),
            unit=str(result.get("unit") or standard.unit or ""),
            extra=result,
        )

    def transform_full(
        self,
        source_data: dict[str, Any],
        mapping_code: str,
        version: str = "v1",
    ) -> PlatformPublishModel:
        mapping = self.get_mapping(mapping_code, version)
        if mapping is None:
            mapping = DEFAULT_1688_MAPPING
        standard = self.transform_source_to_standard(source_data, mapping)
        return self.transform_standard_to_platform(standard, mapping)

    def validate_standard_product(
        self,
        product: StandardProduct,
        mapping: MappingConfig,
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for field in mapping.required_fields:
            value = getattr(product, field, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"Required field '{field}' is missing or empty")

        for field, rule in mapping.validation_rules.items():
            value = getattr(product, field, None)
            if value is not None:
                if rule.get("type") == "float" and not isinstance(value, (int, float)):
                    errors.append(f"Field '{field}' must be a number")
                elif rule.get("type") == "int" and not isinstance(value, int):
                    errors.append(f"Field '{field}' must be an integer")
                if rule.get("min") is not None and isinstance(value, (int, float)):
                    if value < rule["min"]:
                        errors.append(f"Field '{field}' must be >= {rule['min']}")

        return len(errors) == 0, errors

    def _extract_value(self, data: dict[str, Any], key: str) -> Any:
        if "." in key:
            parts = key.split(".", 1)
            first = parts[0]
            rest = parts[1]
            value = data.get(first)
            if isinstance(value, dict):
                return self._extract_value(value, rest)
            return None
        return data.get(key)

    def _set_nested_value(self, data: dict[str, Any], key: str, value: Any) -> None:
        if "." in key:
            parts = key.split(".", 1)
            first = parts[0]
            rest = parts[1]
            if first not in data:
                data[first] = {}
            if isinstance(data[first], dict):
                self._set_nested_value(data[first], rest, value)
        else:
            data[key] = value

    def _parse_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _parse_int(self, value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    def _normalize_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [item.strip() for item in text.split("|") if item.strip()]