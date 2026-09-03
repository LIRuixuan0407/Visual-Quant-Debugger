from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.broker import BrokerAccountSnapshot, BrokerOrderRequest, BrokerOrderUpdate
from app.main import app
from app.market_data import (
    AlpacaStockMarketDataAdapter,
    FakeLiveMarketDataAdapter,
    MarketApplyKind,
    MarketBar,
    PointInTimeMarketStore,
)
from app.market_data import alpaca as alpaca_module
from app.market_data.alpaca import alpaca_provider_status
from app.paper import (
    CreatePaperAccount,
    CreatePaperSession,
    PaperSessionRepository,
    PaperSessionService,
)
from app.runs import ArtifactIntegrityError, RunRepository, ValidationRequest
from app.runs.validation import validate_backtest_vs_paper
from app.sdk.registry import StrategyRegistry

STRATEGY_SOURCE = """
from app.sdk import StrategyMetadata, VQDStrategy, parameter

class LiveCorrectionStrategy(VQDStrategy):
    metadata = StrategyMetadata(
        strategy_id="test.live-correction",
        name="Live Correction Strategy",
        version="1.0.0",
        description="Exercises live point-in-time correction semantics.",
    )
    threshold = parameter(
        default=0.0,
        minimum=-100.0,
        maximum=100.0,
        step=0.1,
        description="Difference threshold",
    )

    def initialize(self, context):
        self.calls = 0

    def on_bar(self, context):
        self.calls += 1
        history = context.history(symbol="AAPL", bars=2)
        previous = history[0]
        current = history[-1]
        difference = current - previous
        feature = context.feature(
            name="difference",
            value=difference,
            inputs=(history,),
            formula="current - previous",
            window_start=history.timestamps[0],
            window_end=history.timestamps[-1],
        )
        target = 10.0 if len(history) == 2 and difference > self.threshold else 0.0
        return context.target_positions(
            {"AAPL": target},
            reason="Corrected-history comparison",
            dependencies=(feature,),
            signal="LONG" if target else "FLAT",
            previous_state="CURRENT",
            next_state="LONG" if target else "FLAT",
        )
"""


def _time(minute: int) -> datetime:
    return datetime(2025, 1, 2, 14, minute, tzinfo=UTC)


def _bar(
    minute: int,
    close: float,
    *,
    revision: int = 1,
    correction: bool = False,
    provider_event_id: str | None = None,
) -> MarketBar:
    event = _time(minute)
    available = event + timedelta(minutes=1, seconds=30 if correction else 0)
    return MarketBar(
        symbol="AAPL",
        event_time=event,
        available_at=available,
        received_at=available + timedelta(milliseconds=125),
        open=close,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        volume=1_000,
        provider="fake",
        feed="iex",
        provider_event_id=provider_event_id or f"fake:AAPL:{minute}:r{revision}",
        revision=revision,
        is_correction=correction,
    )


def _service(tmp_path: Path) -> tuple[PaperSessionService, CreatePaperSession, Path]:
    source = tmp_path / "live_strategy.py"
    source.write_text(STRATEGY_SOURCE, encoding="utf-8")
    registry = StrategyRegistry(tmp_path)
    registry.add(source)
    service = PaperSessionService(PaperSessionRepository(tmp_path), registry=registry)
    request = CreatePaperSession(
        strategy_id="test.live-correction",
        symbols=("AAPL",),
        provider="fake",
        feed="iex",
        parameters={"threshold": 0.0},
    )
    return service, request, source


def _run(awaitable: object) -> object:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


class FakePaperBroker:
    def __init__(self) -> None:
        self.orders: dict[str, BrokerOrderUpdate] = {}
        self.submissions: list[BrokerOrderRequest] = []
        self.closed = False

    async def account(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            account_id="alpaca-paper-account",
            status="ACTIVE",
            currency="USD",
            cash=100_000.0,
            equity=100_000.0,
            buying_power=200_000.0,
            portfolio_value=100_000.0,
            trading_blocked=False,
        )

    async def submit_market_order(self, request: BrokerOrderRequest) -> BrokerOrderUpdate:
        self.submissions.append(request)
        update = BrokerOrderUpdate(
            provider_order_id=f"alpaca-{len(self.submissions)}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            ordered_quantity=request.quantity,
            filled_quantity=0.0,
            status="SUBMITTED",
            raw_status="new",
            submitted_at=request.submitted_at,
            updated_at=request.submitted_at,
        )
        self.orders[update.provider_order_id] = update
        return update

    async def get_order(self, provider_order_id: str) -> BrokerOrderUpdate:
        return self.orders[provider_order_id]

    async def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderUpdate:
        return next(
            item for item in self.orders.values() if item.client_order_id == client_order_id
        )

    async def cancel_order(self, provider_order_id: str) -> None:
        current = self.orders[provider_order_id]
        self.orders[provider_order_id] = current.model_copy(
            update={
                "status": "CANCELLED",
                "raw_status": "canceled",
                "terminal_at": current.updated_at + timedelta(seconds=1),
                "updated_at": current.updated_at + timedelta(seconds=1),
            }
        )

    async def close(self) -> None:
        self.closed = True


def test_market_bar_time_semantics_and_point_in_time_versions() -> None:
    original = _bar(30, 100.0)
    correction = _bar(30, 100.15, revision=2, correction=True)
    store = PointInTimeMarketStore(("AAPL",))
    assert store.classify(original) == MarketApplyKind.FRAME_READY
    frame = store.commit(original, MarketApplyKind.FRAME_READY)
    assert frame is not None
    assert frame.timestamp == original.event_time
    assert frame.knowledge_time == original.available_at
    assert store.classify(correction) == MarketApplyKind.CORRECTION
    store.commit(correction, MarketApplyKind.CORRECTION)
    assert store.frame(original.event_time, known_at=original.available_at).value("AAPL") == 100.0
    assert store.frame(original.event_time).value("AAPL") == 100.15
    assert [item.revision for item in store.versions("AAPL", original.event_time)] == [1, 2]


