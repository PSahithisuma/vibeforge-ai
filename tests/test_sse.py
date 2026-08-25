from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from agents.graphs.generation_graph import JobEvent, JobStatus
from services.worker.tasks.generation import (
    _build_llm_client,
    _publish_event,
    _write_event,
    generate_spec,
)


def _normalise_payload(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"raw": raw}
    return {}


def _run(coro):
    return asyncio.run(coro)


def _evt(phase="planning", node="plan_node", **payload) -> JobEvent:
    return JobEvent({
        "event_id": str(uuid4()),
        "job_id":   str(uuid4()),
        "node":     node,
        "phase":    phase,
        "payload":  payload or {"status": "started"},
        "ts":       "2025-01-01T00:00:00+00:00",
    })


def _conn(**overrides):
    c = AsyncMock()
    c.execute  = AsyncMock()
    c.fetchrow = AsyncMock(return_value={
        "compiled_spec": json.dumps({"stack": {"backend": "java_spring"}})
    })
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _redis():
    r = AsyncMock()
    r.publish = AsyncMock()
    return r


def _final_state(status=JobStatus.DELIVERED, n_events=3):
    from agents.graphs.generation_graph import GenerationState
    s = GenerationState(
        job_id=str(uuid4()),
        job_status=status,
        preview_url="https://preview.vibeforge.io/t",
        gitea_repo_url="https://git.vibeforge.io/t/t",
    )
    phases = ["planning", "synthesizing", "done"]
    s.events = [_evt(phases[i % len(phases)]) for i in range(n_events)]
    return s


class TestWriteEvent:
    def test_uses_phase_as_event_type(self):
        c = _conn()
        _run(_write_event(c, "j", "t", _evt("gating")))
        _, *args = c.execute.call_args[0]
        assert args[3] == "gating"

    def test_writes_job_id(self):
        c = _conn()
        _run(_write_event(c, "job-99", "t", _evt()))
        _, *args = c.execute.call_args[0]
        assert args[1] == "job-99"

    def test_writes_tenant_id(self):
        c = _conn()
        _run(_write_event(c, "j", "tenant-42", _evt()))
        _, *args = c.execute.call_args[0]
        assert args[2] == "tenant-42"

    def test_payload_is_json_string(self):
        c = _conn()
        _run(_write_event(c, "j", "t", _evt("delivering", file_count=7)))
        _, *args = c.execute.call_args[0]
        assert json.loads(args[4])["file_count"] == 7

    def test_generates_event_id_when_blank(self):
        c = _conn()
        e = _evt()
        e.event_id = ""
        _run(_write_event(c, "j", "t", e))
        _, *args = c.execute.call_args[0]
        assert len(args[0]) == 36

    def test_sql_has_on_conflict_do_nothing(self):
        c = _conn()
        _run(_write_event(c, "j", "t", _evt()))
        sql = c.execute.call_args[0][0]
        assert "ON CONFLICT" in sql and "DO NOTHING" in sql


class TestPublishEvent:
    def test_publishes_to_correct_channel(self):
        r = _redis()
        _run(_publish_event(r, "job-abc", _evt()))
        assert r.publish.call_args[0][0] == "job:job-abc"

    def test_event_type_is_phase_value(self):
        r = _redis()
        _run(_publish_event(r, "j", _evt("synthesizing")))
        assert json.loads(r.publish.call_args[0][1])["event_type"] == "synthesizing"

    def test_node_in_message(self):
        r = _redis()
        _run(_publish_event(r, "j", _evt("gating", "gate_node")))
        assert json.loads(r.publish.call_args[0][1])["node"] == "gate_node"

    def test_payload_in_message(self):
        r = _redis()
        _run(_publish_event(r, "j", _evt("delivering", file_count=5)))
        assert json.loads(r.publish.call_args[0][1])["payload"]["file_count"] == 5


