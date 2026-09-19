"""Explicit, credential-free policy snapshots for durable call authorization."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AuthorizationError(ValueError):
    """An explicit grant is missing, incompatible, expired or revoked."""


class AuthorizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProviderPolicy(AuthorizationModel):
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    protocol: Literal["responses", "chat_completions", "ollama"]
    input_cny_per_million: str
    output_cny_per_million: str
    price_observed_at: str
    max_input_tokens: int = Field(ge=1, le=2**31 - 1)
    max_output_tokens: int = Field(ge=1, le=2**31 - 1)

    @field_validator("input_cny_per_million", "output_cny_per_million")
    @classmethod
    def decimal_price(cls, value: str) -> str:
        return money(value)

    @field_validator("price_observed_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        return utc_timestamp(value)

    @model_validator(mode="after")
    def safe_endpoint(self) -> ProviderPolicy:
        url = urlsplit(self.endpoint)
        if (
            not url.hostname or url.username or url.password or url.query or url.fragment
            or url.scheme not in {"http", "https"}
        ):
            raise ValueError("provider endpoint must not contain credentials or query parameters")
        if self.protocol == "ollama":
            if url.path != "/api/chat":
                raise ValueError("local generation requires an explicit Ollama chat route")
            if Decimal(self.input_cny_per_million) or Decimal(self.output_cny_per_million):
                raise ValueError("local provider does not use remote inference prices")
        else:
            path = "/v1/responses" if self.protocol == "responses" else "/v1/chat/completions"
            if url.scheme != "https" or url.path != path:
                raise ValueError("remote provider requires an exact HTTPS inference route")
        if not self.model.strip() or not self.model_version.strip():
            raise ValueError("model identity must not be blank")
        return self


class RuntimePolicy(AuthorizationModel):
    schema_version: Literal["1.0"] = "1.0"
    remote: ProviderPolicy
    local: ProviderPolicy | None = None
    documind_url: str | None = None
    documind_version: Literal["3.0.0"] = "3.0.0"
    data_fields: tuple[
        Literal["question", "paper_metadata", "selected_pdf", "retrieved_chunks", "claims",
                "evidence_quotes", "verification_results"], ...
    ]
    allowed_search_providers: tuple[
        Literal["arxiv", "openalex", "crossref", "semantic_scholar"], ...
    ]
    max_papers: int = Field(ge=3, le=5)
    max_rounds: Literal[1] = 1

    @model_validator(mode="after")
    def validate_roles(self) -> RuntimePolicy:
        if self.remote.protocol == "ollama":
            raise ValueError("remote profile must be an approved remote inference provider")
        if self.local is not None and self.local.protocol != "ollama":
            raise ValueError("local profile must not silently become a remote model")
        if len(self.data_fields) != len(set(self.data_fields)):
            raise ValueError("duplicate data-scope fields")
        if len(self.allowed_search_providers) != len(set(self.allowed_search_providers)):
            raise ValueError("duplicate search providers")
        if self.documind_url is not None:
            url = urlsplit(self.documind_url)
            if (url.scheme not in {"http", "https"} or not url.hostname
                    or url.username or url.password or url.query or url.fragment
                    or url.path not in {"", "/"}):
                raise ValueError("DocuMind origin must not contain credentials or a route")
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"), allow_nan=False)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def money(value: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?", value):
        raise ValueError("amount must be a nonnegative decimal string of bounded precision")
    return format(Decimal(value).normalize(), "f")


def utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be an explicit UTC ISO8601 value") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must include UTC timezone")
    return parsed.isoformat()


class TaskGrant(AuthorizationModel):
    task_id: str = Field(min_length=1, max_length=200)
    policy: RuntimePolicy
    max_cny: str
    max_remote_calls: int = Field(ge=0, le=2**63 - 1)
    max_local_calls: int = Field(ge=0, le=2**63 - 1)
    max_external_requests: int = Field(ge=0, le=2**63 - 1)
    deadline_at: str

    @field_validator("max_cny")
    @classmethod
    def decimal_amount(cls, value: str) -> str:
        return money(value)

    @field_validator("deadline_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        return utc_timestamp(value)


class PhaseGrant(AuthorizationModel):
    task_id: str = Field(min_length=1, max_length=200)
    authorization_id: str = Field(min_length=1, max_length=200)
    phase: Literal["planning", "execution"]
    generation: int = Field(ge=1, le=2**63 - 1)
    plan_version: int | None = Field(default=None, ge=1, le=2**63 - 1)
    plan_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_cny: str
    max_remote_calls: int = Field(ge=0, le=2**63 - 1)
    max_local_calls: int = Field(ge=0, le=2**63 - 1)
    max_external_requests: int = Field(ge=0, le=2**63 - 1)
    acknowledgement_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("max_cny")
    @classmethod
    def decimal_amount(cls, value: str) -> str:
        return money(value)

    @model_validator(mode="after")
    def bind_plan(self) -> PhaseGrant:
        if self.phase == "planning":
            if self.plan_version is not None or self.plan_digest is not None:
                raise ValueError("planning authorization cannot bind an execution plan")
        elif self.plan_version is None or self.plan_digest is None:
            raise ValueError("execution authorization requires plan version and digest")
        return self


class CallContext(AuthorizationModel):
    authorization_id: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=200)
    call_kind: Literal["remote_model", "local_model", "external_request", "stage"]
    attempt: Literal[1, 2] = 1
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_version: int | None = Field(default=None, ge=1)
    plan_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("attempt", mode="before")
    @classmethod
    def strict_attempt(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("attempt must be an integer")
        return value

    def effect_key(self) -> str:
        identity = (self.authorization_id, self.operation_id, self.attempt)
        return "authorized:" + hashlib.sha256(
            json.dumps(identity, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class CallUsage(AuthorizationModel):
    input_tokens: int | None = Field(default=None, ge=0, le=2**63 - 1)
    output_tokens: int | None = Field(default=None, ge=0, le=2**63 - 1)
    duration_ns: int | None = Field(default=None, ge=0, le=2**63 - 1)
    http_status: int | None = Field(default=None, ge=100, le=599)


class PhaseLimits(AuthorizationModel):
    max_cny: str
    max_remote_calls: int = Field(ge=0, le=2**63 - 1)
    max_local_calls: int = Field(ge=0, le=2**63 - 1)
    max_external_requests: int = Field(ge=0, le=2**63 - 1)

    @field_validator("max_cny")
    @classmethod
    def decimal_amount(cls, value: str) -> str:
        return money(value)


class PlanningAuthorization(PhaseLimits):
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    deadline_at: str
    max_wall_clock_seconds: int = Field(ge=1, le=86400)
    generation: int = Field(ge=1, le=2**63 - 1)
    planning: PhaseLimits

    @field_validator("deadline_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        return utc_timestamp(value)

    @model_validator(mode="after")
    def planning_only(self) -> PlanningAuthorization:
        if self.planning.max_local_calls != 0 or self.planning.max_external_requests != 0:
            raise ValueError("planning authorization cannot grant local or external requests")
        return self


class ExecutionAuthorization(PhaseLimits):
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation: int = Field(ge=1, le=2**63 - 1)


def validate_call_context(
    db: sqlite3.Connection, task_id: str, context: CallContext, *, now: datetime,
    reserve_cny: Decimal, reserve_calls: int,
) -> sqlite3.Row:
    task = active_task(db, task_id, now)
    phase = db.execute(
        "SELECT * FROM phase_authorizations WHERE task_id=? AND authorization_id=?",
        (task_id, context.authorization_id),
    ).fetchone()
    if (phase is None or phase["state"] != "active"
            or context.policy_sha256 != task["policy_sha256"]
            or phase["policy_sha256"] != context.policy_sha256
            or phase["plan_version"] != context.plan_version
            or phase["plan_digest"] != context.plan_digest):
        raise AuthorizationError("call does not match an active phase and approved plan")
    if phase["phase"] == "planning" and context.call_kind != "remote_model":
        raise AuthorizationError("planning authorization permits only remote model calls")
    if reserve_calls != int(context.call_kind == "remote_model"):
        raise AuthorizationError("remote call reservation does not match call kind")
    if context.call_kind != "remote_model" and reserve_cny != 0:
        raise AuthorizationError("only remote model calls reserve inference cost")
    return phase  # type: ignore[no-any-return]


def check_context_capacity(
    db: sqlite3.Connection, task_id: str, context: CallContext, phase: sqlite3.Row,
    *, reserve_cny: Decimal, reserve_calls: int,
) -> None:
    """Use all historical generations of a phase, never a fresh grant allowance."""
    if db.execute(
        "SELECT 1 FROM effects WHERE task_id=? AND state='unknown' LIMIT 1", (task_id,),
    ).fetchone() is not None:
        raise AuthorizationError("task has uncertain exposure; reconciliation required")
    task = db.execute(
        "SELECT t.*,b.max_cny,b.max_calls FROM task_authorizations t "
        "JOIN effect_budgets b USING(task_id) WHERE task_id=?", (task_id,),
    ).fetchone()
    rows = db.execute(
        "SELECT e.*,c.reserve_local_calls,c.reserve_external_requests,p.phase "
        "FROM effects e JOIN effect_contexts c USING(task_id,effect_key) "
        "JOIN phase_authorizations p ON p.task_id=c.task_id "
        "AND p.authorization_id=c.authorization_id WHERE e.task_id=? AND e.state!='retryable'",
        (task_id,),
    ).fetchall()
    for relevant, ceiling, remote_limit, local_limit, external_limit in (
        (rows, task["max_cny"], task["max_calls"],
         task["max_local_calls"], task["max_external_requests"]),
        ([r for r in rows if r["phase"] == phase["phase"]], phase["max_cny"],
         phase["max_remote_calls"], phase["max_local_calls"], phase["max_external_requests"]),
    ):
        committed = sum((
            Decimal(r["measured_cny"]) if r["state"] == "completed" else
            max(Decimal(r["reserve_cny"]), Decimal(r["measured_cny"] or "0"))
            for r in relevant
        ), Decimal(0))
        if (committed + reserve_cny > Decimal(ceiling)
                or sum(r["reserve_calls"] for r in relevant) + reserve_calls > remote_limit
                or sum(r["reserve_local_calls"] for r in relevant)
                + int(context.call_kind == "local_model") > local_limit
                or sum(r["reserve_external_requests"] for r in relevant)
                + int(context.call_kind == "external_request") > external_limit):
            raise AuthorizationError("call reservation exceeds task or cumulative phase capacity")


def check_operation_attempt(
    db: sqlite3.Connection, task_id: str, context: CallContext,
) -> None:
    """A timeout cannot be retried by changing attempt or phase generation."""
    previous = db.execute(
        "SELECT e.state,c.attempt,c.authorization_id FROM effects e "
        "JOIN effect_contexts c USING(task_id,effect_key) "
        "WHERE e.task_id=? AND c.operation_id=?", (task_id, context.operation_id),
    ).fetchall()
    if any(r["state"] in {"pending", "unknown"} for r in previous):
        raise AuthorizationError("operation has uncertain exposure; reconciliation required")
    if any(r["authorization_id"] != context.authorization_id for r in previous):
        raise AuthorizationError("operation identity cannot move to a new authorization")
    if context.attempt == 2 and not any(
        r["attempt"] == 1 and r["state"] == "completed" for r in previous
    ):
        raise AuthorizationError("second attempt requires a confirmed first result")
    if context.attempt == 1 and previous:
        raise AuthorizationError("operation identity already exists")


def active_task(db: sqlite3.Connection, task_id: str, now: datetime) -> sqlite3.Row:
    row = db.execute(
        "SELECT t.*,b.max_cny,b.max_calls FROM task_authorizations t "
        "JOIN effect_budgets b USING(task_id) WHERE task_id=?", (task_id,),
    ).fetchone()
    if (row is None or row["state"] != "active" or now.tzinfo is None
            or datetime.fromisoformat(row["deadline_at"]) <= now):
        raise AuthorizationError("task authorization missing, revoked or expired")
    if hashlib.sha256(row["policy_json"].encode("utf-8")).hexdigest() != row["policy_sha256"]:
        raise AuthorizationError("stored policy digest is inconsistent")
    return row  # type: ignore[no-any-return]


def save_phase_grant(db: sqlite3.Connection, grant: PhaseGrant, *, now: datetime) -> None:
    """Prepare only; durable cross-store approval must precede activation."""
    if not db.in_transaction:
        raise AuthorizationError("phase grant requires a write transaction")
    task = active_task(db, grant.task_id, now)
    if (grant.policy_sha256 != task["policy_sha256"]
            or Decimal(grant.max_cny) > Decimal(task["max_cny"])
            or grant.max_remote_calls > task["max_calls"]
            or grant.max_local_calls > task["max_local_calls"]
            or grant.max_external_requests > task["max_external_requests"]):
        raise AuthorizationError("phase grant exceeds or differs from task authorization")
    fields = tuple(grant.model_dump())
    values = tuple(grant.model_dump().values())
    row = db.execute(
        "SELECT * FROM phase_authorizations WHERE task_id=? AND authorization_id=?",
        (grant.task_id, grant.authorization_id),
    ).fetchone()
    if row is not None:
        if tuple(row[f] for f in fields) != values or row["state"] not in {"pending", "active"}:
            raise AuthorizationError("phase authorization cannot change or be revived")
        return
    latest = db.execute(
        "SELECT max(generation) FROM phase_authorizations WHERE task_id=? AND phase=?",
        (grant.task_id, grant.phase),
    ).fetchone()[0]
    if latest is not None and grant.generation <= latest:
        raise AuthorizationError("phase generation must increase")
    db.execute(
        f"INSERT INTO phase_authorizations ({','.join(fields)},state,created_at) "
        f"VALUES ({','.join('?' for _ in fields)},'pending',?)",
        (*values, now.astimezone(UTC).isoformat()),
    )


def activate_phase_grant(
    db: sqlite3.Connection, grant: PhaseGrant, *, now: datetime,
) -> None:
    """Activate the exact prepared grant after the caller verifies durable approval."""
    if not db.in_transaction:
        raise AuthorizationError("phase activation requires a write transaction")
    row = db.execute(
        "SELECT state FROM phase_authorizations WHERE task_id=? AND authorization_id=?",
        (grant.task_id, grant.authorization_id),
    ).fetchone()
    if row is None:
        raise AuthorizationError("phase must be prepared before activation")
    save_phase_grant(db, grant, now=now)
    latest = db.execute(
        "SELECT max(generation) FROM phase_authorizations WHERE task_id=? AND phase=?",
        (grant.task_id, grant.phase),
    ).fetchone()[0]
    if latest != grant.generation:
        raise AuthorizationError("newer phase generation requires review")
    if row["state"] == "active":
        return
    db.execute(
        "UPDATE phase_authorizations SET state='superseded' "
        "WHERE task_id=? AND phase=? AND state='active'",
        (grant.task_id, grant.phase),
    )
    db.execute(
        "UPDATE phase_authorizations SET state='active',activated_at=? "
        "WHERE task_id=? AND authorization_id=?",
        (now.astimezone(UTC).isoformat(), grant.task_id, grant.authorization_id),
    )


def save_task_grant(db: sqlite3.Connection, grant: TaskGrant, *, now: datetime) -> None:
    """Insert an immutable envelope. Caller owns the journal lock and transaction.

    This does not activate a phase and cannot itself authorize any dispatch.
    Existing legacy task IDs are refused rather than promoted to live grants.
    """
    if not db.in_transaction:
        raise AuthorizationError("grant must be saved inside the journal write transaction")
    if now.tzinfo is None or datetime.fromisoformat(grant.deadline_at) <= now:
        raise AuthorizationError("task grant is expired or clock is not timezone-aware")
    values = (
        grant.task_id, grant.policy.digest(), grant.policy.canonical_json(),
        grant.max_local_calls, grant.max_external_requests, grant.deadline_at,
        "active",
    )
    existing = db.execute(
        "SELECT task_id,policy_sha256,policy_json,max_local_calls,max_external_requests,"
        "deadline_at,state FROM task_authorizations WHERE task_id=?", (grant.task_id,),
    ).fetchone()
    budget = db.execute(
        "SELECT max_cny,max_calls FROM effect_budgets WHERE task_id=?", (grant.task_id,),
    ).fetchone()
    if existing is not None:
        if (tuple(existing) != values or budget is None
                or Decimal(budget[0]) != Decimal(grant.max_cny)
                or budget[1] != grant.max_remote_calls):
            raise AuthorizationError("task authorization cannot change or be reactivated")
        return
    if budget is not None:
        raise AuthorizationError("legacy task budget cannot be promoted to live authorization")
    db.execute("INSERT INTO effect_budgets VALUES (?,?,?)",
               (grant.task_id, grant.max_cny, grant.max_remote_calls))
    db.execute(
        "INSERT INTO task_authorizations VALUES (?,?,?,?,?,?,?,?,NULL)",
        (*values, now.astimezone(UTC).isoformat()),
    )
