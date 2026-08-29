from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_m0_seed_has_one_development_and_three_evaluation_topics() -> None:
    seed = json.loads((ROOT / "evaluation/seeds/m0_topics.json").read_text("utf-8"))
    development = [topic for topic in seed["topics"] if topic["split"] == "development"]
    evaluation = [topic for topic in seed["topics"] if topic["split"] == "evaluation"]

    assert len(development) == 1
    assert len(evaluation) >= 3
    assert seed["human_review_status"] == "pending_user_approval"


def test_each_topic_has_reviewable_scope() -> None:
    seed = json.loads((ROOT / "evaluation/seeds/m0_topics.json").read_text("utf-8"))
    for topic in seed["topics"]:
        assert 3 <= len(topic["questions"]) <= 5
        assert len(topic["critical_papers"]) >= 3
        assert topic["exclusions"]


def test_identity_conflict_is_not_silently_resolved() -> None:
    seed = json.loads((ROOT / "evaluation/seeds/m0_topics.json").read_text("utf-8"))
    conflicts = seed["known_identity_conflicts"]

    assert any(conflict["identifier"] == "arXiv:2310.11511" for conflict in conflicts)
    assert all("human review" in conflict["resolution"] for conflict in conflicts)
