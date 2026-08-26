from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

from .models import AdapterInspection, AdapterRunRequest, AdapterRunResult

FrameworkFailureCode = Literal[
    "FRAMEWORK_NOT_INSTALLED",
    "IMPORT_FAILED",
    "STRATEGY_LOAD_FAILED",
    "ADAPTER_VALIDATION_FAILED",
    "FRAMEWORK_EXECUTION_FAILED",
    "ADAPTER_NORMALIZATION_FAILED",
]


@dataclass(frozen=True, slots=True)
class FrameworkRunError(RuntimeError):
    code: FrameworkFailureCode
    summary: str
    exception_type: str
    stderr: str
    traceback: str

    def __str__(self) -> str:
        return f"{self.code}: {self.summary}"


class FrameworkRunner:
    """Run trusted local framework code in a process boundary, not a security sandbox."""

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = python_executable or sys.executable

    def _invoke(self, payload: dict[str, object]) -> dict[str, object]:
        completed = subprocess.run(
            [self.python_executable, "-m", "app.adapters.worker"],
            input=json.dumps(payload, separators=(",", ":")),
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise FrameworkRunError(
                "ADAPTER_NORMALIZATION_FAILED",
                "Framework worker returned invalid JSON.",
                type(exc).__name__,
                completed.stderr,
                "",
            ) from exc
        if not isinstance(response, dict):
            raise FrameworkRunError(
                "ADAPTER_NORMALIZATION_FAILED",
                "Framework worker returned an invalid response envelope.",
                "InvalidWorkerResponse",
                completed.stderr,
                "",
            )
        if completed.returncode != 0 or not response.get("ok"):
            failure = response.get("failure")
            details = failure if isinstance(failure, dict) else {}
            code = str(details.get("code", "FRAMEWORK_EXECUTION_FAILED"))
            allowed: tuple[FrameworkFailureCode, ...] = (
                "FRAMEWORK_NOT_INSTALLED",
                "IMPORT_FAILED",
                "STRATEGY_LOAD_FAILED",
                "ADAPTER_VALIDATION_FAILED",
                "FRAMEWORK_EXECUTION_FAILED",
                "ADAPTER_NORMALIZATION_FAILED",
            )
            normalized_code = code if code in allowed else "FRAMEWORK_EXECUTION_FAILED"
            raise FrameworkRunError(
                normalized_code,
                str(details.get("summary", "Framework worker failed.")),
                str(details.get("exception_type", "FrameworkWorkerError")),
                completed.stderr,
                str(details.get("traceback", "")),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise FrameworkRunError(
                "ADAPTER_NORMALIZATION_FAILED",
                "Framework worker did not return a result object.",
                "InvalidWorkerResponse",
                completed.stderr,
                "",
            )
        return result

    def inspect(self, adapter_id: str, source_path: str, entrypoint: str) -> AdapterInspection:
        result = self._invoke(
            {
                "operation": "inspect",
                "adapter_id": adapter_id,
                "source_path": source_path,
                "entrypoint": entrypoint,
            }
        )
        return AdapterInspection.model_validate(result)

    def execute(self, request: AdapterRunRequest) -> AdapterRunResult:
        result = self._invoke({"operation": "execute", "request": request.model_dump(mode="json")})
        return AdapterRunResult.model_validate(result)
