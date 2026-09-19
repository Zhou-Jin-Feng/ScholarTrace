"""Atomic additive schema for durable effect authorizations.

Legacy result rows retain their original layout and never acquire live grants.
The version here belongs to the effect journal, not the delivery task database.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

EFFECT_SCHEMA_VERSION = 1

LEGACY_DDL = (
    """CREATE TABLE effect_budgets (
        task_id TEXT PRIMARY KEY, max_cny TEXT NOT NULL, max_calls INTEGER NOT NULL)""",
    """CREATE TABLE effects (
        task_id TEXT NOT NULL, effect_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        reserve_cny TEXT NOT NULL, reserve_calls INTEGER NOT NULL,
        state TEXT NOT NULL, attempts INTEGER NOT NULL,
        measured_cny TEXT, result_json TEXT, PRIMARY KEY(task_id, effect_key))""",
)
AUTHORIZATION_DDL = (
    """CREATE TABLE effect_schema_metadata (
        key TEXT PRIMARY KEY CHECK(key='schema_version'), value TEXT NOT NULL)""",
    """CREATE TABLE task_authorizations (
        task_id TEXT PRIMARY KEY REFERENCES effect_budgets(task_id),
        policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
        policy_json TEXT NOT NULL,
        max_local_calls INTEGER NOT NULL CHECK(max_local_calls>=0),
        max_external_requests INTEGER NOT NULL CHECK(max_external_requests>=0),
        deadline_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('active','revoked')),
        created_at TEXT NOT NULL, revoked_at TEXT,
        CHECK((state='active' AND revoked_at IS NULL)
            OR (state='revoked' AND revoked_at IS NOT NULL)))""",
    """CREATE TABLE phase_authorizations (
        task_id TEXT NOT NULL REFERENCES task_authorizations(task_id),
        authorization_id TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('planning','execution')),
        generation INTEGER NOT NULL CHECK(generation>=1),
        plan_version INTEGER, plan_digest TEXT,
        policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
        max_cny TEXT NOT NULL,
        max_remote_calls INTEGER NOT NULL CHECK(max_remote_calls>=0),
        max_local_calls INTEGER NOT NULL CHECK(max_local_calls>=0),
        max_external_requests INTEGER NOT NULL CHECK(max_external_requests>=0),
        state TEXT NOT NULL CHECK(state IN ('pending','active','superseded','revoked')),
        acknowledgement_sha256 TEXT NOT NULL CHECK(length(acknowledgement_sha256)=64),
        created_at TEXT NOT NULL, activated_at TEXT,
        PRIMARY KEY(task_id,authorization_id), UNIQUE(task_id,phase,generation),
        CHECK((phase='planning' AND plan_version IS NULL AND plan_digest IS NULL)
            OR (phase='execution' AND plan_version IS NOT NULL AND plan_version>=1
                AND plan_digest IS NOT NULL AND length(plan_digest)=64)),
        CHECK(state!='active' OR activated_at IS NOT NULL))""",
    """CREATE UNIQUE INDEX phase_authorizations_active
        ON phase_authorizations(task_id,phase) WHERE state='active'""",
    """CREATE TABLE effect_contexts (
        task_id TEXT NOT NULL, effect_key TEXT NOT NULL,
        authorization_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        call_kind TEXT NOT NULL CHECK(call_kind IN
            ('remote_model','local_model','external_request','stage')),
        attempt INTEGER NOT NULL CHECK(attempt IN (1,2)),
        reserve_local_calls INTEGER NOT NULL CHECK(reserve_local_calls>=0),
        reserve_external_requests INTEGER NOT NULL CHECK(reserve_external_requests>=0),
        usage_json TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY(task_id,effect_key),
        UNIQUE(task_id,authorization_id,operation_id,attempt),
        FOREIGN KEY(task_id,effect_key) REFERENCES effects(task_id,effect_key),
        FOREIGN KEY(task_id,authorization_id)
            REFERENCES phase_authorizations(task_id,authorization_id),
        CHECK((call_kind='local_model' AND reserve_local_calls=1
                AND reserve_external_requests=0)
            OR (call_kind='external_request' AND reserve_local_calls=0
                AND reserve_external_requests=1)
            OR (call_kind IN ('remote_model','stage') AND reserve_local_calls=0
                AND reserve_external_requests=0)))""",
)


class EffectSchemaError(RuntimeError):
    """An unsupported or damaged effect schema cannot be used for dispatch."""


def _structure(db: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return sorted(
        (str(row[0]), str(row[1]), " ".join(str(row[2]).split()))
        for row in db.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        )
    )


def _expected(*, legacy: bool) -> list[tuple[str, str, str]]:
    with sqlite3.connect(":memory:") as reference:
        for sql in LEGACY_DDL + (() if legacy else AUTHORIZATION_DDL):
            reference.execute(sql)
        return _structure(reference)


def migrate_effect_schema(
    db: sqlite3.Connection,
    *,
    after_statement: Callable[[int], None] | None = None,
) -> None:
    """Validate and migrate in one write transaction, including new databases.

    The optional observer permits deterministic failure injection. It receives
    only a statement number, never data. Existing transactions are not committed.
    """
    if db.in_transaction:
        raise EffectSchemaError("effect migration requires an idle connection")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("BEGIN IMMEDIATE")
    try:
        layout = _structure(db)
        names = {row[1] for row in layout if row[0] == "table"}
        if "effect_schema_metadata" in names:
            if layout != _expected(legacy=False):
                raise EffectSchemaError("effect schema layout differs from the supported version")
            version = db.execute(
                "SELECT value FROM effect_schema_metadata WHERE key='schema_version'"
            ).fetchone()
            if version is None or str(version[0]) != str(EFFECT_SCHEMA_VERSION):
                raise EffectSchemaError("unsupported effect schema version")
        else:
            if layout and layout != _expected(legacy=True):
                raise EffectSchemaError("unsupported legacy effect schema")
            statements = (() if layout else LEGACY_DDL) + AUTHORIZATION_DDL
            for index, sql in enumerate(statements, 1):
                db.execute(sql)
                if after_statement is not None:
                    after_statement(index)
            db.execute(
                "INSERT INTO effect_schema_metadata VALUES ('schema_version',?)",
                (str(EFFECT_SCHEMA_VERSION),),
            )
            if after_statement is not None:
                after_statement(len(statements) + 1)
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise EffectSchemaError("effect authorization references are inconsistent")
        db.commit()
    except BaseException:
        db.rollback()
        raise
