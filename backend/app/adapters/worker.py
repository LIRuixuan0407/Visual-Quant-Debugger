from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from importlib.metadata import PackageNotFoundError

from pydantic import ValidationError

from .models import AdapterRunRequest
from .registry import adapter_registry


def _failure_code(exc: BaseException, operation: str) -> str:
    if isinstance(exc, (PackageNotFoundError, ModuleNotFoundError)):
        return "FRAMEWORK_NOT_INSTALLED"
    if isinstance(exc, (ValidationError, ValueError, KeyError)):
        return "ADAPTER_VALIDATION_FAILED"
    if isinstance(exc, (ImportError, SyntaxError)):
        return "IMPORT_FAILED"
    return "STRATEGY_LOAD_FAILED" if operation == "inspect" else "FRAMEWORK_EXECUTION_FAILED"


def main() -> int:
    operation = "unknown"
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("Worker request must be an object")
        operation = str(payload.get("operation", ""))
        adapter_id = str(
            payload.get("adapter_id") or (payload.get("request") or {}).get("adapter_id")
        )
        adapter = adapter_registry.get(adapter_id)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            if operation == "inspect":
                inspection = adapter.inspect(
                    str(payload.get("source_path", "")), str(payload.get("entrypoint", ""))
                )
                result_payload = inspection.model_dump(mode="json")
            elif operation == "execute":
                run_result = adapter.execute(
                    AdapterRunRequest.model_validate(payload.get("request"))
                )
                result_payload = run_result.model_dump(mode="json")
            else:
                raise ValueError(f"Unknown framework worker operation '{operation}'")
        pollution = captured.getvalue()
        if pollution:
            print(pollution, file=sys.stderr, end="")
        print(json.dumps({"ok": True, "result": result_payload}))
        return 0
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "failure": {
                        "code": _failure_code(exc, operation),
                        "summary": str(exc),
                        "exception_type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                }
            )
        )
        return 1


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    raise SystemExit(main())
