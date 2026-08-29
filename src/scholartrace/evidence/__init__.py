"""M2 single-document evidence retrieval and report assembly."""

from scholartrace.evidence.analysis import GeneratedPaperAnalysis, OllamaPaperAnalyzer
from scholartrace.evidence.artifacts import (
    BudgetExceededError,
    PersistedM2Artifacts,
    persist_m2_artifacts,
)
from scholartrace.evidence.bindings import (
    BindingConflictError,
    DocuMindBindingRepository,
)
from scholartrace.evidence.client import (
    DocuMindClient,
    DocuMindClientError,
    DocuMindProtocolError,
    EvidenceScopeError,
    RetrievalResult,
)
from scholartrace.evidence.models import (
    DOCUMIND_ERROR_CODES,
    EVIDENCE_CONTRACT_MODELS,
    DocuMindErrorEnvelope,
    DocuMindReadiness,
    DocuMindRetrieveRequest,
    DocuMindRetrieveResponse,
    EvidenceReportArtifact,
    PaperAnalysisBundle,
    PaperAnalysisDraft,
    PaperCard,
    RetrievalAudit,
    RetrievalChunk,
)
from scholartrace.evidence.pipeline import (
    EvidencePipelineResult,
    M2EvidencePipeline,
    MissingBindingError,
    NoEvidenceError,
)
from scholartrace.evidence.report import build_evidence_report

__all__ = [
    "DOCUMIND_ERROR_CODES",
    "EVIDENCE_CONTRACT_MODELS",
    "BindingConflictError",
    "BudgetExceededError",
    "DocuMindClient",
    "DocuMindClientError",
    "DocuMindBindingRepository",
    "DocuMindErrorEnvelope",
    "DocuMindReadiness",
    "DocuMindProtocolError",
    "DocuMindRetrieveRequest",
    "DocuMindRetrieveResponse",
    "EvidencePipelineResult",
    "EvidenceReportArtifact",
    "PaperCard",
    "EvidenceScopeError",
    "GeneratedPaperAnalysis",
    "M2EvidencePipeline",
    "MissingBindingError",
    "NoEvidenceError",
    "OllamaPaperAnalyzer",
    "PaperAnalysisBundle",
    "PaperAnalysisDraft",
    "PersistedM2Artifacts",
    "RetrievalAudit",
    "RetrievalChunk",
    "RetrievalResult",
    "build_evidence_report",
    "persist_m2_artifacts",
]
