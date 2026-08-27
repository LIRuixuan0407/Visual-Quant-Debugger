from fastapi import APIRouter

from app.api.autopsy import router as autopsy_router
from app.api.datasets import router as datasets_router
from app.api.diagnostics import router as diagnostics_router
from app.api.discovery import router as discovery_router
from app.api.factor_relationships import router as factor_relationships_router
from app.api.factors import router as factors_router
from app.api.forward import router as forward_router
from app.api.fundamentals import router as fundamentals_router
from app.api.paper import account_router, market_router
from app.api.paper import router as paper_router
from app.api.portfolio_lab import router as portfolio_lab_router
from app.api.replay import router as replay_router
from app.api.research_integrity import router as research_integrity_router
from app.api.research_lineage import router as research_lineage_router
from app.api.research_snapshots import router as research_snapshots_router
from app.api.research_workspace import router as research_workspace_router
from app.api.runs import router as runs_router
from app.api.settings import router as settings_router
from app.api.strategies import router as strategies_router
from app.api.walk_forward import router as walk_forward_router

router = APIRouter()
router.include_router(replay_router)
router.include_router(research_snapshots_router)
router.include_router(research_integrity_router)
router.include_router(research_lineage_router)
router.include_router(research_workspace_router)
router.include_router(runs_router)
router.include_router(datasets_router)
router.include_router(strategies_router)
router.include_router(diagnostics_router)
router.include_router(discovery_router)
router.include_router(autopsy_router)
router.include_router(forward_router)
router.include_router(market_router)
router.include_router(paper_router)
router.include_router(account_router)
router.include_router(settings_router)
router.include_router(factors_router)
router.include_router(factor_relationships_router)
router.include_router(fundamentals_router)
router.include_router(portfolio_lab_router)
router.include_router(walk_forward_router)

__all__ = ["router"]
