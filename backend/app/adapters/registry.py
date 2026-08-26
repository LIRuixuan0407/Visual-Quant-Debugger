from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .protocol import FrameworkAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        from .backtesting_py import BacktestingPyAdapter
        from .vectorbt import VectorbtAdapter

        adapters: tuple[FrameworkAdapter, ...] = (BacktestingPyAdapter(), VectorbtAdapter())
        self._adapters = {adapter.adapter_id: adapter for adapter in adapters}

    @staticmethod
    def normalize_id(adapter_id: str) -> str:
        aliases = {
            "backtesting": "backtesting.py",
            "backtesting_py": "backtesting.py",
            "backtesting.py": "backtesting.py",
            "vectorbt": "vectorbt",
        }
        try:
            return aliases[adapter_id.strip().lower()]
        except KeyError as exc:
            raise KeyError(f"Unsupported framework adapter '{adapter_id}'") from exc

    def get(self, adapter_id: str) -> FrameworkAdapter:
        normalized = self.normalize_id(adapter_id)
        return self._adapters[normalized]

    def list(self) -> tuple[FrameworkAdapter, ...]:
        return tuple(self._adapters.values())

    def installed_version(self, adapter_id: str) -> str | None:
        adapter = self.get(adapter_id)
        try:
            return version(adapter.distribution_name)
        except PackageNotFoundError:
            return None


adapter_registry = AdapterRegistry()
