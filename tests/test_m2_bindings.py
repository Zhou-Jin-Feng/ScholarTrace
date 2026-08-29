from __future__ import annotations

from pathlib import Path

import pytest

from scholartrace.contracts import DocuMindBinding
from scholartrace.evidence.bindings import BindingConflictError, DocuMindBindingRepository


def _binding(*, index: str = "b", source: str = "c") -> DocuMindBinding:
    return DocuMindBinding(
        canonical_paper_id="doi:10.1000/test",
        document_key="a" * 64,
        index_id=index * 64,
        source_sha256=source * 64,
        documind_version="2.2.0",
        retrieval_schema_version="1.0",
    )


def test_binding_repository_is_idempotent_and_persistent(tmp_path: Path) -> None:
    path = tmp_path / "bindings.sqlite3"
    repository = DocuMindBindingRepository(path)

    assert repository.get("doi:10.1000/test") is None
    assert repository.put(_binding()) == _binding()
    assert repository.put(_binding()) == _binding()
    assert DocuMindBindingRepository(path).get("doi:10.1000/test") == _binding()


def test_binding_update_requires_explicit_index_compare_and_swap(tmp_path: Path) -> None:
    repository = DocuMindBindingRepository(tmp_path / "bindings.sqlite3")
    repository.put(_binding())
    replacement = _binding(index="d", source="e")

    with pytest.raises(BindingConflictError, match="changed unexpectedly"):
        repository.put(replacement)
    with pytest.raises(BindingConflictError, match="changed unexpectedly"):
        repository.put(replacement, expected_previous_index_id="f" * 64)

    assert repository.put(replacement, expected_previous_index_id="b" * 64) == replacement
    assert repository.get("doi:10.1000/test") == replacement


def test_binding_compare_and_swap_cannot_change_document_identity(tmp_path: Path) -> None:
    repository = DocuMindBindingRepository(tmp_path / "bindings.sqlite3")
    repository.put(_binding())
    replacement = _binding(index="d").model_copy(update={"document_key": "f" * 64})

    with pytest.raises(BindingConflictError, match="cannot change document"):
        repository.put(replacement, expected_previous_index_id="b" * 64)
