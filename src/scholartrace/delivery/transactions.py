"""Composable SQLite transactions for the single-process delivery store."""

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """An inner store operation must not commit its caller's transaction."""
    name = "delivery_" + uuid.uuid4().hex
    connection.execute(f"SAVEPOINT {name}")
    try:
        yield
        connection.execute(f"RELEASE SAVEPOINT {name}")
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        connection.execute(f"RELEASE SAVEPOINT {name}")
        raise
