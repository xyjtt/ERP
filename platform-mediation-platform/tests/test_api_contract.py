from __future__ import annotations

from fastapi.testclient import TestClient

from platform_mediation.bootstrap import get_container
from platform_mediation.main import create_app


def reset_container() -> None:
    get_container.cache_clear()


def test_task_api_flow() -> None:
    reset_container()
    client = TestClient(create_app())

    create_response = client.post(
        "/api/v1/tasks",
        json={
            "task_type": "listing",
            "platform": "1688",
            "shop_id": 10001,
            "executor_type": "rpa",
            "priority": 10,
            "mapping_version": "1688.v1",
            "created_by": "pytest",
            "items": [{"sku_id": "SKU001", "payload": {"sku_id": "SKU001"}}],
        },
    )
    assert create_response.status_code == 201
    task_id = create_response.json()["task_id"]

    list_response = client.get("/api/v1/tasks")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    dispatch_response = client.post(f"/api/v1/tasks/{task_id}/dispatch")
    assert dispatch_response.status_code == 200
    assert dispatch_response.json()["status"] == "success"

    items_response = client.get(f"/api/v1/tasks/{task_id}/items")
    attempts_response = client.get(f"/api/v1/tasks/{task_id}/attempts")
    artifacts_response = client.get(f"/api/v1/tasks/{task_id}/artifacts")

    assert items_response.status_code == 200
    assert attempts_response.status_code == 200
    assert artifacts_response.status_code == 200
    assert len(attempts_response.json()) == 1
    assert len(artifacts_response.json()) == 1
