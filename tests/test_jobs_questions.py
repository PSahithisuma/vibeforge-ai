from __future__ import annotations

import sys
import os
import types
import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

# ── sys.path + module stubs (before any routers.* import) ─────────────────────
_API_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "services", "api")
)
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

for _n in ["core", "core.config", "core.auth", "core.database",
           "core.metrics", "core.redis_client"]:
    if _n not in sys.modules:
        sys.modules[_n] = types.ModuleType(_n)

sys.modules["core.config"].get_settings = lambda: MagicMock(
    DATABASE_URL="postgresql+asyncpg://u:p@localhost/test",
    TENANT_SETTING_KEY="app.tenant_id",
    API_ENV="test",
)
sys.modules["core.auth"].AuthUser = type("AuthUser", (), {})
sys.modules["core.auth"].get_current_user = lambda: None
sys.modules["core.database"].get_tenant_session = None  # replaced per test

import pytest
from fastapi import HTTPException
from routers.jobs import get_gap_questions, submit_gap_answers  # type: ignore

# ── Constants ─────────────────────────────────────────────────────────────────
JOB_ID  = uuid4()
SPEC_ID = uuid4()
TID     = uuid4()

_ALL_JAVA = {
    "auth": "JWT", "database": "postgres",
    "file_storage": "no", "pagination": "yes",
    "build_tool": "Maven", "spring_security": "yes",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)

def _user():
    u = MagicMock()
    u.tenant_id = TID
    return u

def _make_result(row):
    m = MagicMock()
    m.mappings.return_value.one_or_none.return_value = row
    return m

def _patch_session(*rows):
    """One dict-or-None per get_tenant_session() call in the handler."""
    results = [_make_result(r) for r in rows]
    idx = [0]

    @asynccontextmanager
    async def _ctx(tenant_id):
        i = idx[0]
        idx[0] += 1
        sess = AsyncMock()
        sess.execute = AsyncMock(
            return_value=results[i] if i < len(results) else _make_result(None)
        )
        yield sess

    return _ctx

def _paused_row():
    return {"status": "paused_human", "spec_id": str(SPEC_ID), "error_message": None}

def _spec_row(spec=None):
    return {"compiled_spec": json.dumps(spec or {})}

def _req(arq=None):
    r = MagicMock()
    r.app.state.arq_pool.enqueue_job = arq or AsyncMock()
    return r


# ═══════════════════════════════════════════════════════════════════════════
#  TestGetGapQuestions
# ═══════════════════════════════════════════════════════════════════════════
class TestGetGapQuestions:

    def _call(self, row):
        ctx = _patch_session(row)
        with patch("routers.jobs.get_tenant_session", ctx):
            return _run(get_gap_questions(job_id=JOB_ID, user=_user()))

    def test_404_when_job_not_found(self):
        with pytest.raises(HTTPException) as exc:
            self._call(None)
        assert exc.value.status_code == 404

    def test_409_when_status_running(self):
        with pytest.raises(HTTPException) as exc:
            self._call({"status": "running", "error_message": None})
        assert exc.value.status_code == 409

    def test_409_when_status_delivered(self):
        with pytest.raises(HTTPException) as exc:
            self._call({"status": "delivered", "error_message": None})
        assert exc.value.status_code == 409

    def test_409_when_status_failed(self):
        with pytest.raises(HTTPException) as exc:
            self._call({"status": "failed", "error_message": None})
        assert exc.value.status_code == 409

    def test_returns_questions_from_error_message(self):
        qs = ["What auth?", "Which DB?"]
        out = self._call({"status": "paused_human",
                          "error_message": json.dumps({"gap_questions": qs})})
        assert out.questions == qs

    def test_returns_empty_when_no_error_message(self):
        out = self._call({"status": "paused_human", "error_message": None})
        assert out.questions == []

    def test_returns_empty_on_invalid_json(self):
        out = self._call({"status": "paused_human", "error_message": "bad"})
        assert out.questions == []

    def test_returns_empty_when_key_missing(self):
        out = self._call({"status": "paused_human",
                          "error_message": json.dumps({"other": "x"})})
        assert out.questions == []

    def test_job_id_in_response(self):
        out = self._call({"status": "paused_human", "error_message": None})
        assert out.job_id == JOB_ID

    def test_status_in_response(self):
        out = self._call({"status": "paused_human", "error_message": None})
        assert out.status == "paused_human"

    def test_questions_is_list(self):
        out = self._call({"status": "paused_human", "error_message": None})
        assert isinstance(out.questions, list)

    def test_multiple_questions_preserved(self):
        qs = ["Q1", "Q2", "Q3"]
        out = self._call({"status": "paused_human",
                          "error_message": json.dumps({"gap_questions": qs})})
        assert len(out.questions) == 3


