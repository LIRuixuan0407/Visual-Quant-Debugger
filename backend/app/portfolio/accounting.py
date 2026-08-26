from collections.abc import Mapping
from dataclasses import dataclass, field

from app.models import Execution, PortfolioSnapshot


@dataclass(slots=True)
class Portfolio:
    cash: float
    quantity_a: float = 0.0
    quantity_b: float = 0.0
    cumulative_fees: float = 0.0
    cumulative_slippage: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity_a:
            self.positions["ASSET_A"] = self.quantity_a
        if self.quantity_b:
            self.positions["ASSET_B"] = self.quantity_b

    def apply(self, executions: tuple[Execution, ...]) -> None:
        for execution in executions:
            signed_quantity = execution.quantity if execution.side == "BUY" else -execution.quantity
            self.cash -= signed_quantity * execution.fill_price
            self.cash -= execution.fee
            if execution.symbol == "ASSET_A":
                self.quantity_a += signed_quantity
            elif execution.symbol == "ASSET_B":
                self.quantity_b += signed_quantity
            self.positions[execution.symbol] = (
                self.positions.get(execution.symbol, 0.0) + signed_quantity
            )
            self.cumulative_fees += execution.fee
            self.cumulative_slippage += execution.slippage

    def mark(self, price_a: float, price_b: float) -> PortfolioSnapshot:
        value_a = self.quantity_a * price_a
        value_b = self.quantity_b * price_b
        return PortfolioSnapshot(
            cash=self.cash,
            quantity_a=self.quantity_a,
            quantity_b=self.quantity_b,
            gross_exposure=abs(value_a) + abs(value_b),
            net_exposure=value_a + value_b,
            equity=self.cash + value_a + value_b,
            cumulative_fees=self.cumulative_fees,
            cumulative_slippage=self.cumulative_slippage,
            positions=dict(self.positions),
        )

    def mark_prices(self, prices: Mapping[str, float]) -> PortfolioSnapshot:
        values = {symbol: quantity * prices[symbol] for symbol, quantity in self.positions.items()}
        return PortfolioSnapshot(
            cash=self.cash,
            quantity_a=self.positions.get("ASSET_A", 0.0),
            quantity_b=self.positions.get("ASSET_B", 0.0),
            gross_exposure=sum(abs(value) for value in values.values()),
            net_exposure=sum(values.values()),
            equity=self.cash + sum(values.values()),
            cumulative_fees=self.cumulative_fees,
            cumulative_slippage=self.cumulative_slippage,
            positions=dict(self.positions),
        )
