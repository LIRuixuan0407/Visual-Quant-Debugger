from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path

from app.workspace import default_workspace_root

from .models import StrategyDriftReport, StrategyDriftSummary

DRIFT_REPORT_ID_PATTERN = re.compile(r"^drift-[0-9a-f]{24}$")


class StrategyDriftIntegrityError(ValueError):
    pass


class StrategyDriftRepository:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.root = self.workspace_root / ".vqd" / "strategy-drift"

    def _path(self, report_id: str) -> Path:
        if not DRIFT_REPORT_ID_PATTERN.fullmatch(report_id):
            raise ValueError(f"Invalid Strategy Drift report id '{report_id}'")
        target = (self.root / report_id / "report.json").resolve()
        if target.parent.parent != self.root.resolve():
            raise ValueError("Strategy Drift report path escaped the workspace")
        return target

    def save(self, report: StrategyDriftReport) -> StrategyDriftReport:
        path = self._path(report.drift_report_id)
        if path.exists():
            existing = self.get(report.drift_report_id)
            if existing == report:
                return existing
            raise StrategyDriftIntegrityError(
                f"Strategy Drift report '{report.drift_report_id}' is immutable"
            )
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(".json.tmp")
        try:
            payload = (report.model_dump_json(indent=2) + "\n").encode()
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            with suppress(OSError):
                path.parent.rmdir()
            raise
        return report

    def get(self, report_id: str) -> StrategyDriftReport | None:
        path = self._path(report_id)
        if not path.exists():
            return None
        try:
            report = StrategyDriftReport.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise StrategyDriftIntegrityError(
                f"Strategy Drift report '{report_id}' is invalid: {exc}"
            ) from exc
        if report.drift_report_id != report_id:
            raise StrategyDriftIntegrityError(
                f"Strategy Drift report '{report_id}' identity does not match its path"
            )
        return report

    @staticmethod
    def _summary(report: StrategyDriftReport) -> StrategyDriftSummary:
        return StrategyDriftSummary(
            drift_report_id=report.drift_report_id,
            baseline_type=report.baseline_type,
            baseline_id=report.baseline_id,
            observed_type=report.observed_type,
            observed_id=report.observed_id,
            created_at=report.created_at,
            comparability=report.comparability,
            overall_status=report.overall_status,
            first_drift_at=report.first_drift_at,
            first_drift_dimension=report.first_drift_dimension,
            sample_size=report.observed.sample_size,
        )

    def list(self) -> tuple[StrategyDriftSummary, ...]:
        if not self.root.exists():
            return ()
        reports: list[StrategyDriftReport] = []
        for path in self.root.glob("*/report.json"):
            report = self.get(path.parent.name)
            if report is not None:
                reports.append(report)
        return tuple(
            self._summary(report)
            for report in sorted(reports, key=lambda item: item.created_at, reverse=True)
        )


strategy_drift_repository = StrategyDriftRepository()
