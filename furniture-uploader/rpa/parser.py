from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path


MANDATORY_COLUMNS = {"title", "price", "quantity", "platform_category"}
IMAGE_FIELDS = {"main_image": False, "detail_images": True}
CSV_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "gb18030", "gbk")
SANITIZE_EXACT_FIELDS = {
    "title",
    "subtitle",
    "brand",
    "material",
    "size",
    "color",
    "description",
}
SANITIZE_KEYWORDS = ("spec", "sku", "规格")
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE,
)


@dataclass
class ProductRecord:
    task_id: str
    channel: str
    store_name: str
    store_label: str
    outer_sku: str
    title: str
    price: str
    quantity: str
    platform_category: str
    ship_from_template: str
    freight_template: str
    ship_time_template: str
    length_cm: str
    width_cm: str
    height_cm: str
    weight_g: str
    link_owner: str
    operator_name: str
    source_record_id: str
    raw: dict[str, str]
    sanitization_notes: list[str] = field(default_factory=list)
    sku_rows: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "ProductRecord":
        sanitized_row, sanitization_notes = sanitize_row(row)
        return cls(
            task_id=sanitized_row.get("task_id", "").strip(),
            channel=sanitized_row.get("channel", "").strip(),
            store_name=sanitized_row.get("store_name", "").strip(),
            store_label=sanitized_row.get("store_label", "").strip(),
            outer_sku=sanitized_row.get("outer_sku", "").strip(),
            title=sanitized_row.get("title", "").strip(),
            price=sanitized_row.get("price", "").strip(),
            quantity=sanitized_row.get("quantity", "").strip(),
            platform_category=sanitized_row.get("platform_category", "").strip(),
            ship_from_template=sanitized_row.get("ship_from_template", "").strip(),
            freight_template=sanitized_row.get("freight_template", "").strip(),
            ship_time_template=sanitized_row.get("ship_time_template", "").strip(),
            length_cm=sanitized_row.get("length_cm", "").strip(),
            width_cm=sanitized_row.get("width_cm", "").strip(),
            height_cm=sanitized_row.get("height_cm", "").strip(),
            weight_g=sanitized_row.get("weight_g", "").strip(),
            link_owner=sanitized_row.get("link_owner", "").strip(),
            operator_name=sanitized_row.get("operator_name", "").strip(),
            source_record_id=sanitized_row.get("source_record_id", "").strip(),
            raw=sanitized_row,
            sanitization_notes=sanitization_notes,
            sku_rows=list(row.get("sku_rows") or []),
        )


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_json_config(path: str | Path) -> dict:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_products(path: str | Path) -> list[ProductRecord]:
    try:
        import pandas as pd
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency 'pandas'. Install requirements before loading template files."
        ) from exc

    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        dataframe = _read_csv_with_fallback(file_path, pd)
    elif suffix in {".xlsx", ".xls"}:
        dataframe = pd.read_excel(file_path, dtype=str).fillna("")
    else:
        raise ValueError(f"Unsupported template format: {suffix}")

    columns = {column.strip() for column in dataframe.columns}
    missing_columns = sorted(MANDATORY_COLUMNS - columns)
    if missing_columns:
        raise ValueError(f"Missing mandatory columns: {', '.join(missing_columns)}")

    records: list[ProductRecord] = []
    for row in dataframe.to_dict(orient="records"):
        normalized = {str(key).strip(): str(value).strip() for key, value in row.items()}
        if not any(normalized.values()):
            continue
        records.append(ProductRecord.from_row(normalized))

    return records


def _read_csv_with_fallback(file_path: Path, pd_module):
    last_error: Exception | None = None
    for encoding in CSV_ENCODING_CANDIDATES:
        try:
            return pd_module.read_csv(file_path, dtype=str, encoding=encoding).fillna("")
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    message = (
        f"Failed to decode CSV file '{file_path}'. "
        f"Tried encodings: {', '.join(CSV_ENCODING_CANDIDATES)}."
    )
    if last_error is not None:
        raise ValueError(message) from last_error
    raise ValueError(message)


