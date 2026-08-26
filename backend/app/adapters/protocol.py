from __future__ import annotations

from typing import Protocol

from .models import AdapterInspection, AdapterRunRequest, AdapterRunResult


class FrameworkAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    framework_name: str
    distribution_name: str

    def inspect(self, source_path: str, entrypoint: str) -> AdapterInspection: ...

    def execute(self, request: AdapterRunRequest) -> AdapterRunResult: ...
