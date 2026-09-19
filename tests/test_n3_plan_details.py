from datetime import date

import pytest
from pydantic import ValidationError

from scholartrace.contracts import ResearchSubquestion
from scholartrace.delivery.models import PlanModification, ResearchPlanView
from scholartrace.delivery.plans import (
    BudgetPlan,
    PlanDetails,
    PlanError,
    PlanOrigin,
    PlanStore,
    SourceScope,
    compute_plan_digest,
)
from scholartrace.delivery.store import DeliveryStore


def details():
    return PlanDetails(
        title="Synthetic plan",
        objective="Compare evidence and limitations",
        inclusion_criteria=("Peer reviewed experimental evidence",),
        subquestions=(
            ResearchSubquestion(
                subquestion_id="subq:1",
                question="First question",
                evidence_required="fulltext",
                priority="high",
            ),
            ResearchSubquestion(
                subquestion_id="subq:2",
                question="Second question",
                evidence_required="fulltext",
                priority="normal",
            ),
        ),
        retrieval_cutoff=date(2026, 9, 18),
    )


def values():
    return dict(
        sub_questions=("First question", "Second question"),
        source_scope=SourceScope(providers=("arxiv",), year_from=2020, year_to=2026),
        exclusions=("Exclude opinion pieces",),
        budget_plan=BudgetPlan(
            max_cny=1, max_api_calls=3, max_wall_clock_seconds=600, estimate_source="synthetic"
        ),
    )


def test_details_persist_and_are_bound_to_digest_without_schema_changes(tmp_path):
    path = tmp_path / "tasks.sqlite"
    store = DeliveryStore(path)
    plan = PlanStore(store.connection, store._lock)
    before = tuple(store.connection.execute("SELECT sql FROM sqlite_master ORDER BY name"))
    try:
        record = plan.save_plan(
            task_id="synthetic",
            generated_by=PlanOrigin.API_STRONG,
            require_acknowledgement=False,
            details=details(),
            **values(),
        )
        assert record.details == details()
        assert (
            tuple(store.connection.execute("SELECT sql FROM sqlite_master ORDER BY name")) == before
        )
        view = ResearchPlanView.model_validate(record.as_public_dict())
        assert len(view.details.subquestions) == 2
        assert view.details.inclusion_criteria == details().inclusion_criteria
    finally:
        store.close()
    store = DeliveryStore(path)
    try:
        recovered = PlanStore(store.connection, store._lock).current_plan("synthetic")
        assert recovered.details == details()
        assert recovered.plan_digest == record.plan_digest
        assert recovered.plan_digest == compute_plan_digest(details=details(), **values())
        changed = details().model_copy(update={"objective": "Changed objective"})
        assert compute_plan_digest(details=changed, **values()) != record.plan_digest
        assert compute_plan_digest(details=None, **values()) == compute_plan_digest(**values())
    finally:
        store.close()


def test_details_cannot_silently_disagree_with_review_fields(tmp_path):
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        plan = PlanStore(store.connection, store._lock)
        with pytest.raises(PlanError, match="subquestions"):
            plan.save_plan(
                task_id="synthetic",
                generated_by=PlanOrigin.MANUAL,
                require_acknowledgement=False,
                details=details(),
                **(values() | {"sub_questions": ("Changed without detailed review",)}),
            )
        with pytest.raises(ValidationError):
            PlanModification.model_validate(
                values()
                | {
                    "sub_questions": ["Changed without detailed review"],
                    "details": details(),
                }
            )
        assert store.connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
    finally:
        store.close()