# ═══════════════════════════════════════════════════════════════════════════
#  TestSubmitGapAnswers
# ═══════════════════════════════════════════════════════════════════════════
class TestSubmitGapAnswers:

    def _complete(self, arq=None):
        """All 6 java_spring questions answered → gaps cleared → requeued."""
        arq_m = arq or AsyncMock()
        body = MagicMock(answers=dict(_ALL_JAVA))
        ctx = _patch_session(_paused_row(), _spec_row(), None)
        with patch("routers.jobs.get_tenant_session", ctx):
            out = _run(submit_gap_answers(
                job_id=JOB_ID, body=body, request=_req(arq_m), user=_user()
            ))
        return out, arq_m

    def _partial(self, arq=None):
        """Only auth answered → 5 gaps remain."""
        arq_m = arq or AsyncMock()
        body = MagicMock(answers={"auth": "JWT"})
        ctx = _patch_session(_paused_row(), _spec_row(), None)
        with patch("routers.jobs.get_tenant_session", ctx):
            out = _run(submit_gap_answers(
                job_id=JOB_ID, body=body, request=_req(arq_m), user=_user()
            ))
        return out, arq_m

    # Guards
    def test_404_when_job_not_found(self):
        ctx = _patch_session(None)
        with pytest.raises(HTTPException) as exc:
            with patch("routers.jobs.get_tenant_session", ctx):
                _run(submit_gap_answers(
                    job_id=JOB_ID, body=MagicMock(answers={}),
                    request=_req(), user=_user()
                ))
        assert exc.value.status_code == 404

    def test_409_when_not_paused(self):
        row = {"status": "running", "spec_id": str(SPEC_ID), "error_message": None}
        ctx = _patch_session(row)
        with pytest.raises(HTTPException) as exc:
            with patch("routers.jobs.get_tenant_session", ctx):
                _run(submit_gap_answers(
                    job_id=JOB_ID, body=MagicMock(answers={}),
                    request=_req(), user=_user()
                ))
        assert exc.value.status_code == 409

    # All gaps resolved
    def test_status_queued_when_complete(self):
        assert self._complete()[0].status == "queued"

    def test_requeued_true_when_complete(self):
        assert self._complete()[0].requeued is True

    def test_remaining_empty_when_complete(self):
        assert self._complete()[0].remaining_questions == []

    def test_arq_enqueue_called_when_complete(self):
        arq = AsyncMock()
        self._complete(arq=arq)
        arq.assert_called_once()

    def test_arq_enqueue_job_name(self):
        arq = AsyncMock()
        self._complete(arq=arq)
        assert arq.call_args[0][0] == "generate_spec"

    def test_job_id_in_complete_response(self):
        assert self._complete()[0].job_id == JOB_ID

    # Gaps remain
    def test_status_paused_when_gaps_remain(self):
        assert self._partial()[0].status == "paused_human"

    def test_requeued_false_when_gaps_remain(self):
        assert self._partial()[0].requeued is False

    def test_remaining_non_empty_when_gaps_remain(self):
        assert len(self._partial()[0].remaining_questions) > 0

    def test_arq_not_called_when_gaps_remain(self):
        arq = AsyncMock()
        self._partial(arq=arq)
        arq.assert_not_called()

    def test_remaining_questions_are_strings(self):
        out, _ = self._partial()
        assert all(isinstance(q, str) for q in out.remaining_questions)