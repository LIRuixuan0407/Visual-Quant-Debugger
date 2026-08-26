from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import UTC, datetime, time
from typing import Any

import httpx

from .models import (
    STANDARD_FUNDAMENTAL_FIELDS,
    CreateFundamentalDataset,
    FundamentalObservation,
    FundamentalPeriodType,
    FundamentalProviderInfo,
)

SEC_DATA_BASE = "https://data.sec.gov"
SEC_WWW_BASE = "https://www.sec.gov"

_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "assets": ("Assets",),
    "debt": (
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebt",
    ),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForProceedsFromOtherPropertyPlantAndEquipment",
    ),
    "debt_current": (
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent",
    ),
    "debt_noncurrent": (
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "LongTermDebtNoncurrent",
    ),
}

_INSTANT_FIELDS = {
    "equity",
    "assets",
    "debt",
    "shares_outstanding",
    "debt_current",
    "debt_noncurrent",
}


def _day(value: str) -> datetime:
    return datetime.combine(datetime.fromisoformat(value).date(), time.max, tzinfo=UTC)


def _period_type(field: str, form: str) -> FundamentalPeriodType:
    if field in _INSTANT_FIELDS:
        return "INSTANT"
    return "ANNUAL" if form.startswith("10-K") else "QUARTERLY"


