from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from scholartrace.delivery.effect_schema import (
    AUTHORIZATION_DDL,
    LEGACY_DDL,
    EffectSchemaError,
    migrate_effect_schema,
)
from scholartrace.delivery.effects import EffectJournal


def legacy(db):
    for sql in LEGACY_DDL:
        db.execute(sql)
    db.execute("INSERT INTO effect_budgets VALUES ('old','5.25',6)")
    for key, state in enumerate(('completed', 'pending', 'unknown', 'retryable')):
        db.execute(
            "INSERT INTO effects VALUES ('old',?,'digest','0.5',1,?,1,'0.25',?)",
            (str(key), state, '{"synthetic":true}'),
        )
    db.commit()


def dump(db):
    return tuple(db.iterdump())


def test_legacy_rows_stay_unchanged_and_never_gain_authorization(tmp_path):
    path = tmp_path / 'effects.sqlite'
    with sqlite3.connect(path) as db:
        legacy(db)
        before = db.execute('SELECT * FROM effects').fetchall()
        migrate_effect_schema(db)
        assert db.execute('SELECT * FROM effects').fetchall() == before
        assert db.execute('SELECT count(*) FROM task_authorizations').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM phase_authorizations').fetchone()[0] == 0
        migrated = dump(db)
        migrate_effect_schema(db)
        assert dump(db) == migrated
    journal = EffectJournal(path)
    try:
        assert journal.summary('old')['uncertain_effects'] == 2
        assert journal.db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    finally:
        journal.close()


@pytest.mark.parametrize('existing', [False, True])
def test_every_ddl_and_version_failure_rolls_back_entire_schema(tmp_path, existing):
    count = len(AUTHORIZATION_DDL) + (0 if existing else len(LEGACY_DDL)) + 1
    for fail_at in range(1, count + 1):
        with sqlite3.connect(tmp_path / f'{existing}-{fail_at}.sqlite') as db:
            if existing:
                legacy(db)
            before = dump(db)

            def fail(index, target=fail_at):
                if index == target:
                    raise RuntimeError('synthetic migration failure')

            with pytest.raises(RuntimeError, match='synthetic'):
                migrate_effect_schema(db, after_statement=fail)
            assert dump(db) == before
            assert not db.in_transaction
            migrate_effect_schema(db)


def test_future_version_and_damaged_layout_are_refused_without_changes(tmp_path):
    with sqlite3.connect(tmp_path / 'future.sqlite') as db:
        migrate_effect_schema(db)
        db.execute("UPDATE effect_schema_metadata SET value='999'")
        db.commit()
        before = dump(db)
        with pytest.raises(EffectSchemaError, match='version'):
            migrate_effect_schema(db)
        assert dump(db) == before
    with sqlite3.connect(tmp_path / 'broken.sqlite') as db:
        db.execute('CREATE TABLE effects (task_id TEXT)')
        db.commit()
        before = dump(db)
        with pytest.raises(EffectSchemaError, match='legacy'):
            migrate_effect_schema(db)
        assert dump(db) == before


def test_parallel_connections_initialize_one_consistent_schema(tmp_path):
    path = tmp_path / 'parallel.sqlite'

    def opened(_):
        with sqlite3.connect(path, timeout=5) as db:
            migrate_effect_schema(db)
            return db.execute('SELECT value FROM effect_schema_metadata').fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(opened, range(2))) == ['1', '1']


def test_caller_transaction_is_not_committed_or_rolled_back(tmp_path):
    with sqlite3.connect(tmp_path / 'transaction.sqlite') as db:
        legacy(db)
        db.execute("UPDATE effect_budgets SET max_cny='6'")
        with pytest.raises(EffectSchemaError, match='idle'):
            migrate_effect_schema(db)
        assert db.in_transaction
        db.rollback()
        assert db.execute('SELECT max_cny FROM effect_budgets').fetchone()[0] == '5.25'
