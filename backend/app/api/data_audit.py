from fastapi import APIRouter, HTTPException

from app.corporate_actions import corporate_action_repository
from app.data_audit import (
    CreateDataAudit,
    DataAuditDetail,
    DataAuditEngine,
    DataAuditIntegrityError,
    DataAuditSourceVerification,
    DataAuditSummary,
    data_audit_repository,
)
from app.datasets import dataset_registry
from app.factors import FactorResearchEngine, factor_research_repository
from app.fundamentals import fundamental_repository
from app.runs import ArtifactIntegrityError, run_store
from app.universes import universe_repository

router = APIRouter(prefix="/api/data-audits", tags=["data-audits"])


def _engine() -> DataAuditEngine:
    factor_engine = FactorResearchEngine(
        dataset_registry,
        fundamentals=fundamental_repository,
        universes=universe_repository,
    )
    return DataAuditEngine(
        dataset_registry,
        factor_research_repository,
        factor_engine,
        fundamental_repository,
        universe_repository,
        run_store.repository,
        data_audit_repository,
        corporate_action_repository,
    )


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc.args[0]))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.post("", response_model=DataAuditDetail, status_code=201)
def create_data_audit(request: CreateDataAudit) -> DataAuditDetail:
    engine = _engine()
    try:
        record = engine.create(request)
        return engine.detail(record.audit_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except (ArtifactIntegrityError, DataAuditIntegrityError, TypeError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.get("", response_model=tuple[DataAuditSummary, ...])
def list_data_audits() -> tuple[DataAuditSummary, ...]:
    return data_audit_repository.list()


@router.get("/{audit_id}", response_model=DataAuditDetail)
def get_data_audit(audit_id: str) -> DataAuditDetail:
    try:
        return _engine().detail(audit_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except (DataAuditIntegrityError, ValueError) as exc:
        raise _unprocessable(exc) from exc


@router.post("/{audit_id}/verify-source", response_model=DataAuditSourceVerification)
def verify_data_audit_source(audit_id: str) -> DataAuditSourceVerification:
    try:
        return _engine().verify_source(audit_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except (DataAuditIntegrityError, ValueError) as exc:
        raise _unprocessable(exc) from exc
