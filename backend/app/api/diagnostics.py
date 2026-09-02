from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.diagnostics.engine import diagnose_framework_trace, diagnose_run, run_what_if_scenario
from app.diagnostics.models import DiagnosisReport, WhatIfInputs, WhatIfScenario
from app.runs import ArtifactIntegrityError, run_ledger

router = APIRouter(prefix="/api", tags=["diagnostics"])


class DiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    inputs: WhatIfInputs


def _current_cached_report(cached: bytes | None) -> DiagnosisReport | None:
    if cached is None:
        return None
    report = DiagnosisReport.model_validate_json(cached)
    if (
        report.statistical_diagnostics is None
        or report.volatility_diagnostics is None
        or report.what_if is None
    ):
        return None
    return report


@router.post("/diagnostics", response_model=DiagnosisReport)
def create_diagnosis(request: DiagnosisRequest) -> DiagnosisReport:
    run_id = run_ledger.repository.run_id_for_trace(request.trace_id)
    if run_id is None:
        raise HTTPException(status_code=404, detail=f"Trace '{request.trace_id}' was not found")
    manifest = run_ledger.repository.get_manifest(run_id)
    if manifest.runtime.kind == "framework":
        cached = run_ledger.repository.load_derived(run_id, "diagnostics")
        cached_report = _current_cached_report(cached)
        if cached_report is not None:
            return cached_report
        trace = run_ledger.repository.load_trace_for_run(run_id)
        report = diagnose_framework_trace(
            request.trace_id,
            run_id,
            trace,
            str(run_ledger.repository.workspace_root / ".vqd" / "datasets"),
        )
        run_ledger.repository.save_derived(
            run_id, "diagnostics", (report.model_dump_json(indent=2) + "\n").encode()
        )
        return report
    try:
        record = run_ledger.execution_record(request.trace_id)
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace '{request.trace_id}' was not found")
    cached = run_ledger.repository.load_derived(record.run_id, "diagnostics")
    cached_report = _current_cached_report(cached)
    if cached_report is not None:
        return cached_report
    report = diagnose_run(request.trace_id, record)
    run_ledger.repository.save_derived(
        record.run_id, "diagnostics", (report.model_dump_json(indent=2) + "\n").encode()
    )
    return report


@router.post("/diagnostics/what-if", response_model=WhatIfScenario)
def create_what_if_scenario(request: WhatIfRequest) -> WhatIfScenario:
    run_id = run_ledger.repository.run_id_for_trace(request.trace_id)
    if run_id is None:
        raise HTTPException(status_code=404, detail=f"Trace '{request.trace_id}' was not found")
    manifest = run_ledger.repository.get_manifest(run_id)
    if manifest.runtime.kind == "framework":
        raise HTTPException(
            status_code=409,
            detail="What-if reruns are not supported for framework-owned execution",
        )
    try:
        record = run_ledger.execution_record(request.trace_id)
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"Trace '{request.trace_id}' was not found")
    try:
        return run_what_if_scenario(record, request.inputs)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
