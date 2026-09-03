from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models import Execution, Order


@dataclass(slots=True)
class ExecutionEngine:
    """Deterministic next-bar execution model used by VQD research runtimes.

    ``slippage_bps`` is a fixed adverse move. ``spread_bps`` represents the full quoted
    bid/ask spread, so a marketable order pays half of it. ``market_impact_bps`` is the
    adverse move at 100% participation and scales with sqrt(order_qty / bar_volume).

    All extra costs default to zero so existing experiments keep their previous semantics.
    """

    fee_bps: float = 5.0
    slippage_bps: float = 5.0
    spread_bps: float = 0.0
    market_impact_bps: float = 0.0

    def __post_init__(self) -> None:
        values = {
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.spread_bps,
            "market_impact_bps": self.market_impact_bps,
        }
        if any(value < 0 or not math.isfinite(value) for value in values.values()):
            raise ValueError("Execution cost bps must be finite and non-negative")

    def create_orders(
        self,
        *,
        current_a: float,
        current_b: float,
        desired_a: float,
        desired_b: float,
        submitted_at: datetime,
        target_position: Literal[-1, 0, 1],
        source_signal_id: str,
    ) -> tuple[Order, ...]:
        orders: list[Order] = []
        legs: tuple[
            tuple[Literal["ASSET_A", "ASSET_B"], float],
            tuple[Literal["ASSET_A", "ASSET_B"], float],
        ] = (("ASSET_A", desired_a - current_a), ("ASSET_B", desired_b - current_b))
        for index, (symbol, delta) in enumerate(legs, start=1):
            if abs(delta) < 1e-12:
                continue
            side: Literal["BUY", "SELL"] = "BUY" if delta > 0 else "SELL"
            orders.append(
                Order(
                    order_id=f"{source_signal_id}-order-{index}",
                    symbol=symbol,
                    side=side,
                    quantity=abs(delta),
                    submitted_at=submitted_at,
                    target_position=target_position,
                    source_signal_id=source_signal_id,
                )
            )
        return tuple(orders)

    def create_target_orders(
        self,
        *,
        current_positions: Mapping[str, float],
        target_positions: Mapping[str, float],
        submitted_at: datetime,
        source_signal_id: str,
        target_state: Literal[-1, 0, 1] = 0,
    ) -> tuple[Order, ...]:
        """Create one deterministic delta order per changed symbol."""

        symbols = tuple(dict.fromkeys((*current_positions, *target_positions)))
        orders: list[Order] = []
        for index, symbol in enumerate(symbols, start=1):
            delta = target_positions.get(symbol, 0.0) - current_positions.get(symbol, 0.0)
            if abs(delta) < 1e-12:
                continue
            side: Literal["BUY", "SELL"] = "BUY" if delta > 0 else "SELL"
            orders.append(
                Order(
                    order_id=f"{source_signal_id}-order-{index}",
                    symbol=symbol,
                    side=side,
                    quantity=abs(delta),
                    submitted_at=submitted_at,
                    target_position=target_state,
                    source_signal_id=source_signal_id,
                )
            )
        return tuple(orders)

    def resolve_gross_weights(
        self,
        target_weights: Mapping[str, float],
        gross_notional: float,
        prices: Mapping[str, float],
    ) -> dict[str, float]:
        if gross_notional <= 0:
            raise ValueError("gross_notional must be positive")
        if all(abs(weight) < 1e-12 for weight in target_weights.values()):
            return {symbol: 0.0 for symbol in target_weights}
        denominator = sum(abs(weight) * prices[symbol] for symbol, weight in target_weights.items())
        if denominator <= 0:
            raise ValueError("Target weights require positive current prices")
        scale = gross_notional / denominator
        return {symbol: weight * scale for symbol, weight in target_weights.items()}

    def _fill(
        self,
        order: Order,
        *,
        expected_price: float,
        volume: float | None,
        executed_at: datetime,
    ) -> Execution:
        if expected_price <= 0 or not math.isfinite(expected_price):
            raise ValueError(f"Execution price for {order.symbol} must be positive and finite")
        direction = 1.0 if order.side == "BUY" else -1.0
        fixed_rate = self.slippage_bps / 10_000.0
        half_spread_rate = self.spread_bps / 20_000.0
        participation = 0.0
        if self.market_impact_bps > 0.0:
            if volume is None or volume <= 0 or not math.isfinite(volume):
                raise ValueError(
                    f"Market-impact execution requires positive bar volume for {order.symbol}"
                )
            participation = max(order.quantity / volume, 0.0)
        impact_rate = self.market_impact_bps / 10_000.0 * math.sqrt(participation)
        adverse_rate = fixed_rate + half_spread_rate + impact_rate
        fill_price = expected_price * (1.0 + direction * adverse_rate)
        traded_notional = order.quantity * expected_price
        fee = traded_notional * (self.fee_bps / 10_000.0)
        spread_cost = traded_notional * half_spread_rate
        market_impact = traded_notional * impact_rate
        total_slippage = traded_notional * adverse_rate
        return Execution(
            execution_id=f"{order.order_id}-execution",
            source_order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            expected_price=expected_price,
            fill_price=fill_price,
            traded_notional=traded_notional,
            fee=fee,
            slippage=total_slippage,
            executed_at=executed_at,
            spread_cost=spread_cost,
            market_impact=market_impact,
        )

    def execute(
        self,
        orders: tuple[Order, ...],
        *,
        price_a: float,
        price_b: float,
        executed_at: datetime,
        volume_a: float | None = None,
        volume_b: float | None = None,
    ) -> tuple[Execution, ...]:
        prices = {"ASSET_A": price_a, "ASSET_B": price_b}
        volumes = {"ASSET_A": volume_a, "ASSET_B": volume_b}
        return tuple(
            self._fill(
                order,
                expected_price=prices[order.symbol],
                volume=volumes.get(order.symbol),
                executed_at=executed_at,
            )
            for order in orders
        )

    def execute_at_prices(
        self,
        orders: tuple[Order, ...],
        *,
        prices: Mapping[str, float],
        executed_at: datetime,
        volumes: Mapping[str, float] | None = None,
    ) -> tuple[Execution, ...]:
        executions: list[Execution] = []
        for order in orders:
            try:
                expected_price = prices[order.symbol]
            except KeyError as exc:
                raise ValueError(f"No execution price for {order.symbol}") from exc
            executions.append(
                self._fill(
                    order,
                    expected_price=expected_price,
                    volume=None if volumes is None else volumes.get(order.symbol),
                    executed_at=executed_at,
                )
            )
        return tuple(executions)
