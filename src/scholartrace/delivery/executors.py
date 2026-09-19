"""Task executor seam separating the explicit demo path from real research.

F01 in the S4-A0 review: ``_execute_queued`` called ``_run_demo``
unconditionally, so no real module was reachable from the web entry point. This
module introduces the seam without deleting the demo path, which remains a
delivered, explicitly-labelled capability.

Rules encoded here:

* a task declares ``execution_mode``; ``demo`` and ``real`` never share a code
  path, and the demo path never renames its fixed counts into "real" metrics;
* the real executor is **fail closed**. A missing dependency produces a refusal
  that names the dependency, never a silent downgrade to the demo path and never
  an automatic switch to a more expensive model (ADR-008);
* refusal reasons are safe to show a user: no keys, no prompts, no raw content.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ExecutionMode(StrEnum):
    DEMO = "demo"
    REAL = "real"


class DependencyKind(StrEnum):
    """Dependencies the real executor requires, reported separately."""

    DOCUMIND = "documind"
    LOCAL_MODEL = "local_model"
    API_STRONG = "api_strong"
    SEARCH_PROVIDER = "search_provider"


@dataclass(frozen=True)
class DependencyGap:
    """One missing prerequisite, expressed without leaking configuration."""

    kind: DependencyKind
    #: Short, user-safe explanation. Must not contain keys, URLs with tokens,
    #: prompts, or paper content.
    reason: str
    #: What the operator can do about it, or None when it is a policy gate.
    remediation: str | None = None

    def as_public_dict(self) -> dict[str, str | None]:
        return {
            "dependency": self.kind.value,
            "reason": self.reason,
            "remediation": self.remediation,
        }


class RealExecutionUnavailableError(RuntimeError):
    """Real execution was requested but a prerequisite is missing.

    Carries the structured gaps so the API can render them; the message stays
    free of secrets so it is safe to log.
    """

    def __init__(self, gaps: list[DependencyGap]) -> None:
        self.gaps = gaps
        names = ", ".join(gap.kind.value for gap in gaps)
        super().__init__(f"real execution unavailable; unmet dependencies: {names}")

    def as_public_payload(self) -> dict[str, Any]:
        return {
            "code": "dependency_unavailable",
            "unmet_dependencies": [gap.as_public_dict() for gap in self.gaps],
        }


class TaskExecutor(Protocol):
    """A strategy for running an approved task to a terminal state."""

    @property
    def mode(self) -> ExecutionMode: ...

    def preflight(self) -> list[DependencyGap]:
        """Report unmet prerequisites without performing expensive probes.

        Must not load a model, call a paid provider, or echo credentials.
        """
        ...

    def run(self, task_id: str, *, cancel_event: threading.Event) -> dict[str, Any]:
        """Execute the task. Raises on failure; callers own status transitions."""
        ...


class DemoTaskExecutor:
    """Adapter over the pre-existing deterministic demo run.

    Kept as a first-class capability: the demo is a delivered feature with
    explicit, fixed outcomes. Its counts are demo constants and are surfaced as
    such — they are never relabelled as measured research metrics.

    ``run_demo`` is resolved at call time rather than captured at construction,
    so a subclass override or a test double installed after the service is built
    still takes effect.
    """

    def __init__(self, run_demo: Callable[..., Any]) -> None:
        self._run_demo = run_demo

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.DEMO

    def preflight(self) -> list[DependencyGap]:
        return []  # deterministic by construction

    def run(self, task_id: str, *, cancel_event: threading.Event) -> dict[str, Any]:
        result = self._run_demo(task_id, cancel_event=cancel_event)
        return dict(result) if result is not None else {}


class RealTaskExecutor:
    """Assembles the existing research modules behind the task entry point.

    A2 status: the dependency gate and the demo/real split are implemented and
    tested. Pipeline stage wiring (T08-T12) is assembled incrementally on top of
    this seam; until a stage is wired, ``preflight`` reports the gap and ``run``
    refuses rather than producing a partial result that looks complete.
    """

    def __init__(
        self,
        *,
        documind_available: bool,
        local_model_available: bool,
        api_strong_enabled: bool,
        search_providers_configured: bool,
        pipeline_stages_wired: bool = False,
        run_research: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._documind = documind_available
        self._local_model = local_model_available
        self._api_strong = api_strong_enabled
        self._search = search_providers_configured
        self._stages_wired = pipeline_stages_wired
        self._run_research = run_research

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.REAL

    @property
    def pipeline_stages_wired(self) -> bool:
        """Whether T08-T12 stages are assembled behind this executor.

        Exposed so the readiness endpoint can distinguish "dependencies present
        but stages not built yet" from "dependencies missing" — two different
        answers an operator needs to tell apart.
        """

        return self._stages_wired

    def preflight(self) -> list[DependencyGap]:
        gaps: list[DependencyGap] = []
        if not self._search:
            gaps.append(
                DependencyGap(
                    kind=DependencyKind.SEARCH_PROVIDER,
                    reason="No academic search provider is configured.",
                    remediation="Configure at least one of arXiv, OpenAlex or Crossref.",
                )
            )
        if not self._documind:
            gaps.append(
                DependencyGap(
                    kind=DependencyKind.DOCUMIND,
                    reason=(
                        "DocuMind retrieval is not reporting ready, so full-text "
                        "evidence cannot be retrieved."
                    ),
                    remediation="Start DocuMind and confirm components.retrieval=ready.",
                )
            )
        if not self._local_model:
            gaps.append(
                DependencyGap(
                    kind=DependencyKind.LOCAL_MODEL,
                    reason="The frozen local analysis model is not reachable.",
                    remediation="Start the local model runtime used by the frozen profile.",
                )
            )
        if not self._api_strong:
            # A policy gate, not a fault: planning, independent verification and
            # synthesis are gated on approval of a paid profile (ADR-008/014/016).
            gaps.append(
                DependencyGap(
                    kind=DependencyKind.API_STRONG,
                    reason=(
                        "The paid api-strong profile is not approved, so planning, "
                        "independent verification and synthesis stay fail-closed."
                    ),
                    remediation=None,
                )
            )
        return gaps

    def run(self, task_id: str, *, cancel_event: threading.Event) -> dict[str, Any]:
        gaps = self.preflight()
        if gaps:
            raise RealExecutionUnavailableError(gaps)
        if not self._stages_wired:
            raise RealExecutionUnavailableError(
                [
                    DependencyGap(
                        kind=DependencyKind.SEARCH_PROVIDER,
                        reason=(
                            "Real pipeline stages are not wired in this build, so no "
                            "real research result can be produced."
                        ),
                        remediation="Complete the T08-T12 stage assembly.",
                    )
                ]
            )
        if self._run_research is None:
            raise RealExecutionUnavailableError([
                DependencyGap(kind=DependencyKind.SEARCH_PROVIDER,
                              reason="No production composition has been supplied.")
            ])
        return self._run_research(task_id, cancel_event=cancel_event)


def select_executor(
    mode: ExecutionMode,
    *,
    demo: DemoTaskExecutor,
    real: RealTaskExecutor,
) -> TaskExecutor:
    """Pick the executor for a task. There is no implicit fallback between them."""

    return demo if mode is ExecutionMode.DEMO else real
