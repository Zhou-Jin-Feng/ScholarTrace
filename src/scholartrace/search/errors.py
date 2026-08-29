"""Public, structured failures for academic metadata sources."""

from __future__ import annotations

from scholartrace.search.models import PublicErrorCode


class AcademicSourceError(Exception):
    """A bounded source failure that is safe to expose in run diagnostics."""

    def __init__(
        self,
        *,
        code: PublicErrorCode,
        public_reason: str,
        attempts: int,
        http_status: int | None = None,
    ) -> None:
        super().__init__(public_reason)
        self.code = code
        self.public_reason = public_reason
        self.attempts = attempts
        self.http_status = http_status