def test_fake_provider_contract_connect_receive_disconnect_and_backfill() -> None:
    async def scenario() -> None:
        adapter = FakeLiveMarketDataAdapter()
        await adapter.connect()
        assert adapter.connection_state == "CONNECTED"
        await adapter.subscribe(("aapl",))
        assert adapter.symbols == ("AAPL",)
        emitted = _bar(30, 100.0)
        await adapter.emit(emitted)
        assert await adapter.events().__anext__() == emitted
        adapter.add_historical(_bar(31, 101.0))
        historical = await adapter.historical_bars(("AAPL",), _time(31), _time(31))
        assert historical == (_bar(31, 101.0),)
        await adapter.simulate_disconnect()
        assert adapter.connection_state == "RECONNECTING"
        await adapter.simulate_reconnect()
        assert adapter.connection_state == "CONNECTED"
        await adapter.disconnect()
        assert adapter.connection_state == "DISCONNECTED"

    asyncio.run(scenario())


def test_alpaca_normalization_corrections_and_provider_status_do_not_expose_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "visible-only-to-backend")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "never-return-this")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    adapter = AlpacaStockMarketDataAdapter()
    payload: dict[str, object] = {
        "T": "b",
        "S": "AAPL",
        "o": 100.0,
        "h": 101.0,
        "l": 99.0,
        "c": 100.5,
        "v": 1200,
        "t": "2025-01-02T14:30:00Z",
    }
    received = _time(31)
    original = adapter.normalize_message(payload, received_at=received)
    assert original is not None and original.revision == 1 and not original.is_correction
    payload["T"] = "u"
    payload["c"] = 100.75
    corrected = adapter.normalize_message(payload, received_at=received + timedelta(seconds=30))
    assert corrected is not None and corrected.revision == 2 and corrected.is_correction
    rendered = alpaca_provider_status().model_dump_json()
    assert '"configured":true' in rendered
    assert "visible-only-to-backend" not in rendered
    assert "never-return-this" not in rendered


def test_alpaca_websocket_rest_clock_and_calendar_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self.responses = iter(
                (
                    '[{"T":"success","msg":"connected"}]',
                    '[{"T":"success","msg":"authenticated"}]',
                )
            )
            self.closed = False

        async def recv(self) -> str:
            return next(self.responses)

        async def send(self, value: str) -> None:
            self.sent.append(json.loads(value))

        async def close(self) -> None:
            self.closed = True

        def __aiter__(self) -> object:
            async def messages() -> object:
                yield json.dumps(
                    [
                        {
                            "T": "b",
                            "S": "AAPL",
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100.5,
                            "v": 5,
                            "t": "2025-01-02T14:30:00Z",
                        }
                    ]
                )

            return messages()

    socket = FakeSocket()

    async def fake_connect(*_: object, **__: object) -> FakeSocket:
        return socket

    class FakeHttpClient:
        async def __aenter__(self) -> FakeHttpClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            request = httpx.Request("GET", url)
            if url.endswith("/clock"):
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "timestamp": "2025-01-02T15:00:00Z",
                        "is_open": True,
                        "next_open": "2025-01-03T14:30:00Z",
                        "next_close": "2025-01-02T21:00:00Z",
                    },
                )
            if url.endswith("/calendar"):
                return httpx.Response(
                    200,
                    request=request,
                    json=[{"date": "2025-01-02", "open": "09:30", "close": "16:00"}],
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "bars": [
                        {
                            "t": "2025-01-02T14:30:00Z",
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100.5,
                            "v": 5,
                        }
                    ]
                },
            )

    monkeypatch.setattr(alpaca_module, "connect", fake_connect)
    monkeypatch.setattr(alpaca_module.httpx, "AsyncClient", lambda **_: FakeHttpClient())

    async def scenario() -> None:
        adapter = AlpacaStockMarketDataAdapter(
            api_key="backend-key", secret_key="backend-secret", feed="iex"
        )
        await adapter.connect()
        await adapter.subscribe(("AAPL",))
        streamed = await adapter.events().__anext__()
        assert streamed.symbol == "AAPL" and streamed.close == 100.5
        historical = await adapter.historical_bars(("AAPL",), _time(30), _time(31))
        assert len(historical) == 1 and historical[0].provider_event_id.startswith("rest:")
        clock = await adapter.market_clock.current()
        assert clock.is_open
        calendar = await adapter.market_clock.calendar("2025-01-02", "2025-01-02")
        assert calendar[0].open.hour == 9 and calendar[0].close.hour == 16
        await adapter.disconnect()

    asyncio.run(scenario())
    assert socket.sent[0] == {
        "action": "auth",
        "key": "backend-key",
        "secret": "backend-secret",
    }
    assert socket.sent[1] == {
        "action": "subscribe",
        "bars": ["AAPL"],
        "updatedBars": ["AAPL"],
    }
    assert socket.closed


def test_late_correction_never_rewrites_prior_decision_but_changes_future_history(
    tmp_path: Path,
) -> None:
    service, request, _ = _service(tmp_path)
    created = service.create(request)
    _run(service.start(created.session_id, launch_task=False))
    first = _run(service.ingest(created.session_id, _bar(30, 100.0)))
    assert first.latest_event.market_snapshot.values[3].value == 100.0
    original_event = service.trace(created.session_id).timeline[0]
    _run(service.ingest(created.session_id, _bar(30, 101.0, revision=2, correction=True)))
    after_correction = service.trace(created.session_id)
    assert after_correction.timeline[0] == original_event
    assert after_correction.market_revisions[0].used_close == 100.0
    _run(service.ingest(created.session_id, _bar(31, 100.5)))
    future = service.trace(created.session_id).timeline[-1]
    difference = next(item for item in future.feature_snapshots if item.name == "difference")
    assert difference.value == pytest.approx(-0.5)
    assert future.signal_evaluation.signal == "FLAT"


