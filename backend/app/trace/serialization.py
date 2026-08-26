from app.trace.models import BacktestTrace


def trace_to_json(trace: BacktestTrace, *, indent: int | None = 2) -> str:
    return trace.model_dump_json(indent=indent)


def trace_from_json(payload: str | bytes) -> BacktestTrace:
    return BacktestTrace.model_validate_json(payload)
