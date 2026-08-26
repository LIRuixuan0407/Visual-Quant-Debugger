from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models import Execution, Order


@dataclass(slots=True)
class ExecutionEngine:
    fee_bps: float = 5.0
    slippage_bps: float = 5.0

    def __post_init__(self) -> None:
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("fee_bps and slippage_bps must be non-negative")

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

    def execute(
        self,
        orders: tuple[Order, ...],
        *,
        price_a: float,
        price_b: float,
        executed_at: datetime,
    ) -> tuple[Execution, ...]:
        executions: list[Execution] = []
        slip_rate = self.slippage_bps / 10_000
        fee_rate = self.fee_bps / 10_000
        for order in orders:
            expected_price = price_a if order.symbol == "ASSET_A" else price_b
            direction = 1.0 if order.side == "BUY" else -1.0
            fill_price = expected_price * (1 + direction * slip_rate)
            traded_notional = order.quantity * expected_price
            executions.append(
                Execution(
                    execution_id=f"{order.order_id}-execution",
                    source_order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    expected_price=expected_price,
                    fill_price=fill_price,
                    traded_notional=traded_notional,
                    fee=traded_notional * fee_rate,
                    slippage=traded_notional * slip_rate,
                    executed_at=executed_at,
                )
            )
        return tuple(executions)

    def execute_at_prices(
        self,
        orders: tuple[Order, ...],
        *,
        prices: Mapping[str, float],
        executed_at: datetime,
    ) -> tuple[Execution, ...]:
        executions: list[Execution] = []
        slip_rate = self.slippage_bps / 10_000
        fee_rate = self.fee_bps / 10_000
        for order in orders:
            try:
                expected_price = prices[order.symbol]
            except KeyError as exc:
                raise ValueError(f"No execution price for {order.symbol}") from exc
            direction = 1.0 if order.side == "BUY" else -1.0
            fill_price = expected_price * (1 + direction * slip_rate)
            traded_notional = order.quantity * expected_price
            executions.append(
                Execution(
                    execution_id=f"{order.order_id}-execution",
                    source_order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    expected_price=expected_price,
                    fill_price=fill_price,
                    traded_notional=traded_notional,
                    fee=traded_notional * fee_rate,
                    slippage=traded_notional * slip_rate,
                    executed_at=executed_at,
                )
            )
        return tuple(executions)
