"""OpenAI-compatible model provider utilities."""

from scholartrace.model_provider.catalog import (
    OpenAICompatibleCatalogClient,
    ProviderCatalogError,
    ProviderModel,
)
from scholartrace.model_provider.plan_generator import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatiblePlanGenerator,
    PlanGenerationResult,
    ProviderBudgetError,
    ProviderEndpointUnsupportedError,
    ProviderInferenceError,
    ProviderTokenUsage,
    ResearchPlanDraft,
)
from scholartrace.model_provider.report_generator import (
    EvidenceReportDraft,
    OpenAICompatibleReportGenerator,
    ReportFindingDraft,
)
from scholartrace.model_provider.settings import (
    ProviderSettings,
    read_dotenv,
    resolve_provider_settings,
)
from scholartrace.model_provider.verifier import (
    OpenAICompatibleSemanticVerifier,
    VerifierCallRecord,
)

__all__ = [
    "OpenAICompatibleCatalogClient",
    "OpenAICompatiblePlanGenerator",
    "OpenAICompatibleReportGenerator",
    "OpenAICompatibleSemanticVerifier",
    "ApiCallBudget",
    "ApiCallCounter",
    "PlanGenerationResult",
    "ProviderCatalogError",
    "ProviderBudgetError",
    "ProviderEndpointUnsupportedError",
    "ProviderInferenceError",
    "ProviderModel",
    "ProviderSettings",
    "ProviderTokenUsage",
    "ResearchPlanDraft",
    "EvidenceReportDraft",
    "ReportFindingDraft",
    "VerifierCallRecord",
    "read_dotenv",
    "resolve_provider_settings",
]
