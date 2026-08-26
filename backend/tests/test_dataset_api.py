import asyncio
from pathlib import Path

import httpx
import pytest

import app.api.datasets as datasets_api
from app.datasets import DatasetRegistry
from app.main import app


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_dataset_preview_import_inspection_and_compatibility_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = DatasetRegistry(tmp_path)
    monkeypatch.setattr(datasets_api, "dataset_registry", registry)
    content = (
        b"date,ticker,price\n"
        b"2025-01-01T00:00:00Z,AAPL,100\n"
        b"2025-01-02T00:00:00Z,AAPL,101\n"
        b"2025-01-03T00:00:00Z,AAPL,102\n"
    )
    preview = asyncio.run(
        _request(
            "POST",
            "/api/datasets/import/preview",
            files={"file": ("prices.csv", content, "text/csv")},
        )
    )
    assert preview.status_code == 200
    assert preview.json()["candidate_mapping"] == {
        "timestamp": "date",
        "symbol": "ticker",
        "close": "price",
    }
    imported = asyncio.run(
        _request(
            "POST",
            "/api/datasets/import",
            json={
                "preview_id": preview.json()["preview_id"],
                "name": "API prices",
                "mapping": {"timestamp": "date", "symbol": "ticker", "close": "price"},
            },
        )
    )
    assert imported.status_code == 201
    dataset_id = imported.json()["dataset_id"]
    listed = asyncio.run(_request("GET", "/api/datasets"))
    assert dataset_id in {item["dataset_id"] for item in listed.json()}
    detail = asyncio.run(_request("GET", f"/api/datasets/{dataset_id}"))
    assert detail.json()["content_fingerprint"].startswith("sha256:")
    rows = asyncio.run(_request("GET", f"/api/datasets/{dataset_id}/preview"))
    assert rows.json()["rows"][0] == {
        "timestamp": "2025-01-01T00:00:00+00:00",
        "symbol": "AAPL",
        "close": 100.0,
    }
    incompatible = asyncio.run(
        _request(
            "POST",
            "/api/compatibility-checks",
            json={
                "strategy_id": "pairs-trading",
                "dataset_id": dataset_id,
                "parameters": {"lookback": 5},
            },
        )
    )
    assert incompatible.status_code == 200
    assert incompatible.json()["compatible"] is False
    assert "requires 2 symbols" in " ".join(incompatible.json()["reasons"])


def test_dataset_api_reports_file_and_registry_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(datasets_api, "dataset_registry", DatasetRegistry(tmp_path))
    wrong_type = asyncio.run(
        _request(
            "POST",
            "/api/datasets/import/preview",
            files={"file": ("prices.txt", b"value\n1\n", "text/plain")},
        )
    )
    assert wrong_type.status_code == 422
    missing = asyncio.run(_request("GET", "/api/datasets/dataset-missing"))
    assert missing.status_code == 404
    missing_preview = asyncio.run(_request("GET", "/api/datasets/dataset-missing/preview"))
    assert missing_preview.status_code == 404
    missing_strategy = asyncio.run(
        _request(
            "POST",
            "/api/compatibility-checks",
            json={"strategy_id": "missing", "dataset_id": "pairs-sample-v1"},
        )
    )
    assert missing_strategy.status_code == 404
