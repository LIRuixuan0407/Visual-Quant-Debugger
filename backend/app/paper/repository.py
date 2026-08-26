from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.paper.models import (
    MarketJournalEntry,
    PaperAccount,
    PaperBrokerEvent,
    PaperFill,
    PaperOrder,
    PaperSessionManifest,
    PaperTrace,
    RuntimeConsistencyReport,
)
from app.storage import ensure_schema_version
from app.workspace import default_workspace_root

PAPER_SESSION_ID = re.compile(r"^paper-[0-9a-f]{24}$")
PAPER_ACCOUNT_ID = re.compile(r"^paper-account-[0-9a-f]{24}$")


class PaperSessionNotFoundError(KeyError):
    pass


class PaperSessionRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.vqd_root = self.workspace_root / ".vqd"
        self.paper_root = self.vqd_root / "paper-sessions"
        self.database_path = self.vqd_root / "vqd.sqlite"
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.paper_root.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            ensure_schema_version(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    feed_status TEXT NOT NULL,
                    recovery_status TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_fingerprint TEXT NOT NULL,
                    symbols_json TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    stopped_at TEXT,
                    updated_at TEXT NOT NULL,
                    last_market_event TEXT,
                    last_event_sequence INTEGER NOT NULL DEFAULT 0,
                    equity REAL NOT NULL,
                    error_code TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(paper_sessions)").fetchall()
            }
            if "account_id" not in columns:
                connection.execute("ALTER TABLE paper_sessions ADD COLUMN account_id TEXT")
            if "research_run_id" not in columns:
                connection.execute("ALTER TABLE paper_sessions ADD COLUMN research_run_id TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    initial_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    positions_json TEXT NOT NULL,
                    equity REAL NOT NULL,
                    cumulative_fees REAL NOT NULL,
                    cumulative_slippage REAL NOT NULL,
                    active_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    market_event_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_fills (
                    fill_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    market_event_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_orders_session ON paper_orders(session_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_fills_session ON paper_fills(session_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_created ON paper_sessions(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_sessions(status)"
            )
            connection.commit()

    @staticmethod
    def new_session_id() -> str:
        return f"paper-{secrets.token_hex(12)}"

    @staticmethod
    def new_account_id() -> str:
        return f"paper-account-{secrets.token_hex(12)}"

    @staticmethod
    def _validate_account_id(account_id: str) -> None:
        if not PAPER_ACCOUNT_ID.fullmatch(account_id):
            raise ValueError(f"Invalid paper account id '{account_id}'")

    def create_account(self, account: PaperAccount) -> None:
        self._validate_account_id(account.account_id)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO paper_accounts(
                    account_id, name, currency, initial_cash, cash, positions_json,
                    equity, cumulative_fees, cumulative_slippage, active_session_id,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account.account_id,
                    account.name,
                    account.currency,
                    account.initial_cash,
                    account.cash,
                    json.dumps(account.positions, sort_keys=True),
                    account.equity,
                    account.cumulative_fees,
                    account.cumulative_slippage,
                    account.active_session_id,
                    account.created_at.isoformat(),
                    account.updated_at.isoformat(),
                ),
            )
            connection.commit()

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> PaperAccount:
        from datetime import datetime

        return PaperAccount(
            account_id=str(row["account_id"]),
            name=str(row["name"]),
            currency="USD",
            initial_cash=float(row["initial_cash"]),
            cash=float(row["cash"]),
            positions=json.loads(row["positions_json"]),
            equity=float(row["equity"]),
            cumulative_fees=float(row["cumulative_fees"]),
            cumulative_slippage=float(row["cumulative_slippage"]),
            active_session_id=None
            if row["active_session_id"] is None
            else str(row["active_session_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def get_account(self, account_id: str) -> PaperAccount:
        self._validate_account_id(account_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM paper_accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
        if row is None:
            raise PaperSessionNotFoundError(account_id)
        return self._account_from_row(row)

    def list_accounts(self) -> tuple[PaperAccount, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_accounts ORDER BY created_at DESC"
            ).fetchall()
        return tuple(self._account_from_row(row) for row in rows)

    def save_account(self, account: PaperAccount) -> None:
        self._validate_account_id(account.account_id)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE paper_accounts SET name=?, currency=?, initial_cash=?, cash=?,
                    positions_json=?, equity=?, cumulative_fees=?, cumulative_slippage=?,
                    active_session_id=?, updated_at=? WHERE account_id=?
                """,
                (
                    account.name,
                    account.currency,
                    account.initial_cash,
                    account.cash,
                    json.dumps(account.positions, sort_keys=True),
                    account.equity,
                    account.cumulative_fees,
                    account.cumulative_slippage,
                    account.active_session_id,
                    account.updated_at.isoformat(),
                    account.account_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperSessionNotFoundError(account.account_id)
            connection.commit()

    def session_directory(self, session_id: str) -> Path:
        if not PAPER_SESSION_ID.fullmatch(session_id):
            raise ValueError(f"Invalid paper session id '{session_id}'")
        target = (self.paper_root / session_id).resolve()
        if target.parent != self.paper_root.resolve():
            raise ValueError("Paper session artifact path escaped the workspace")
        return target

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def create(self, manifest: PaperSessionManifest, strategy_source: bytes) -> None:
        target = self.session_directory(manifest.session_id)
        target.mkdir(parents=True, exist_ok=False)
        try:
            self._atomic_write(target / "strategy.py", strategy_source)
            self._atomic_write(target / "market-events.jsonl", b"")
            self._atomic_write(target / "broker-events.jsonl", b"")
            self.save_manifest(manifest, equity=manifest.initial_cash, insert=True)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    def save_manifest(
        self, manifest: PaperSessionManifest, *, equity: float, insert: bool = False
    ) -> None:
        target = self.session_directory(manifest.session_id)
        self._atomic_write(
            target / "manifest.json", (manifest.model_dump_json(indent=2) + "\n").encode()
        )
        values = (
            manifest.status,
            manifest.feed_status,
            manifest.recovery_status,
            manifest.strategy_id,
            manifest.strategy_fingerprint,
            manifest.model_dump_json(include={"symbols"}),
            manifest.provider,
            manifest.feed,
            manifest.created_at.isoformat(),
            None if manifest.started_at is None else manifest.started_at.isoformat(),
            None if manifest.stopped_at is None else manifest.stopped_at.isoformat(),
            manifest.updated_at.isoformat(),
            None if manifest.last_market_event is None else manifest.last_market_event.isoformat(),
            0 if manifest.checkpoint is None else manifest.checkpoint.last_event_sequence,
            equity,
            manifest.error_code,
            manifest.account_id or None,
            manifest.research_run_id,
            manifest.session_id,
        )
        with self._connection() as connection:
            if insert:
                connection.execute(
                    """
                    INSERT INTO paper_sessions(
                        status, feed_status, recovery_status, strategy_id,
                        strategy_fingerprint, symbols_json, provider, feed,
                        created_at, started_at, stopped_at, updated_at,
                        last_market_event, last_event_sequence, equity, error_code,
                        account_id, research_run_id, session_id
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE paper_sessions SET
                        status=?, feed_status=?, recovery_status=?, strategy_id=?,
                        strategy_fingerprint=?, symbols_json=?, provider=?, feed=?,
                        created_at=?, started_at=?, stopped_at=?, updated_at=?,
                        last_market_event=?, last_event_sequence=?, equity=?, error_code=?,
                        account_id=?, research_run_id=?
                    WHERE session_id=?
                    """,
                    values,
                )
                if cursor.rowcount != 1:
                    raise PaperSessionNotFoundError(manifest.session_id)
            connection.commit()

    def load_manifest(self, session_id: str) -> PaperSessionManifest:
        path = self.session_directory(session_id) / "manifest.json"
        if not path.is_file():
            raise PaperSessionNotFoundError(session_id)
        return PaperSessionManifest.model_validate_json(path.read_bytes())

    def list_manifests(self) -> tuple[PaperSessionManifest, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT session_id FROM paper_sessions ORDER BY created_at DESC"
            ).fetchall()
        return tuple(self.load_manifest(str(row["session_id"])) for row in rows)

    def strategy_path(self, session_id: str) -> Path:
        return self.session_directory(session_id) / "strategy.py"

    def append_session_journal(self, session_id: str, entry: MarketJournalEntry) -> None:
        path = self.session_directory(session_id) / "market-events.jsonl"
        with path.open("ab") as handle:
            handle.write((entry.model_dump_json() + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())

    def read_journal(self, session_id: str) -> tuple[MarketJournalEntry, ...]:
        path = self.session_directory(session_id) / "market-events.jsonl"
        if not path.is_file():
            raise PaperSessionNotFoundError(session_id)
        return tuple(
            MarketJournalEntry.model_validate_json(line)
            for line in path.read_bytes().splitlines()
            if line.strip()
        )

    def append_broker_event(self, session_id: str, event: PaperBrokerEvent) -> None:
        path = self.session_directory(session_id) / "broker-events.jsonl"
        with path.open("ab") as handle:
            handle.write((event.model_dump_json() + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())

    def read_broker_events(self, session_id: str) -> tuple[PaperBrokerEvent, ...]:
        path = self.session_directory(session_id) / "broker-events.jsonl"
        if not path.is_file():
            return ()
        return tuple(
            PaperBrokerEvent.model_validate_json(line)
            for line in path.read_bytes().splitlines()
            if line.strip()
        )

    def save_trace(self, session_id: str, trace: PaperTrace) -> None:
        self._atomic_write(
            self.session_directory(session_id) / "trace.json",
            (trace.model_dump_json(indent=2) + "\n").encode(),
        )

    def save_orders_and_fills(
        self, orders: tuple[PaperOrder, ...], fills: tuple[PaperFill, ...]
    ) -> None:
        with self._connection() as connection:
            for order in orders:
                payload = order.model_dump_json()
                connection.execute(
                    """
                    INSERT INTO paper_orders(
                        order_id, account_id, session_id, market_event_id, payload_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        market_event_id=excluded.market_event_id,
                        payload_json=excluded.payload_json
                    """,
                    (
                        order.order_id,
                        order.account_id,
                        order.session_id,
                        order.market_event_id,
                        payload,
                        order.submitted_at.isoformat(),
                    ),
                )
            for fill in fills:
                payload = fill.model_dump_json()
                existing = connection.execute(
                    "SELECT payload_json FROM paper_fills WHERE fill_id = ?", (fill.fill_id,)
                ).fetchone()
                if existing is not None and str(existing["payload_json"]) != payload:
                    raise RuntimeError(f"Fill identity collision for {fill.fill_id}")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO paper_fills(
                        fill_id, execution_id, account_id, session_id, market_event_id,
                        payload_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.fill_id,
                        fill.execution_id,
                        fill.account_id,
                        fill.session_id,
                        fill.market_event_id,
                        payload,
                        fill.executed_at.isoformat(),
                    ),
                )
            connection.commit()

    def list_orders(self, session_id: str) -> tuple[PaperOrder, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM paper_orders WHERE session_id=? "
                "ORDER BY created_at, order_id",
                (session_id,),
            ).fetchall()
        return tuple(PaperOrder.model_validate_json(row["payload_json"]) for row in rows)

    def list_fills(self, session_id: str) -> tuple[PaperFill, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM paper_fills WHERE session_id=? "
                "ORDER BY created_at, fill_id",
                (session_id,),
            ).fetchall()
        return tuple(PaperFill.model_validate_json(row["payload_json"]) for row in rows)

    def save_consistency_report(self, report: RuntimeConsistencyReport) -> None:
        self._atomic_write(
            self.session_directory(report.session_id) / "runtime-consistency.json",
            (report.model_dump_json(indent=2) + "\n").encode(),
        )

    def load_consistency_report(self, session_id: str) -> RuntimeConsistencyReport:
        path = self.session_directory(session_id) / "runtime-consistency.json"
        if not path.is_file():
            raise PaperSessionNotFoundError(f"{session_id}/runtime-consistency")
        return RuntimeConsistencyReport.model_validate_json(path.read_bytes())
