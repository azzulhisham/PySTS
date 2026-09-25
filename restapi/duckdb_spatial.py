"""
Thread-safe DuckDB spatial helpers for the MANTIS REST API.

Each HTTP handler gets its own in-memory DuckDB connection so concurrent
requests do not share temp views or pending results on the global connection.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

import duckdb
import pandas as pd

_install_lock = threading.Lock()
_extension_installed = False


def _load_spatial(conn: duckdb.DuckDBPyConnection) -> None:
    global _extension_installed
    with _install_lock:
        if not _extension_installed:
            conn.execute("INSTALL spatial")
            _extension_installed = True
        conn.execute("LOAD spatial")


@contextmanager
def duckdb_spatial_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect()
    try:
        _load_spatial(conn)
        yield conn
    finally:
        conn.close()


def spatial_df(sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    with duckdb_spatial_connection() as conn:
        for name, frame in tables.items():
            conn.register(name, frame)
        return conn.sql(sql).df()


def spatial_fetchone(sql: str, tables: dict[str, pd.DataFrame]) -> tuple[Any, ...] | None:
    with duckdb_spatial_connection() as conn:
        for name, frame in tables.items():
            conn.register(name, frame)
        return conn.sql(sql).fetchone()