def test_pause_duplicate_out_of_order_and_no_fake_disconnect_decisions(tmp_path: Path) -> None:
    service, request, _ = _service(tmp_path)
    session_id = service.create(request).session_id
    _run(service.start(session_id, launch_task=False))
    _run(service.ingest(session_id, _bar(30, 100.0)))
    _run(service.pause(session_id))
    _run(service.ingest(session_id, _bar(31, 101.0)))
    trace = service.trace(session_id)
    assert trace.timeline[-1].signal_evaluation.signal == "EVALUATION_SKIPPED_PAUSED"
    assert trace.timeline[-1].signal_evaluation.signal_id is None
    adapter = FakeLiveMarketDataAdapter()
    _run(adapter.connect())
    _run(adapter.simulate_disconnect())
    before = len(trace.timeline)
    assert len(service.trace(session_id).timeline) == before
    _run(service.resume(session_id))
    _run(service.ingest(session_id, _bar(32, 102.0)))
    _run(service.ingest(session_id, _bar(32, 102.0)))
    _run(service.ingest(session_id, _bar(29, 99.0)))
    snapshot = service.get(session_id)
    assert snapshot.duplicate_count == 1
    assert snapshot.out_of_order_count == 1
    full_timeline = service.trace(session_id).timeline
    assert len(full_timeline) == 3
    assert service.trace(session_id, limit=2).timeline == full_timeline[-2:]


def test_reconnect_gap_backfill_is_chronological_and_exactly_once(tmp_path: Path) -> None:
    service, request, _ = _service(tmp_path)
    session_id = service.create(request).session_id
    adapter = FakeLiveMarketDataAdapter()
    service.register_adapter(session_id, adapter)
    _run(service.start(session_id, launch_task=False))
    _run(service.ingest(session_id, _bar(30, 100.0)))
    _run(service.ingest(session_id, _bar(31, 101.0)))
    adapter.add_historical(_bar(32, 102.0), _bar(33, 103.0))
    backfilled = _run(service.reconnect_once(session_id, current_time=_time(35)))
    assert [item.event_time for item in backfilled] == [_time(32), _time(33)]
    _run(service.ingest(session_id, _bar(34, 104.0)))
    _run(service.ingest(session_id, _bar(33, 103.0, provider_event_id="ws-duplicate")))
    evaluated = [
        item.event_time
        for item in service.get(session_id).recent_market_events
        if item.disposition == "EVALUATED"
    ]
    assert evaluated == [_time(30), _time(31), _time(32), _time(33), _time(34)]
    assert service.get(session_id).duplicate_count == 1


def test_operational_health_and_log_are_durable_sequenced_and_secret_free(
    tmp_path: Path,
) -> None:
    service, request, _ = _service(tmp_path)
    session_id = service.create(request).session_id
    _run(service.start(session_id, launch_task=False))
    _run(service.ingest(session_id, _bar(30, 100.0)))
    adapter = FakeLiveMarketDataAdapter()
    adapter.add_historical(_bar(31, 101.0), _bar(32, 102.0))
    service.register_adapter(session_id, adapter)
    _run(service.reconnect_once(session_id, current_time=_time(34)))
    service._record_operation(
        session_id,
        "ERROR",
        "Safe diagnostic",
        {
            "api_key": "must-not-persist",
            "secret_key": "must-not-persist",
            "access_token": "must-not-persist",
            "attempt": 2,
        },
    )

    operations = service.operations(session_id).items
    assert [item.sequence for item in operations] == list(range(1, len(operations) + 1))
    assert operations[0].operation_type == "CREATED"
    assert {item.operation_type for item in operations} >= {
        "STARTED",
        "FEED_RECONNECTING",
        "BACKFILL_STARTED",
        "BACKFILL_COMPLETED",
        "FEED_RECONNECTED",
    }
    serialized = service.operations(session_id).model_dump_json()
    assert "must-not-persist" not in serialized
    assert operations[-1].metadata == {"attempt": 2}
    assert service.operations(session_id, limit=2).items == operations[-2:]
    assert service.repository.next_operation_sequence(session_id) == operations[-1].sequence + 1

    health = service.health(session_id)
    assert health.status == "RUNNING"
    assert health.feed_status == "CONNECTED"
    assert health.reconnect_count == 1
    assert health.backfill_count == 1
    assert health.backfilled_bar_count == 2
    assert health.last_latency_ms == pytest.approx(60_125.0)
    assert health.stale_seconds >= 0

    restarted = PaperSessionService(PaperSessionRepository(tmp_path))
    restarted_operations = restarted.operations(session_id).items
    assert [item.sequence for item in restarted_operations] == list(
        range(1, len(restarted_operations) + 1)
    )
    assert restarted_operations[-2].operation_type == "RECOVERY_STARTED"
    assert restarted_operations[-1].operation_type == "RECOVERY_COMPLETED"


def test_lifecycle_rejects_terminal_restart_and_duplicate_start_has_one_task(
    tmp_path: Path,
) -> None:
    service, request, _ = _service(tmp_path)
    adapter = FakeLiveMarketDataAdapter()
    service = PaperSessionService(
        service.repository,
        registry=service.registry,
        adapter_factory=lambda _: adapter,
    )
    session_id = service.create(request).session_id

    async def scenario() -> None:
        await service.start(session_id)
        first_task = service._tasks[session_id]
        with pytest.raises(ValueError, match="Cannot start a RUNNING session"):
            await service.start(session_id)
        assert service._tasks[session_id] is first_task
        await service.stop(session_id)
        with pytest.raises(ValueError, match="Cannot start a STOPPED session"):
            await service.start(session_id)
        with pytest.raises(ValueError, match="Cannot resume a STOPPED session"):
            await service.resume(session_id)
        await service.shutdown()

    asyncio.run(scenario())


