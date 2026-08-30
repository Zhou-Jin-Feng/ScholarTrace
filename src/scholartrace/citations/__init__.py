"""Explicit bibliographic citation expansion and deterministic graph analysis."""

from scholartrace.citations.graph import CitationGraphBuilder
from scholartrace.citations.lifecycle import CitationLifecycleGate
from scholartrace.citations.provider import OpenAlexCitationProvider

__all__ = [
    "CitationGraphBuilder",
    "CitationLifecycleGate",
    "OpenAlexCitationProvider",
]
