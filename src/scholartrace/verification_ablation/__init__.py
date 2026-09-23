"""Independent SA-03 V-on/V-off verification ablation package."""

from .formal_checkpoint import FormalCheckpointRecorder
from .models import (
    ABLATION_REPORT_SCHEMA_SHA256,
    AblationAttemptRecord,
    AblationBudget,
    AblationClaimDisposition,
    AblationError,
    AblationExecutionError,
    AblationExecutionManifest,
    AblationFrozenInput,
    AblationGeneratedReport,
    AblationPrivateArchive,
    AblationPrivateRow,
    AblationReportDraft,
    AblationReportFindingDraft,
    AblationReportRequest,
    AblationResult,
    AblationUsage,
    AblationVariant,
    load_frozen_inputs,
)
from .provider import OpenAICompatibleAblationReportGenerator, SharedBudgetGuard
from .provider_context import (
    COMPACT_CONTEXT_ID,
    CompactReportContextGenerator,
    compact_prepared_context,
)
from .reporting import public_payload, require_private_path, write_artifacts
from .runner import (
    DeterministicFixtureAblationReportGenerator,
    DeterministicFixtureAblationVerifier,
    VerificationAblationRunner,
    evidence_identity_sha256,
    prepare_variant_input,
)
from .simple_baseline import (
    SimpleBaselineArchive,
    SimpleBaselineManifest,
    SimpleBaselineRow,
    SimpleBaselineRunner,
    SimpleBaselineUsage,
)

__all__ = [
    "ABLATION_REPORT_SCHEMA_SHA256",
    "AblationBudget",
    "AblationClaimDisposition",
    "AblationError",
    "AblationExecutionError",
    "AblationExecutionManifest",
    "AblationAttemptRecord",
    "AblationFrozenInput",
    "AblationGeneratedReport",
    "AblationReportDraft",
    "AblationReportFindingDraft",
    "AblationPrivateArchive",
    "AblationPrivateRow",
    "AblationReportRequest",
    "AblationResult",
    "AblationUsage",
    "AblationVariant",
    "DeterministicFixtureAblationReportGenerator",
    "DeterministicFixtureAblationVerifier",
    "OpenAICompatibleAblationReportGenerator",
    "COMPACT_CONTEXT_ID",
    "CompactReportContextGenerator",
    "compact_prepared_context",
    "SharedBudgetGuard",
    "FormalCheckpointRecorder",
    "VerificationAblationRunner",
    "evidence_identity_sha256",
    "load_frozen_inputs",
    "prepare_variant_input",
    "SimpleBaselineArchive",
    "SimpleBaselineManifest",
    "SimpleBaselineRow",
    "SimpleBaselineRunner",
    "SimpleBaselineUsage",
    "public_payload",
    "require_private_path",
    "write_artifacts",
]
