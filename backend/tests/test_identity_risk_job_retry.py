"""Lock-timeout retry for identity_risk_job (no DB)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

BACKEND = Path(__file__).resolve().parents[1]
RESTAPI = BACKEND.parent / "restapi"
for p in (BACKEND, RESTAPI):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import identity_risk_job as job  # noqa: E402


def test_is_lock_timeout_error_detects_message():
    assert job.is_lock_timeout_error(OperationalError("stmt", {}, Exception("canceling statement due to lock timeout")))
    assert job.is_lock_timeout_error(RuntimeError("LockNotAvailable: canceling statement due to lock timeout"))
    assert not job.is_lock_timeout_error(RuntimeError("division by zero"))
    assert not job.is_lock_timeout_error(OperationalError("stmt", {}, Exception("statement timeout")))


def test_is_lock_timeout_error_detects_psycopg_type():
    try:
        from psycopg2 import errors as pg_errors

        exc: BaseException = pg_errors.LockNotAvailable(
            "canceling statement due to lock timeout"
        )
    except Exception:
        exc = type("LockNotAvailable", (Exception,), {})(
            "canceling statement due to lock timeout"
        )
    assert job.is_lock_timeout_error(exc)


def test_retry_succeeds_on_later_attempt(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []
    ticks = {"t": 0.0}

    def fake_run(*, lookback_months=24):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError(
                "DELETE FROM ais_ship_risk_rating",
                {},
                Exception("canceling statement due to lock timeout\n"),
            )
        return {"ratings_upserted": 10, "lookback_months": lookback_months}

    def fake_sleep(s):
        sleeps.append(s)
        ticks["t"] += s

    monkeypatch.setattr(job, "LOCK_RETRY_WAIT_S", 120)
    monkeypatch.setattr(job, "LOCK_RETRY_MAX_S", 1800)

    out = job.run_once_with_lock_retry(
        lookback_months=24,
        sleep_fn=fake_sleep,
        monotonic_fn=lambda: ticks["t"],
        run_fn=fake_run,
    )
    assert out["ratings_upserted"] == 10
    assert calls["n"] == 3
    assert sleeps == [120, 120]


def test_retry_gives_up_after_limit(monkeypatch):
    calls = {"n": 0}
    ticks = {"t": 0.0}

    def fake_run(*, lookback_months=24):
        calls["n"] += 1
        raise OperationalError(
            "DELETE",
            {},
            Exception("canceling statement due to lock timeout"),
        )

    def fake_sleep(s):
        ticks["t"] += s

    monkeypatch.setattr(job, "LOCK_RETRY_WAIT_S", 60)
    monkeypatch.setattr(job, "LOCK_RETRY_MAX_S", 120)  # two waits max after first fail

    with pytest.raises(RuntimeError, match="lock timeout after"):
        job.run_once_with_lock_retry(
            lookback_months=24,
            sleep_fn=fake_sleep,
            monotonic_fn=lambda: ticks["t"],
            run_fn=fake_run,
        )
    assert calls["n"] >= 2


def test_non_lock_error_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake_run(*, lookback_months=24):
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        job.run_once_with_lock_retry(
            lookback_months=24,
            sleep_fn=lambda _s: None,
            monotonic_fn=lambda: 0.0,
            run_fn=fake_run,
        )
    assert calls["n"] == 1


def test_main_returns_nonzero_after_lock_giveup(monkeypatch):
    def boom(*, lookback_months=24):
        raise RuntimeError("identity_risk_job failed: lock timeout after 3 attempt(s) over 1800s")

    monkeypatch.setattr(job, "run_once_with_lock_retry", boom)
    assert job.main([]) == 1
