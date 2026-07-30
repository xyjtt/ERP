"""Translate stop-sale and SKU-replacement tasks into durable Saga operations."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from operation_saga import OperationSagaRepository, SagaOperation


def task_operation_key(task_type: str, task: Any) -> str:
    if task_type == "stop_sale":
        parts = (
            getattr(task, "store_name", ""),
            getattr(task, "product_id", ""),
            getattr(task, "online_sku", ""),
            getattr(task, "platform_store_item_code", ""),
        )
    elif task_type == "sku_replace":
        parts = (
            getattr(task, "store_name", ""),
            getattr(task, "product_id", ""),
            getattr(task, "online_sku", ""),
            getattr(task, "replacement_sku", ""),
        )
    else:
        raise ValueError(f"Unsupported SKU operation task type: {task_type}")
    normalized = "|".join("".join(str(part or "").split()).lower() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def record_operation_key(task_type: str, record: Mapping[str, Any]) -> str:
    class RecordTask:
        pass

    task = RecordTask()
    for name in (
        "store_name",
        "product_id",
        "online_sku",
        "replacement_sku",
        "platform_store_item_code",
    ):
        setattr(task, name, record.get(name, ""))
    return task_operation_key(task_type, task)


def build_saga_operation(task_type: str, run_id: str, account_key: str, task: Any) -> SagaOperation:
    operation_key = task_operation_key(task_type, task)
    payload = {
        "task_id": operation_key,
        "operation_key": operation_key,
        "source": "1688_sku_offline" if task_type == "stop_sale" else "1688_sku_replace",
        "store_name": str(getattr(task, "store_name", "") or ""),
        "platform": str(getattr(task, "platform", "") or ""),
        "product_id": str(getattr(task, "product_id", "") or ""),
        "online_sku": str(getattr(task, "online_sku", "") or ""),
        "replacement_sku": str(getattr(task, "replacement_sku", "") or ""),
        "platform_store_item_code": str(getattr(task, "platform_store_item_code", "") or ""),
        "handling": str(getattr(task, "handling", "") or ""),
        "source_file": str(getattr(task, "source_file", "") or ""),
        "source_row_number": int(getattr(task, "source_row_number", 0) or 0),
        "action": "cleanup_1688_link" if task_type == "stop_sale" else "sync_by_link",
    }
    business_key = "|".join(
        str(payload[name])
        for name in (
            "store_name",
            "product_id",
            "online_sku",
            "replacement_sku",
            "platform_store_item_code",
        )
        if str(payload[name])
    )
    return SagaOperation(
        operation_key=operation_key,
        run_id=run_id,
        task_type=task_type,
        account_key=account_key,
        business_key=business_key,
        payload=payload,
    )


def build_saga_operations(
    task_type: str,
    run_id: str,
    account_key: str,
    tasks: Iterable[Any],
) -> list[SagaOperation]:
    by_key = {
        operation.operation_key: operation
        for operation in (
            build_saga_operation(task_type, run_id, account_key, task) for task in tasks
        )
    }
    return list(by_key.values())


def persist_ali1688_results(
    repository: OperationSagaRepository,
    *,
    task_type: str,
    operations: Iterable[SagaOperation],
    records: Iterable[Mapping[str, Any]],
    account_fencing_token: int,
) -> None:
    operation_map = {operation.operation_key: operation for operation in operations}
    topic = (
        "jushuitan.cleanup_1688_link"
        if task_type == "stop_sale"
        else "jushuitan.sync_1688_link"
    )
    success_statuses = (
        {"success", "already_offline"}
        if task_type == "stop_sale"
        else {"success", "already_replaced"}
    )
    for record in records:
        operation_key = record_operation_key(task_type, record)
        operation = operation_map.get(operation_key)
        if operation is None:
            continue
        status = str(record.get("status") or "failed").strip()
        success = status in success_statuses
        outbox_payload = dict(operation.payload)
        outbox_payload["source_status"] = status
        repository.record_ali1688_result(
            operation_key=operation_key,
            account_fencing_token=account_fencing_token,
            status=status,
            evidence={
                "status": status,
                "result_context": dict(record.get("result_context") or {}),
                "screenshot_path": str(record.get("screenshot_path") or ""),
                "html_snapshot_path": str(record.get("html_snapshot_path") or ""),
            },
            outbox_topic=topic if success else "",
            outbox_payload=outbox_payload if success else None,
            error_code=str(record.get("error_category") or record.get("error_type") or ""),
            error_summary=str(record.get("error_message") or record.get("page_error_text") or ""),
        )