class SecCompanyFactsProvider:
    """SEC Company Facts boundary; raw XBRL tags never reach the factor engine."""

    def __init__(
        self, *, user_agent: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.user_agent = user_agent or os.environ.get("SEC_USER_AGENT", "")
        self._client = client

    def info(self) -> FundamentalProviderInfo:
        configured = bool(self.user_agent.strip())
        return FundamentalProviderInfo(
            provider_id="sec-companyfacts",
            name="SEC EDGAR Company Facts",
            fields=(
                "revenue",
                "net_income",
                "equity",
                "assets",
                "debt",
                "operating_cash_flow",
                "free_cash_flow",
                "shares_outstanding",
                "operating_income",
            ),
            requires_credentials=False,
            point_in_time_semantics=(
                "Filed date is used as the earliest available date; fiscal period is never used "
                "as availability."
            ),
            restatement_safe=False,
            status="AVAILABLE" if configured else "BLOCKED",
            detail=(
                "SEC_USER_AGENT configured."
                if configured
                else (
                    "Set SEC_USER_AGENT to an application name and contact email required by "
                    "SEC fair-access guidance."
                )
            ),
        )

    def _headers(self) -> dict[str, str]:
        if not self.user_agent.strip():
            raise RuntimeError(
                "SEC_USER_AGENT is required and must identify the application and a contact email"
            )
        return {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}

    async def _get_json(self, url: str) -> Mapping[str, Any]:
        if self._client is not None:
            response = await self._client.get(url, headers=self._headers())
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

    async def _ciks(self, symbols: tuple[str, ...]) -> dict[str, str]:
        payload = await self._get_json(f"{SEC_WWW_BASE}/files/company_tickers.json")
        requested = {item.upper() for item in symbols}
        result: dict[str, str] = {}
        for raw in payload.values():
            if not isinstance(raw, Mapping):
                continue
            ticker = str(raw.get("ticker", "")).upper()
            if ticker in requested:
                result[ticker] = str(raw.get("cik_str", "")).zfill(10)
        missing = sorted(requested - set(result))
        if missing:
            raise ValueError(f"SEC CIK mapping was not found for: {', '.join(missing)}")
        return result

    @staticmethod
    def _concept_units(
        payload: Mapping[str, Any], concepts: tuple[str, ...]
    ) -> tuple[str, Mapping[str, Any]] | None:
        facts = payload.get("facts", {})
        gaap = facts.get("us-gaap", {}) if isinstance(facts, Mapping) else {}
        for concept in concepts:
            raw = gaap.get(concept) if isinstance(gaap, Mapping) else None
            if not isinstance(raw, Mapping):
                continue
            units = raw.get("units", {})
            if not isinstance(units, Mapping):
                continue
            for unit in ("USD", "shares", "pure"):
                if isinstance(units.get(unit), list):
                    return concept, {unit: units[unit]}
        return None

    @classmethod
    def _standardize_symbol(
        cls,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        start: datetime,
        end: datetime,
        retrieved_at: datetime,
    ) -> tuple[FundamentalObservation, ...]:
        rows: list[FundamentalObservation] = []
        for field, concepts in _CONCEPTS.items():
            selected = cls._concept_units(payload, concepts)
            if selected is None:
                continue
            concept, units = selected
            unit, entries = next(iter(units.items()))
            for raw in entries:
                if not isinstance(raw, Mapping):
                    continue
                form = str(raw.get("form", ""))
                filed = str(raw.get("filed", ""))
                period_end = str(raw.get("end", ""))
                accession = str(raw.get("accn", ""))
                if (
                    not filed
                    or not period_end
                    or not accession
                    or form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}
                ):
                    continue
                available_at = _day(filed)
                if available_at < start or available_at > end:
                    continue
                try:
                    value = float(raw["val"])
                except (KeyError, TypeError, ValueError):
                    continue
                report_date = _day(period_end)
                period_start = _day(str(raw["start"])) if raw.get("start") else None
                fiscal_period = f"{raw.get('fy', report_date.year)}{raw.get('fp', '')}"
                identity = f"{symbol}:{field}:{accession}:{period_end}:{value}"
                rows.append(
                    FundamentalObservation(
                        observation_id=f"sec-fund-{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
                        symbol=symbol,
                        field=field,
                        value=value,
                        unit=str(unit),
                        fiscal_period=fiscal_period,
                        period_type=_period_type(field, form),
                        period_start=period_start,
                        period_end=report_date,
                        report_date=report_date,
                        filed_at=available_at,
                        available_at=available_at,
                        retrieved_at=retrieved_at,
                        form=form,
                        accession=accession,
                        source="sec-companyfacts",
                        source_concepts=(concept,),
                        is_restatement=form.endswith("/A"),
                    )
                )
        rows.extend(cls._derived(symbol, rows, retrieved_at))
        # Component concepts used to derive debt and free cash flow remain inside
        # the provider boundary.  Persist only the public standardized schema.
        unique = {
            item.observation_id: item for item in rows if item.field in STANDARD_FUNDAMENTAL_FIELDS
        }
        return tuple(
            sorted(
                unique.values(), key=lambda item: (item.available_at, item.field, item.accession)
            )
        )

    @staticmethod
    def _derived(
        symbol: str,
        rows: list[FundamentalObservation],
        retrieved_at: datetime,
    ) -> list[FundamentalObservation]:
        by_key = {(item.accession, item.period_end, item.field): item for item in rows}
        derived: list[FundamentalObservation] = []
        for (accession, period_end, field), left in tuple(by_key.items()):
            if field == "operating_cash_flow":
                right = by_key.get((accession, period_end, "capital_expenditure"))
                if right is not None:
                    identity = hashlib.sha256(
                        (symbol + accession + "free_cash_flow").encode()
                    ).hexdigest()[:20]
                    derived.append(
                        left.model_copy(
                            update={
                                "observation_id": f"sec-fund-{identity}",
                                "field": "free_cash_flow",
                                "value": left.value - abs(right.value),
                                "source_concepts": (*left.source_concepts, *right.source_concepts),
                                "retrieved_at": retrieved_at,
                            }
                        )
                    )
            if field == "debt_current":
                right = by_key.get((accession, period_end, "debt_noncurrent"))
                if right is not None and (accession, period_end, "debt") not in by_key:
                    identity = hashlib.sha256((symbol + accession + "debt").encode()).hexdigest()[
                        :20
                    ]
                    derived.append(
                        left.model_copy(
                            update={
                                "observation_id": f"sec-fund-{identity}",
                                "field": "debt",
                                "value": left.value + right.value,
                                "source_concepts": (*left.source_concepts, *right.source_concepts),
                                "retrieved_at": retrieved_at,
                            }
                        )
                    )
        return derived

    async def fetch(self, request: CreateFundamentalDataset) -> tuple[FundamentalObservation, ...]:
        symbols = tuple(
            dict.fromkeys(item.strip().upper() for item in request.symbols if item.strip())
        )
        ciks = await self._ciks(symbols)
        retrieved_at = datetime.now(UTC)
        result: list[FundamentalObservation] = []
        for symbol in symbols:
            payload = await self._get_json(
                f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{ciks[symbol]}.json"
            )
            result.extend(
                self._standardize_symbol(
                    symbol,
                    payload,
                    start=request.start,
                    end=request.end,
                    retrieved_at=retrieved_at,
                )
            )
        return tuple(result)
