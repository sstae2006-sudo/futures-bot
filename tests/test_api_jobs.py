"""Tests for `api.jobs.JobManager` -- submission, progress updates, and
completion/failure, independent of the backtest/optimizer work it usually
wraps (see `test_api_services.py::TestBackgroundJobs` for that)."""

from __future__ import annotations

import time

import pytest

from futures_bot.api import jobs


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "jobs.db"))
    jobs.reset_executor()
    yield
    jobs.reset_executor()


def _wait_for_terminal(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get_job(job_id)
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.02)
    raise TimeoutError(f"Job {job_id} did not reach a terminal state within {timeout}s")


class TestSubmitAndComplete:
    def test_successful_job_completes_with_result_id(self):
        def work(progress):
            progress.update(1, 1, message="done")
            return "result-42", None

        job_id = jobs.submit("backtest", {"strategy_name": "x"}, work)
        job = _wait_for_terminal(job_id)

        assert job["status"] == "completed"
        assert job["result_id"] == "result-42"
        assert job["completed_at"] is not None

    def test_job_starts_as_queued_before_worker_picks_it_up(self):
        # Submit a job whose work function blocks briefly, then check status
        # immediately -- it should be queued or running, never "completed"
        # before the worker thread has actually run.
        import threading

        gate = threading.Event()

        def work(progress):
            gate.wait(timeout=2)
            return None, None

        job_id = jobs.submit("backtest", {}, work)
        immediate = jobs.get_job(job_id)
        assert immediate["status"] in ("queued", "running")

        gate.set()
        _wait_for_terminal(job_id)

    def test_result_payload_round_trips(self):
        def work(progress):
            return None, {"entries": [{"strategy": "x", "net_pnl": "12.50"}]}

        job_id = jobs.submit("compare", {}, work)
        job = _wait_for_terminal(job_id)

        assert job["result_payload"] == {"entries": [{"strategy": "x", "net_pnl": "12.50"}]}

    def test_request_body_is_persisted_and_readable(self):
        job_id = jobs.submit("backtest", {"strategy_name": "vwap_reversion", "dataset": "d.csv"}, lambda p: (None, None))
        _wait_for_terminal(job_id)
        job = jobs.get_job(job_id)
        assert job["request"] == {"strategy_name": "vwap_reversion", "dataset": "d.csv"}


class TestProgress:
    def test_progress_updates_are_visible_before_completion(self):
        import threading

        started = threading.Event()
        proceed = threading.Event()

        def work(progress):
            progress.update(5, 10, message="halfway")
            started.set()
            proceed.wait(timeout=2)
            return None, None

        job_id = jobs.submit("backtest", {}, work)
        started.wait(timeout=2)
        job = jobs.get_job(job_id)
        assert job["progress_current"] == 5
        assert job["progress_total"] == 10
        assert job["progress_message"] == "halfway"

        proceed.set()
        _wait_for_terminal(job_id)


class TestFailure:
    def test_exception_in_work_marks_job_failed_not_raised(self):
        def work(progress):
            raise ValueError("simulated failure")

        job_id = jobs.submit("backtest", {}, work)
        job = _wait_for_terminal(job_id)

        assert job["status"] == "failed"
        assert "simulated failure" in job["error_message"]

    def test_failed_job_has_no_result(self):
        def work(progress):
            raise RuntimeError("boom")

        job_id = jobs.submit("backtest", {}, work)
        job = _wait_for_terminal(job_id)

        assert job["result_id"] is None


class TestListAndFetch:
    def test_list_jobs_includes_submitted_job(self):
        job_id = jobs.submit("backtest", {}, lambda p: (None, None))
        _wait_for_terminal(job_id)
        all_jobs = jobs.list_jobs()
        assert any(j["id"] == job_id for j in all_jobs)

    def test_list_jobs_filters_by_status(self):
        import threading

        gate = threading.Event()
        blocked_id = jobs.submit("backtest", {}, lambda p: (gate.wait(timeout=2), None, None)[1:])
        done_id = jobs.submit("backtest", {}, lambda p: (None, None))
        _wait_for_terminal(done_id)

        completed = jobs.list_jobs(status="completed")
        assert any(j["id"] == done_id for j in completed)
        assert all(j["id"] != blocked_id for j in completed)

        gate.set()
        _wait_for_terminal(blocked_id)

    def test_fetch_unknown_job_returns_none(self):
        assert jobs.get_job("does-not-exist") is None
