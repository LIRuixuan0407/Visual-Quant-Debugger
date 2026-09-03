from __future__ import annotations

import math


def annualization_factor_for_frequency(dataset_frequency: str | None) -> int | None:
    """Return a defensible observations-per-year factor for a Dataset frequency.

    VQD intentionally does not infer an intraday annualization factor from bar spacing alone.
    Doing so requires an explicit trading calendar/session contract (market holidays, half days,
    overnight sessions, etc.). Daily bars are the only frequency for which the current Dataset
    contract provides a stable annualization policy.
    """

    if dataset_frequency is None:
        return None
    normalized = dataset_frequency.strip().lower().replace(" ", "")
    if normalized in {"1d", "1day", "daily", "86400s"}:
        return 252
    return None


def sharpe_ratio(returns: object, *, dataset_frequency: str | None = None) -> float:
    """Calculate Sharpe using daily annualization only when the Dataset contract supports it.

    ``returns`` is intentionally typed loosely so callers can pass a NumPy array without making
    this small policy module depend on NumPy. The object must support ``len``/iteration semantics.
    """

    import numpy as np

    values = np.asarray(returns, dtype=np.float64)
    if values.size < 2:
        return 0.0
    standard_deviation = float(np.std(values, ddof=1))
    if standard_deviation == 0.0 or not math.isfinite(standard_deviation):
        return 0.0
    value = float(np.mean(values) / standard_deviation)
    factor = annualization_factor_for_frequency(dataset_frequency)
    if factor is not None:
        value *= math.sqrt(factor)
    return value if math.isfinite(value) else 0.0
