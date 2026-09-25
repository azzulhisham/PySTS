"""
Shared read-only Postgres engine for MANTIS REST API handlers.

Designed so MANTIS does not starve other apps on the same RDS:

- One SQLAlchemy pool per Gunicorn worker (not one pool per HTTP request).
- Small pool (default 1 connection / worker): sync workers handle one request at a time.
- Session GUCs: statement + lock + idle-in-transaction timeouts; read-only transactions.
- No INSERT/UPDATE/DELETE in restapi — SELECT-only workload.
"""

from __future__ import annotations

import os
import threading
from urllib.parse import quote

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_pswd = os.environ.get("pnav_db_password", "m4r1t1m3")
DATABASE_URL = (
    f"postgresql://postgresadmin:{quote(_pswd)}"
    f"@marineai2.cxwk8yige5f2.ap-southeast-5.rds.amazonaws.com:5432/pnav"
)

# milliseconds (PostgreSQL GUC units for these settings)
API_STATEMENT_TIMEOUT_MS = int(os.environ.get("mantis_pg_statement_timeout_ms", "60000"))
API_LOCK_TIMEOUT_MS = int(os.environ.get("mantis_pg_lock_timeout_ms", "15000"))
API_IDLE_IN_TXN_TIMEOUT_MS = int(os.environ.get("mantis_pg_idle_in_txn_timeout_ms", "60000"))

# Default 2: overview runs Postgres modules and spoofing (PG static/OFAC) in parallel threads.
API_POOL_SIZE = int(os.environ.get("mantis_pg_pool_size", "2"))
API_MAX_OVERFLOW = int(os.environ.get("mantis_pg_max_overflow", "0"))

_PG_SESSION_OPTIONS = " ".join(
    [
        f"-c statement_timeout={API_STATEMENT_TIMEOUT_MS}",
        f"-c lock_timeout={API_LOCK_TIMEOUT_MS}",
        f"-c idle_in_transaction_session_timeout={API_IDLE_IN_TXN_TIMEOUT_MS}",
        "-c default_transaction_read_only=on",
    ]
)

_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_pg_engine() -> Engine:
    """Return the process-wide read-only engine (one pool per Gunicorn worker)."""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            _engine = create_engine(
                DATABASE_URL,
                pool_size=API_POOL_SIZE,
                max_overflow=API_MAX_OVERFLOW,
                pool_timeout=30,
                pool_pre_ping=True,
                connect_args={"options": _PG_SESSION_OPTIONS},
            )
    return _engine
