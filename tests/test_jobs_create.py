from __future__ import annotations

import sys
import os
import types
import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

# ── Module stubs before router imports ────────────────────────────────────────
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
sys.modules["core.database"].get_tenant_session = None

import pytest
from fastapi import HTTPException
from routers.jobs import create_job, JobCreateIn

SPEC_ID = uuid4()
TID = uuid4()

_FULL_SPEC = {
    "security": "jwt",
    "database": "postgresql",
    "storage": "s3",
    "pagination": "cursor",
    "build": "maven",
    "spring_security": "yes",
}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _user():
    u = MagicMock()
    u.tenant_id = TID
    return u


def _req(arq=None):
    r = MagicMock()
    r.app.state.arq_pool.enqueue_job = arq or AsyncMock()
    return r


def _patch_session(*rows):
    idx = [0]

    @asynccontextmanager
    async def _ctx(tenant_id):
        i = idx[0]
        idx[0] += 1
        sess = AsyncMock()
        m = MagicMock()
        row = rows[i] if i < len(rows) else None
        m.mappings.return_value.one_or_none.return_value = row
        sess.execute = AsyncMock(return_value=m)
        yield sess

    return _ctx


class TestCreateJob:
    def test_404_when_spec_not_found(self):
        ctx = _patch_session(None)
        body = JobCreateIn(spec_id=SPEC_ID)
        with pytest.raises(HTTPException) as exc:
            with patch("routers.jobs.get_tenant_session", ctx):
                _run(create_job(body=body, request=_req(), user=_user()))
        assert exc.value.status_code == 404

    def test_pauses_job_when_spec_has_gaps(self):
        # Empty spec -> has gaps
        spec_row = {"compiled_spec": json.dumps({})}
        ctx = _patch_session(spec_row, None)
        body = JobCreateIn(spec_id=SPEC_ID)
        arq = AsyncMock()
        with patch("routers.jobs.get_tenant_session", ctx):
            res = _run(create_job(body=body, request=_req(arq), user=_user()))

        assert res.status == "paused_human"
        assert res.requeued is False
        assert len(res.gap_questions) > 0
        arq.assert_not_called()

    def test_queues_job_when_spec_is_complete(self):
        spec_row = {"compiled_spec": json.dumps(_FULL_SPEC)}
        ctx = _patch_session(spec_row, None)
        body = JobCreateIn(spec_id=SPEC_ID)
        arq = AsyncMock()
        with patch("routers.jobs.get_tenant_session", ctx):
            res = _run(create_job(body=body, request=_req(arq), user=_user()))

        assert res.status == "queued"
        assert res.requeued is True
        assert res.gap_questions == []
        arq.assert_called_once()
        assert arq.call_args[0][0] == "generate_spec"

    def test_initial_answers_resolve_gaps(self):
        spec_row = {"compiled_spec": json.dumps({})}
        ctx = _patch_session(spec_row, None)
        answers = {
            "auth": "JWT",
            "database": "postgres",
            "file_storage": "no",
            "pagination": "yes",
            "build_tool": "Maven",
            "spring_security": "yes",
        }
        body = JobCreateIn(spec_id=SPEC_ID, initial_answers=answers)
        arq = AsyncMock()
        with patch("routers.jobs.get_tenant_session", ctx):
            res = _run(create_job(body=body, request=_req(arq), user=_user()))

        assert res.status == "queued"
        assert res.requeued is True
        assert res.gap_questions == []
        arq.assert_called_once()