def test_transient_feed_failure_is_not_mislabeled_as_a_market_data_gap(
    tmp_path: Path,
) -> None:
    class FailingPollAdapter(FakeLiveMarketDataAdapter):
        async def _failed_events(self) -> AsyncIterator[MarketBar]:
            raise ValueError("provider poll failed")
            yield  # pragma: no cover - makes this an async iterator

        def events(self) -> AsyncIterator[MarketBar]:
            return self._failed_events()

    service, request, _ = _service(tmp_path)
    adapter = FailingPollAdapter()
    service = PaperSessionService(
        service.repository,
        registry=service.registry,
        adapter_factory=lambda _: adapter,
    )
    session_id = service.create(request).session_id

    async def scenario() -> None:
        await service.start(session_id)
        for _ in range(100):
            if service.get(session_id).error_code == "MARKET_DATA_RECONNECTING":
                break
            await asyncio.sleep(0.01)
        snapshot = service.get(session_id)
        assert snapshot.feed_status == "RECONNECTING"
        assert snapshot.error_code == "MARKET_DATA_RECONNECTING"
        assert "gap detected" not in (snapshot.error_message or "").lower()
        await service.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("currency", "market_session"),
    (("CNY", "CN_REGULAR"), ("HKD", "HK_REGULAR"), ("USD", "US_REGULAR")),
)
def test_account_currency_survives_persistence_and_matches_market_session(
    tmp_path: Path,
    currency: Literal["CNY", "HKD", "USD"],
    market_session: Literal["CN_REGULAR", "HK_REGULAR", "US_REGULAR"],
) -> None:
    service, base_request, _ = _service(tmp_path)
    account = service.create_account(
        CreatePaperAccount(
            name=f"{currency} paper",
            initial_cash=100_000.0,
            currency=currency,
        )
    )

    persisted = service.get_account(account.account_id)
    assert persisted.currency == currency

    snapshot = service.create(
        base_request.model_copy(
            update={"account_id": account.account_id, "market_session": market_session}
        )
    )
    assert snapshot.account_id == account.account_id
    assert snapshot.market_session == market_session


def test_account_ownership_uses_active_manifests_and_survives_session_history(
    tmp_path: Path,
) -> None:
    service, base_request, _ = _service(tmp_path)
    account = service.create_account(
        CreatePaperAccount(name="Long-running paper", initial_cash=100_000.0)
    )
    request = base_request.model_copy(update={"account_id": account.account_id})
    first_session_id = service.create(request).session_id
    _run(service.stop(first_session_id))
    second_session_id = service.create(request).session_id

    current_account = service.get_account(account.account_id)
    service.repository.save_account(current_account.model_copy(update={"active_session_id": None}))
    with pytest.raises(ValueError, match="already has active session"):
        service.create(request)

    restarted = PaperSessionService(PaperSessionRepository(tmp_path))
    assert restarted.get(first_session_id).status == "STOPPED"
    assert restarted.get(second_session_id).status == "CREATED"
    assert restarted.get_account(account.account_id).active_session_id == second_session_id


def test_explicit_recovery_requires_a_matching_checkpoint_and_returns_paused(
    tmp_path: Path,
) -> None:
    service, request, _ = _service(tmp_path)
    session_id = service.create(request).session_id
    _run(service.start(session_id, launch_task=False))
    _run(service.ingest(session_id, _bar(30, 100.0)))
    manifest = service.repository.load_manifest(session_id)
    assert manifest.checkpoint is not None
    service.repository.save_manifest(
        manifest.model_copy(
            update={
                "checkpoint": manifest.checkpoint.model_copy(
                    update={"portfolio_hash": "sha256:tampered"}
                )
            }
        ),
        equity=service.get(session_id).account.equity,
    )

    restarted = PaperSessionService(PaperSessionRepository(tmp_path))
    assert restarted.recovery(session_id).status == "RECOVERY_DIVERGENCE"
    assert restarted.get(session_id).status == "ERROR"
    assert session_id not in restarted._tasks
    actual = restarted._session(session_id).checkpoint()
    failed_manifest = restarted.repository.load_manifest(session_id)
    restarted.repository.save_manifest(
        failed_manifest.model_copy(update={"checkpoint": actual}),
        equity=restarted.get(session_id).account.equity,
    )

    report = _run(restarted.recover(session_id))
    assert report.status == "RECOVERED"
    assert report.recorded_portfolio_hash == report.recovered_portfolio_hash
    assert report.recorded_trace_hash == report.recovered_trace_hash
    recovered = restarted.get(session_id)
    assert recovered.status == "PAUSED"
    assert recovered.recovery_status == "READY"
    assert session_id not in restarted._tasks