def validate_products(
    products: list[ProductRecord],
    platform_key: str,
    platform_config: dict,
    category_config: dict,
) -> ValidationReport:
    report = ValidationReport()
    required_fields = set(platform_config.get("required_fields", []))

    for index, product in enumerate(products, start=1):
        prefix = f"Row {index} ({product.title or 'untitled'})"

        if product.channel and product.channel.lower() != platform_key.lower():
            report.warnings.append(
                f"{prefix}: channel '{product.channel}' does not match runtime platform '{platform_key}'"
            )
        if not product.store_name:
            report.warnings.append(f"{prefix}: store_name is empty")
        if not product.outer_sku:
            report.warnings.append(f"{prefix}: outer_sku is empty")
        if not product.link_owner:
            report.warnings.append(f"{prefix}: link_owner is empty")

        for field_name in sorted(required_fields):
            value = product.raw.get(field_name, "").strip()
            if not value:
                report.errors.append(f"{prefix}: missing required field '{field_name}'")

        category_key = product.platform_category
        category_entry = category_config.get(category_key)
        if not category_entry:
            report.errors.append(
                f"{prefix}: unknown platform_category '{category_key}' in furniture_categories.json"
            )
        else:
            platform_category_name = category_entry.get("platform_categories", {}).get(platform_key, "")
            if not platform_category_name:
                report.errors.append(
                    f"{prefix}: no category mapping for platform '{platform_key}' under '{category_key}'"
                )

            for field_name in category_entry.get("required_fields", []):
                if not product.raw.get(field_name, "").strip():
                    report.warnings.append(
                        f"{prefix}: category '{category_key}' suggests filling '{field_name}'"
                    )

        if product.price:
            _validate_price(product.price, prefix, report)
        if product.quantity:
            _validate_quantity(product.quantity, prefix, report)

        for field_name, is_multiple in IMAGE_FIELDS.items():
            raw_value = product.raw.get(field_name, "").strip()
            if not raw_value:
                continue

            paths = _split_paths(raw_value) if is_multiple else [raw_value]
            for image_path in paths:
                if not Path(image_path).exists():
                    report.warnings.append(
                        f"{prefix}: image path does not exist for '{field_name}': {image_path}"
                    )

    return report


def collect_sanitization_warnings(products: list[ProductRecord]) -> list[str]:
    warnings: list[str] = []
    for index, product in enumerate(products, start=1):
        prefix = f"Row {index} ({product.title or 'untitled'})"
        for note in product.sanitization_notes:
            warnings.append(f"{prefix}: {note}")
    return warnings


def _validate_price(raw_price: str, prefix: str, report: ValidationReport) -> None:
    try:
        price = Decimal(raw_price)
    except InvalidOperation:
        report.errors.append(f"{prefix}: invalid price '{raw_price}'")
        return

    if price <= 0:
        report.errors.append(f"{prefix}: price must be greater than 0")


def _validate_quantity(raw_quantity: str, prefix: str, report: ValidationReport) -> None:
    if not raw_quantity.isdigit():
        report.errors.append(f"{prefix}: quantity must be an integer, got '{raw_quantity}'")
        return

    quantity = int(raw_quantity)
    if quantity < 0:
        report.errors.append(f"{prefix}: quantity must be 0 or greater")


def _split_paths(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def sanitize_row(row: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    sanitized_row = dict(row)
    notes: list[str] = []

    for key, value in row.items():
        if not _should_sanitize_field(key):
            continue

        cleaned = strip_emoji(str(value))
        cleaned = _collapse_whitespace(cleaned)
        if cleaned != str(value):
            sanitized_row[key] = cleaned
            notes.append(
                f"removed emoji from field '{key}' before publish"
            )

    return sanitized_row, notes


def strip_emoji(value: str) -> str:
    return EMOJI_PATTERN.sub("", value)


def _should_sanitize_field(field_name: str) -> bool:
    normalized = field_name.strip().lower()
    if normalized in SANITIZE_EXACT_FIELDS:
        return True
    return any(keyword in normalized for keyword in SANITIZE_KEYWORDS)


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())
