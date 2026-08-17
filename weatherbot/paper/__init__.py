"""Internal PAPER strategy evaluation plus retained legacy paper-trading compatibility."""

from weatherbot.paper.execution import PaperExecutionAdapter, build_paper_execution_plan
from weatherbot.paper.experiment import (
    EconomicEvaluationStatus,
    PaperCaseResult,
    PaperEconomicConfig,
    PaperEvidenceCase,
    PaperExperimentEngine,
    PaperExperimentResult,
    PaperExperimentSpec,
    StrategyDecision,
    StrategyEvaluator,
)
from weatherbot.paper.integration import (
    paper_runtime_status,
    paper_scan_decision_id,
    recover_paper_runtime,
    reset_paper_runtime,
    submit_scanner_candidate,
)
from weatherbot.paper.io import (
    PaperExperimentArtifacts,
    case_payload,
    summary_payload,
    write_experiment_artifacts,
)
from weatherbot.paper.ledger import archive_and_reset_paper_ledger, initialize_paper_store
from weatherbot.paper.model import (
    PaperExecutionPlan,
    PaperExecutionStatus,
    PaperFillLevel,
    PaperStatus,
)
from weatherbot.paper.runtime import (
    PaperBookReference,
    PaperRuntimeConfig,
    load_open_position_books,
    open_position_book_references,
)
from weatherbot.paper.service import (
    PaperEntryRequest,
    PaperEntryResult,
    PaperEntryStatus,
    PaperTradingService,
)
from weatherbot.paper.valuation import build_paper_valuation, paper_status

__all__ = [
    "EconomicEvaluationStatus",
    "PaperBookReference",
    "PaperCaseResult",
    "PaperEconomicConfig",
    "PaperEntryRequest",
    "PaperEntryResult",
    "PaperEntryStatus",
    "PaperEvidenceCase",
    "PaperExecutionAdapter",
    "PaperExecutionPlan",
    "PaperExecutionStatus",
    "PaperExperimentArtifacts",
    "PaperExperimentEngine",
    "PaperExperimentResult",
    "PaperExperimentSpec",
    "PaperFillLevel",
    "PaperRuntimeConfig",
    "PaperStatus",
    "PaperTradingService",
    "StrategyDecision",
    "StrategyEvaluator",
    "archive_and_reset_paper_ledger",
    "build_paper_execution_plan",
    "build_paper_valuation",
    "case_payload",
    "initialize_paper_store",
    "load_open_position_books",
    "open_position_book_references",
    "paper_runtime_status",
    "paper_scan_decision_id",
    "paper_status",
    "recover_paper_runtime",
    "reset_paper_runtime",
    "submit_scanner_candidate",
    "summary_payload",
    "write_experiment_artifacts",
]