def test_strategy_failure_is_terminal_and_does_not_retry_input(tmp_path: Path) -> None:
    source = tmp_path / "faulty_strategy.py"
    source.write_text(
        STRATEGY_SOURCE.replace(
            "        self.calls += 1\n        history = context.history",
            "        self.calls += 1\n"
            "        if self.calls == 2:\n"
            "            raise RuntimeError('deterministic strategy failure')\n"
            "        history = context.history",
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry(tmp_path)
    registry.add(source)
    service = PaperSessionService(PaperSessionRepository(tmp_path), registry=registry)
    request = CreatePaperSession(
        strategy_id="test.live-correction",
        symbols=("AAPL",),
        provider="fake",
        feed="iex",
    )
    session_id = service.create(request).session_id
    _run(service.start(session_id, launch_task=False))
    _run(service.ingest(session_id, _bar(30, 100.0)))
    with pytest.raises(RuntimeError, match="deterministic strategy failure"):
        _run(service.ingest(session_id, _bar(31, 101.0)))
    snapshot = service.get(session_id)
    assert snapshot.status == "ERROR"
    assert snapshot.error_code == "StrategyRuntimeError"
    with pytest.raises(ValueError, match="Cannot ingest into a ERROR session"):
        _run(service.ingest(session_id, _bar(31, 101.0)))
    assert service.get(session_id).last_event_sequence == snapshot.last_event_sequence


def test_background_runtime_and_sse_continue_without_frontend_steps(tmp_path: Path) -> None:
    service, request, _ = _service(tmp_path)
    adapter = FakeLiveMarketDataAdapter()
    service = PaperSessionService(
        service.repository,
        registry=service.registry,
        adapter_factory=lambda _: adapter,
    )
    session_id = service.create(request).session_id

    async def scenario() -> None:
        stream = service.stream(session_id)
        initial = await stream.__anext__()
        assert json.loads(initial)["status"] == "CREATED"
        await service.start(session_id)
        for _ in range(100):
            if adapter.connection_state == "CONNECTED" and adapter.symbols:
                break
            await asyncio.sleep(0.001)
        await adapter.emit(_bar(30, 100.0))
        for _ in range(100):
            if service.get(session_id).last_event_sequence == 1:
                break
            await asyncio.sleep(0.001)
        update = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        assert json.loads(update)["status"] in {"RUNNING", "CREATED"}
        assert service.get(session_id).last_event_sequence == 1
        await service.stop(session_id)
        await stream.aclose()  # type: ignore[attr-defined]
        await service.shutdown()

    asyncio.run(scenario())


def test_recovery_mismatch_stops_session_in_error(tmp_path: Path) -> None:
    service, request, _ = _service(tmp_path)
    session_id = service.create(request).session_id
    _run(service.start(session_id, launch_task=False))
    _run(service.ingest(session_id, _bar(30, 100.0)))
    manifest = service.repository.load_manifest(session_id)
    assert manifest.checkpoint is not None
    tampered = manifest.model_copy(
        update={
            "checkpoint": manifest.checkpoint.model_copy(
                update={"portfolio_hash": "sha256:tampered"}
            )
        }
    )
    service.repository.save_manifest(tampered, equity=100_000)
    restarted = PaperSessionService(PaperSessionRepository(tmp_path))
    snapshot = restarted.get(session_id)
    assert snapshot.status == "ERROR"
    assert snapshot.recovery_status == "RECOVERY_DIVERGENCE"
    assert snapshot.error_code == "RECOVERY_DIVERGENCE"
    stopped = _run(restarted.stop(session_id))
    assert stopped.status == "STOPPED"
    assert stopped.research_run_id is not None
    assert stopped.reference_run_id is not None


def _equivalence(snapshot: object, trace: object) -> dict[str, object]:
    snapshot_data = snapshot.model_dump(mode="json")  # type: ignore[attr-defined]
    trace_data = trace.model_dump(mode="json")  # type: ignore[attr-defined]
    return {
        "decisions": [item["signal_evaluation"] for item in trace_data["timeline"]],
        "orders": [item["order_events"] for item in trace_data["timeline"]],
        "executions": [item["execution_events"] for item in trace_data["timeline"]],
        "positions": snapshot_data["account"]["positions"],
        "fees": snapshot_data["account"]["cumulative_fees"],
        "slippage": snapshot_data["account"]["cumulative_slippage"],
        "pnl": snapshot_data["account"]["net_pnl"],
        "equity": snapshot_data["account"]["equity"],
        "trace": trace_data["timeline"],
    }


def test_restart_recovery_is_fully_equivalent_and_strategy_source_is_snapshotted(
    tmp_path: Path,
) -> None:
    continuous_root = tmp_path / "continuous"
    recovered_root = tmp_path / "recovered"
    continuous_root.mkdir()
    recovered_root.mkdir()
    continuous, request_a, _ = _service(continuous_root)
    interrupted, request_b, external_source = _service(recovered_root)
    session_a = continuous.create(request_a).session_id
    session_b = interrupted.create(request_b).session_id
    _run(continuous.start(session_a, launch_task=False))
    _run(interrupted.start(session_b, launch_task=False))
    bars = (_bar(30, 100.0), _bar(31, 102.0), _bar(32, 99.0), _bar(33, 103.0))
    for bar in bars:
        _run(continuous.ingest(session_a, bar))
    for bar in bars[:2]:
        _run(interrupted.ingest(session_b, bar))
    external_source.write_text(STRATEGY_SOURCE.replace("target = 10.0", "target = 99.0"))
    recovered = PaperSessionService(PaperSessionRepository(recovered_root))
    assert recovered.get(session_b).recovery_status == "READY"
    for bar in bars[2:]:
        _run(recovered.ingest(session_b, bar))
    assert _equivalence(continuous.get(session_a), continuous.trace(session_a)) == _equivalence(
        recovered.get(session_b), recovered.trace(session_b)
    )
    assert (
        recovered.repository.load_manifest(session_b).checkpoint
        == recovered._session(session_b).checkpoint()
    )


def test_schema_v1_to_v4_migration_preserves_run_table(tmp_path: Path) -> None:
    workspace = tmp_path / "migration-workspace"
    vqd = workspace / ".vqd"
    workspace.mkdir()
    vqd.mkdir()
    database = vqd / "vqd.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES('schema_version', '1')")
        connection.execute("CREATE TABLE legacy_marker(value TEXT)")
        connection.execute("INSERT INTO legacy_marker VALUES('preserved')")
    repository = PaperSessionRepository(workspace)
    assert repository.database_path == database
    assert RunRepository(workspace).schema_version() == 4
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserved"
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='paper_sessions'"
        ).fetchone()


