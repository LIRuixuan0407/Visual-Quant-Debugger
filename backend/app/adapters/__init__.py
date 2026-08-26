"""Optional third-party framework adapters.

Adapters normalize framework-owned research results into persisted VQD artifacts.  They do
not participate in the native strategy runtime or live-paper execution path.
"""

from .models import (
    AdapterRunRequest,
    AdapterRunResult,
    AdapterStrategyManifest,
    CapabilityStatus,
    DeterminismStatus,
    RuntimeDescriptor,
    TraceCapabilitySet,
    TraceFidelity,
    derive_trace_fidelity,
    native_runtime,
)
from .registry import adapter_registry
from .runner import FrameworkRunError, FrameworkRunner

__all__ = [
    "AdapterRunRequest",
    "AdapterRunResult",
    "AdapterStrategyManifest",
    "CapabilityStatus",
    "DeterminismStatus",
    "FrameworkRunError",
    "FrameworkRunner",
    "RuntimeDescriptor",
    "TraceCapabilitySet",
    "TraceFidelity",
    "adapter_registry",
    "derive_trace_fidelity",
    "native_runtime",
]
