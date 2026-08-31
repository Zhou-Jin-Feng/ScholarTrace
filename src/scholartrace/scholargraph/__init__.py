"""Capability-limited ScholarGraph integration for M5."""

from scholartrace.scholargraph.client import ScholarGraphClient
from scholartrace.scholargraph.models import (
    CapabilitiesResponse,
    QueryRequest,
    QueryResponse,
    ReadyResponse,
)

__all__ = [
    "CapabilitiesResponse",
    "QueryRequest",
    "QueryResponse",
    "ReadyResponse",
    "ScholarGraphClient",
]