def test_persistent_account_exactly_once_orders_fills_recovery_and_paper_run(
    tmp_path: Path,
) -> None:
    service, base_request, _ = _service(tmp_path)
    account = service.create_account(
        CreatePaperAccount(name="Primary paper", initial_cash=125_000.0)
    )
    request = base_request.model_copy(update={"account_id": account.account_id})
    session_id = service.create(request).session_id
    assert service.get_account(account.account_id).active_session_id == session_id
    with pytest.raises(ValueError, match="already has active session"):
        service.create(request)

    _run(service.start(session_id, launch_task=False))
    for bar in (_bar(30, 100.0), _bar(31, 102.0), _bar(32, 99.0)):
        _run(service.ingest(session_id, bar))
    before_duplicate = service.get(session_id)
    assert len(before_duplicate.orders) == 1
    assert len(before_duplicate.fills) == 1
    assert before_duplicate.fills[0].source_order_id == before_duplicate.orders[0].order_id
    _run(service.ingest(session_id, _bar(32, 99.0, provider_event_id="duplicate-wire-id")))
    after_duplicate = service.get(session_id)
    assert len(after_duplicate.orders) == 1
    assert len(after_duplicate.fills) == 1
    assert after_duplicate.last_processed_market_event_id is not None
    assert after_duplicate.market_watermark == _time(32)
    persisted_account = service.get_account(account.account_id)
    assert persisted_account.cash == after_duplicate.account.cash
    assert persisted_account.positions == after_duplicate.account.positions
    assert persisted_account.equity == after_duplicate.account.equity

    recovered = PaperSessionService(PaperSessionRepository(tmp_path))
    recovered_snapshot = recovered.get(session_id)
    assert len(recovered_snapshot.orders) == 1
    assert len(recovered_snapshot.fills) == 1
    assert recovered.runtime_consistency(session_id).status == "MATCH"

    stopped = _run(recovered.stop(session_id))
    assert stopped.research_run_id is not None
    assert stopped.reference_run_id is not None
    assert recovered.get_account(account.account_id).active_session_id is None
    paper_manifest = RunRepository(tmp_path).get_manifest(stopped.research_run_id)
    assert paper_manifest.run_type == "PAPER"
    assert paper_manifest.trace_id is not None
    assert paper_manifest.artifacts.recorded_market_events_sha256 is not None
    assert paper_manifest.artifacts.runtime_consistency_sha256 is not None
    assert RunRepository(tmp_path).load_trace_for_run(stopped.research_run_id).timeline
    reference_manifest = RunRepository(tmp_path).get_manifest(stopped.reference_run_id)
    assert reference_manifest.run_type == "REFERENCE"

    repository = RunRepository(tmp_path)
    paper_trace = repository.load_trace_for_run(stopped.research_run_id)
    backtest_run_id = repository.new_run_id()
    running_backtest = paper_manifest.model_copy(
        update={
            "run_id": backtest_run_id,
            "run_type": "BACKTEST",
            "run_fingerprint": "sha256:descriptive-historical-run",
            "status": "RUNNING",
            "completed_at": None,
            "trace_id": None,
            "artifacts": paper_manifest.artifacts.model_copy(
                update={
                    "recorded_market_events_sha256": None,
                    "runtime_consistency_sha256": None,
                }
            ),
            "dataset": paper_manifest.dataset.model_copy(
                update={
                    "dataset_id": "historical-aapl",
                    "content_fingerprint": "sha256:different-market-path",
                }
            ),
        }
    )
    source = repository.strategy_path(stopped.research_run_id).read_bytes()
    repository.create_running(running_backtest, source)
    completed_backtest = running_backtest.model_copy(
        update={
            "status": "COMPLETED",
            "completed_at": paper_manifest.completed_at,
            "trace_id": f"trace-{backtest_run_id}",
            "metrics": paper_manifest.metrics,
        }
    )
    repository.finalize(completed_backtest, paper_trace)
    paper_manifest_before = repository.get_manifest(stopped.research_run_id)
    paper_trace_before = repository.load_trace_for_run(stopped.research_run_id)
    validation = validate_backtest_vs_paper(
        repository,
        recovered,
        ValidationRequest(
            backtest_run_id=backtest_run_id,
            paper_run_id=stopped.research_run_id,
        ),
    )
    assert repository.get_manifest(stopped.research_run_id) == paper_manifest_before
    assert repository.load_trace_for_run(stopped.research_run_id) == paper_trace_before
    assert validation.historical_comparability == "DESCRIPTIVE_ONLY"
    assert validation.strict_recorded_feed_status == "MATCH"
    assert validation.reference_run_id == stopped.reference_run_id
    assert validation.first_divergence.status == "MATCH"
    assert [component.layer for component in validation.pnl_attribution.components] == [
        "MARKET_PATH",
        "DECISION",
        "EXECUTION_PRICE",
        "DELAY",
        "FEES",
        "SLIPPAGE",
        "RESIDUAL",
    ]
    assert validation.pnl_attribution.reconciliation_error == pytest.approx(0.0)
    assert (
        validation.pnl_attribution.attributed_total
        + validation.pnl_attribution.residual_unattributed
        == pytest.approx(validation.pnl_attribution.total_difference)
    )
    validation_path = tmp_path / ".vqd" / "validations" / f"{validation.report_id}.json"
    assert validation_path.exists()
    assert RunRepository(tmp_path).load_validation(validation.report_id) == validation
    RunRepository(tmp_path).save_validation(validation)
    with pytest.raises(ArtifactIntegrityError, match="immutable"):
        RunRepository(tmp_path).save_validation(
            validation.model_copy(update={"note": "changed after creation"})
        )


def test_alpaca_paper_broker_partial_fills_are_incremental_and_recoverable(
    tmp_path: Path,
) -> None:
    service, base_request, _ = _service(tmp_path)
    broker = FakePaperBroker()
    request = base_request.model_copy(update={"execution_mode": "ALPACA_PAPER"})
    session_id = service.create(request).session_id
    service.register_broker_adapter(session_id, broker)
    started = _run(service.start(session_id, launch_task=False))
    assert started.execution_mode == "ALPACA_PAPER"
    assert started.broker_status == "CONNECTED"
    assert started.broker_account.account_id == "alpaca-paper-account"

    for bar in (_bar(30, 100.0), _bar(31, 102.0), _bar(32, 99.0)):
        _run(service.ingest(session_id, bar))
    submitted = service.get(session_id)
    assert len(submitted.orders) == 1
    assert submitted.orders[0].status == "SUBMITTED"
    assert submitted.fills == ()
    assert submitted.account.positions.get("AAPL", 0.0) == 0.0

    provider_id = submitted.orders[0].provider_order_id
    assert provider_id is not None
    first = broker.orders[provider_id]
    broker.orders[provider_id] = first.model_copy(
        update={
            "status": "PARTIALLY_FILLED",
            "raw_status": "partially_filled",
            "filled_quantity": 4.0,
            "average_fill_price": 101.0,
            "updated_at": first.updated_at + timedelta(seconds=1),
        }
    )
    _run(service._refresh_broker_state(service._session(session_id)))
    partial = service.get(session_id)
    assert partial.orders[0].status == "PARTIALLY_FILLED"
    assert partial.account.positions["AAPL"] == pytest.approx(4.0)
    assert len(partial.fills) == 1
    assert partial.fills[0].quantity == pytest.approx(4.0)

    second = broker.orders[provider_id]
    broker.orders[provider_id] = second.model_copy(
        update={
            "status": "FILLED",
            "raw_status": "filled",
            "filled_quantity": 10.0,
            "average_fill_price": 102.0,
            "terminal_at": second.updated_at + timedelta(seconds=1),
            "updated_at": second.updated_at + timedelta(seconds=1),
        }
    )
    _run(service._refresh_broker_state(service._session(session_id)))
    _run(service._refresh_broker_state(service._session(session_id)))
    filled = service.get(session_id)
    assert filled.orders[0].status == "FILLED"
    assert filled.account.positions["AAPL"] == pytest.approx(10.0)
    assert [item.quantity for item in filled.fills] == pytest.approx([4.0, 6.0])
    assert len(filled.recent_broker_events) == 3
    assert service.trace(session_id).broker_events == filled.recent_broker_events

    recovered = PaperSessionService(PaperSessionRepository(tmp_path))
    recovered_snapshot = recovered.get(session_id)
    assert recovered_snapshot.account.positions["AAPL"] == pytest.approx(10.0)
    assert len(recovered_snapshot.fills) == 2
    assert recovered.runtime_consistency(session_id).status == "MATCH"


