from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.autopsy import PnLAutopsyReport, build_pnl_autopsy
from app.diagnostics.models import DiagnosisReport, FailureFingerprint
from app.runs import ArtifactIntegrityError, run_ledger

router = APIRouter(prefix="/api", tags=["pnl-autopsy"])


@router.get("/traces/{trace_id}/pnl-autopsy", response_model=PnLAutopsyReport)
def get_pnl_autopsy(trace_id: str) -> PnLAutopsyReport:
    run_id = run_ledger.repository.run_id_for_trace(trace_id)
    if run_id is None:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' was not found")

    fingerprint: FailureFingerprint | None = None
    diagnostics = run_ledger.repository.load_derived(run_id, "diagnostics")
    if diagnostics is not None:
        try:
            fingerprint = DiagnosisReport.model_validate_json(diagnostics).failure_fingerprint
        except ValidationError:
            fingerprint = None

    try:
        cached = run_ledger.repository.load_derived(run_id, "pnl-autopsy")
        if cached is not None:
            cached_report = PnLAutopsyReport.model_validate_json(cached)
            if cached_report.failure_fingerprint is not None or fingerprint is None:
                return cached_report
        trace = run_ledger.repository.load_trace_for_run(run_id)
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    report = build_pnl_autopsy(trace_id, trace, failure_fingerprint=fingerprint)
    run_ledger.repository.save_derived(
        run_id, "pnl-autopsy", (report.model_dump_json(indent=2) + "\n").encode()
    )
    return report