class TestBuildLLMClient:
    def test_returns_none_when_url_not_set(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _build_llm_client() is None

    def test_returns_none_on_import_error(self):
        with patch.dict("os.environ", {"LITELLM_BASE_URL": "http://litellm:4000"}), \
             patch.dict("sys.modules", {"agents.llm.litellm_client": None}):
            assert _build_llm_client() is None


class TestGenerateSpec:

    def _go(self, conn, redis, final):
        async def run():
            with patch("asyncpg.connect", return_value=conn), \
                 patch("services.worker.tasks.generation.run_generation_job",
                       return_value=final):
                return await generate_spec(
                    {"redis": redis},
                    job_id="job-1", tenant_id="t-1", spec_id="spec-1",
                )
        return _run(run())

    def test_marks_job_running_at_start(self):
        c, r, f = _conn(), _redis(), _final_state()
        self._go(c, r, f)
        assert "status = 'running'" in c.execute.call_args_list[0][0][0]

    def test_loads_spec_by_spec_id(self):
        c, r, f = _conn(), _redis(), _final_state()
        self._go(c, r, f)
        assert "spec-1" in str(c.fetchrow.call_args)

    def test_raises_on_missing_spec(self):
        c = _conn()
        c.fetchrow = AsyncMock(return_value=None)
        async def run():
            with patch("asyncpg.connect", return_value=c):
                await generate_spec({"redis": None},
                                    job_id="j", tenant_id="t", spec_id="x")
        with pytest.raises(ValueError, match="not found"):
            _run(run())

    def test_inserts_all_events_to_postgres(self):
        c, r, f = _conn(), _redis(), _final_state(n_events=4)
        self._go(c, r, f)
        inserts = [x for x in c.execute.call_args_list
                   if "INSERT INTO job_events" in x[0][0]]
        assert len(inserts) == 4

    def test_publishes_all_events_plus_terminal(self):
        c, r, f = _conn(), _redis(), _final_state(n_events=3)
        self._go(c, r, f)
        assert r.publish.call_count == 4

    def test_terminal_complete_on_delivery(self):
        c, r, f = _conn(), _redis(), _final_state(JobStatus.DELIVERED)
        self._go(c, r, f)
        last = json.loads(r.publish.call_args_list[-1][0][1])
        assert last["event_type"] == "complete"

    def test_terminal_error_on_failure(self):
        c, r, f = _conn(), _redis(), _final_state(JobStatus.FAILED)
        self._go(c, r, f)
        last = json.loads(r.publish.call_args_list[-1][0][1])
        assert last["event_type"] == "error"

    def test_updates_job_to_completed(self):
        c, r, f = _conn(), _redis(), _final_state(JobStatus.DELIVERED)
        self._go(c, r, f)
        update = [x for x in c.execute.call_args_list
                  if "UPDATE jobs" in x[0][0] and "completed_at" in x[0][0]]
        assert len(update) == 1 and update[0][0][1] == "completed"

    def test_updates_job_to_failed_on_exception(self):
        c = _conn()
        c.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
        async def run():
            with patch("asyncpg.connect", return_value=c):
                await generate_spec({"redis": None},
                                    job_id="j", tenant_id="t", spec_id="s")
        with pytest.raises(RuntimeError):
            _run(run())
        failed = [x for x in c.execute.call_args_list if len(x[0]) > 0 and 'failed' in str(x[0][0])]
        assert len(failed) == 1

    def test_works_without_redis(self):
        c, f = _conn(), _final_state()
        async def run():
            with patch("asyncpg.connect", return_value=c), \
                 patch("services.worker.tasks.generation.run_generation_job",
                       return_value=f):
                await generate_spec({"redis": None},
                                    job_id="j", tenant_id="t", spec_id="s")
        _run(run())


class TestNormalisePayload:
    def test_dict_passes_through(self):
        assert _normalise_payload({"k": "v"}) == {"k": "v"}

    def test_json_string_parsed(self):
        assert _normalise_payload('{"a": 1}') == {"a": 1}

    def test_invalid_string_returns_wrapper(self):
        assert "raw" in _normalise_payload("not-json")

    def test_none_returns_empty_dict(self):
        assert _normalise_payload(None) == {}

    def test_nested_structure_preserved(self):
        raw = {"outer": {"inner": [1, 2]}}
        assert _normalise_payload(raw) == raw