def test_alpaca_paper_broker_cancel_uses_provider_lifecycle(tmp_path: Path) -> None:
    service, base_request, _ = _service(tmp_path)
    broker = FakePaperBroker()
    session_id = service.create(
        base_request.model_copy(update={"execution_mode": "ALPACA_PAPER"})
    ).session_id
    service.register_broker_adapter(session_id, broker)
    _run(service.start(session_id, launch_task=False))
    for bar in (_bar(30, 100.0), _bar(31, 102.0), _bar(32, 99.0)):
        _run(service.ingest(session_id, bar))
    order = service.get(session_id).orders[0]

    cancelled = _run(service.cancel_order(session_id, order.order_id))

    assert cancelled.orders[0].status == "CANCELLED"
    assert cancelled.orders[0].raw_status == "canceled"
    assert cancelled.recent_broker_events[-1].event_type == "canceled"
    assert cancelled.fills == ()


def test_api_provider_status_and_persistent_created_session() -> None:
    client = TestClient(app)
    provider_response = client.get("/api/market-data/providers")
    assert provider_response.status_code == 200
    assert "secret" not in provider_response.text.lower()
    account_response = client.post(
        "/api/paper-accounts",
        json={"name": "API paper account", "initial_cash": 250_000},
    )
    assert account_response.status_code == 201
    account_id = account_response.json()["account_id"]
    assert client.get(f"/api/paper-accounts/{account_id}").status_code == 200
    assert account_id in {
        item["account_id"] for item in client.get("/api/paper-accounts").json()["items"]
    }
    created = client.post(
        "/api/paper-sessions",
        json={
            "account_id": account_id,
            "strategy_id": "pairs-trading",
            "symbols": ["AAPL", "MSFT"],
            "provider": "alpaca",
            "feed": "iex",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert created.json()["account_id"] == account_id
    assert created.json()["symbols"] == ["AAPL", "MSFT"]
    assert client.get(f"/api/paper-sessions/{session_id}").status_code == 200
    assert client.get(f"/api/paper-sessions/{session_id}/trace").json()["timeline"] == []
    health = client.get(f"/api/paper/sessions/{session_id}/health")
    assert health.status_code == 200
    assert health.json()["status"] == "CREATED"
    operations = client.get(f"/api/paper/sessions/{session_id}/operations")
    assert operations.status_code == 200
    assert operations.json()["items"][0]["operation_type"] == "CREATED"
    recovery = client.get(f"/api/paper/sessions/{session_id}/recovery")
    assert recovery.status_code == 200
    assert recovery.json()["status"] == "READY"
    invalid_recover = client.post(f"/api/paper/sessions/{session_id}/recover")
    assert invalid_recover.status_code == 409
    stopped = client.post(f"/api/paper-sessions/{session_id}/stop").json()
    assert stopped["status"] == "STOPPED"
    assert stopped["research_run_id"] is not None
    run = client.get(f"/api/runs/{stopped['research_run_id']}")
    assert run.status_code == 200
    assert run.json()["manifest"]["run_type"] == "PAPER"
    final_operations = client.get(f"/api/paper/sessions/{session_id}/operations").json()["items"]
    assert [item["operation_type"] for item in final_operations[-2:]] == [
        "STOP_REQUESTED",
        "STOPPED",
    ]
    recent_operation = client.get(f"/api/paper/sessions/{session_id}/operations?limit=1").json()[
        "items"
    ]
    assert [item["operation_type"] for item in recent_operation] == ["STOPPED"]


def test_actual_python_backend_process_restart_recovers_and_continues(tmp_path: Path) -> None:
    source = tmp_path / "live_strategy.py"
    source.write_text(STRATEGY_SOURCE, encoding="utf-8")
    backend_root = Path(__file__).parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(backend_root),
        "VQD_WORKSPACE": str(tmp_path),
    }
    create_script = f"""
import asyncio
from datetime import datetime, UTC, timedelta
from app.main import app
from app.paper import CreatePaperSession, PaperSessionRepository, PaperSessionService
from app.market_data import MarketBar
from app.sdk.registry import StrategyRegistry
root = {str(tmp_path)!r}
registry = StrategyRegistry(root)
registry.add({str(source)!r})
service = PaperSessionService(PaperSessionRepository(root), registry=registry)
session_id = service.create(CreatePaperSession(
    strategy_id="test.live-correction",
    symbols=("AAPL",),
    provider="fake",
)).session_id
asyncio.run(service.start(session_id, launch_task=False))
def bar(m, c):
    t=datetime(2025,1,2,14,m,tzinfo=UTC)
    return MarketBar(
        symbol="AAPL", event_time=t,
        available_at=t+timedelta(minutes=1),
        received_at=t+timedelta(minutes=1),
        open=c, high=c, low=c, close=c, volume=1,
        provider="fake", feed="iex", provider_event_id=f"p:{{m}}",
    )
asyncio.run(service.ingest(session_id, bar(30, 100)))
asyncio.run(service.ingest(session_id, bar(31, 102)))
print(session_id)
"""
    first = subprocess.run(
        [sys.executable, "-c", create_script],
        check=True,
        capture_output=True,
        text=True,
        cwd=backend_root,
        env=environment,
    )
    session_id = first.stdout.strip().splitlines()[-1]
    continue_script = f"""
import asyncio, json
from datetime import datetime, UTC, timedelta
from app.main import app
from app.paper import PaperSessionRepository, PaperSessionService
from app.market_data import MarketBar
service = PaperSessionService(PaperSessionRepository({str(tmp_path)!r}))
session_id = {session_id!r}
t=datetime(2025,1,2,14,32,tzinfo=UTC)
bar=MarketBar(
    symbol="AAPL", event_time=t,
    available_at=t+timedelta(minutes=1),
    received_at=t+timedelta(minutes=1),
    open=99, high=99, low=99, close=99, volume=1,
    provider="fake", feed="iex", provider_event_id="p:32",
)
asyncio.run(service.ingest(session_id, bar))
snapshot=service.get(session_id)
print(json.dumps({{
    "recovery": snapshot.recovery_status,
    "events": snapshot.last_event_sequence,
    "timeline": len(service.trace(session_id).timeline),
}}))
"""
    second = subprocess.run(
        [sys.executable, "-c", continue_script],
        check=True,
        capture_output=True,
        text=True,
        cwd=backend_root,
        env=environment,
    )
    result = json.loads(second.stdout.strip().splitlines()[-1])
    assert result == {"recovery": "READY", "events": 3, "timeline": 3}

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    def start_backend() -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=backend_root,
            env=environment,
            text=True,
        )
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2).status_code == 200:
                    return process
            except httpx.HTTPError:
                time.sleep(0.02)
        process.terminate()
        raise AssertionError("Backend process did not become healthy")

    for _ in range(2):
        backend = start_backend()
        try:
            response = httpx.get(
                f"http://127.0.0.1:{port}/api/paper-sessions/{session_id}", timeout=1.0
            )
            assert response.status_code == 200
            assert response.json()["last_event_sequence"] == 3
            assert response.json()["recovery_status"] == "READY"
        finally:
            backend.terminate()
            backend.wait(timeout=5)


@pytest.mark.skipif(
    not os.environ.get("ALPACA_API_KEY") or not os.environ.get("ALPACA_SECRET_KEY"),
    reason="Alpaca credentials unavailable",
)
def test_optional_real_alpaca_authentication() -> None:
    async def scenario() -> None:
        adapter = AlpacaStockMarketDataAdapter()
        await adapter.connect()
        await adapter.subscribe(("AAPL",))
        assert adapter.connection_state == "CONNECTED"
        await adapter.disconnect()

    asyncio.run(scenario())


def test_explicit_recovery_does_not_steal_account_from_new_active_session(
    tmp_path: Path,
) -> None:
    service, base_request, _ = _service(tmp_path)
    account = service.create_account(
        CreatePaperAccount(name="Recovery ownership", initial_cash=100_000.0)
    )
    request = base_request.model_copy(update={"account_id": account.account_id})
    failed_session_id = service.create(request).session_id
    _run(service.start(failed_session_id, launch_task=False))
    _run(service.ingest(failed_session_id, _bar(30, 100.0)))
    manifest = service.repository.load_manifest(failed_session_id)
    assert manifest.checkpoint is not None
    service.repository.save_manifest(
        manifest.model_copy(
            update={
                "checkpoint": manifest.checkpoint.model_copy(
                    update={"portfolio_hash": "sha256:tampered"}
                )
            }
        ),
        equity=service.get(failed_session_id).account.equity,
    )

    restarted = PaperSessionService(
        PaperSessionRepository(tmp_path), registry=StrategyRegistry(tmp_path)
    )
    actual = restarted._session(failed_session_id).checkpoint()
    failed_manifest = restarted.repository.load_manifest(failed_session_id)
    restarted.repository.save_manifest(
        failed_manifest.model_copy(update={"checkpoint": actual}),
        equity=restarted.get(failed_session_id).account.equity,
    )
    new_session_id = restarted.create(request).session_id
    operation_count = len(restarted.operations(failed_session_id).items)

    with pytest.raises(ValueError, match="already owned by active session"):
        _run(restarted.recover(failed_session_id))

    assert restarted.get_account(account.account_id).active_session_id == new_session_id
    assert restarted.get(failed_session_id).status == "ERROR"
    assert restarted.get(failed_session_id).recovery_status == "RECOVERY_DIVERGENCE"
    assert len(restarted.operations(failed_session_id).items) == operation_count


def test_startup_replay_failure_keeps_error_session_retrievable(tmp_path: Path) -> None:
    source = tmp_path / "faulty_recovery_strategy.py"
    source.write_text(
        STRATEGY_SOURCE.replace(
            "        self.calls += 1\n        history = context.history",
            "        self.calls += 1\n"
            "        if self.calls == 2:\n"
            "            raise RuntimeError('deterministic strategy failure')\n"
            "        history = context.history",
        ),
        encoding="utf-8",
    )
    registry = StrategyRegistry(tmp_path)
    registry.add(source)
    service = PaperSessionService(PaperSessionRepository(tmp_path), registry=registry)
    request = CreatePaperSession(
        strategy_id="test.live-correction",
        symbols=("AAPL",),
        provider="fake",
        feed="iex",
    )
    session_id = service.create(request).session_id
    _run(service.start(session_id, launch_task=False))
    _run(service.ingest(session_id, _bar(30, 100.0)))
    with pytest.raises(RuntimeError, match="deterministic strategy failure"):
        _run(service.ingest(session_id, _bar(31, 101.0)))
    assert service.get(session_id).status == "ERROR"

    restarted = PaperSessionService(PaperSessionRepository(tmp_path))
    snapshot = restarted.get(session_id)
    assert snapshot.status == "ERROR"
    assert snapshot.recovery_status == "RECOVERY_DIVERGENCE"
    assert restarted.health(session_id).status == "ERROR"
    assert restarted.operations(session_id).items
    assert restarted.recovery(session_id).status == "RECOVERY_DIVERGENCE"
    stopped = _run(restarted.stop(session_id))
    assert stopped.status == "STOPPED"
    assert stopped.research_run_id is not None
    assert stopped.reference_run_id is not None
