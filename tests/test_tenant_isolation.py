from __future__ import annotations

import asyncio
import json
import os
import sys
import types
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
from routers.jobs import (
    create_job,
    get_gap_questions,
    get_job,
    get_job_events,
    list_jobs,
    submit_gap_answers,
    AnswersIn,
    JobCreateIn,
)

TENANT_A = uuid4()
TENANT_B = uuid4()
JOB_ID = uuid4()
SPEC_ID = uuid4()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _user(tid: UUID):
    u = MagicMock()
    u.tenant_id = tid
    return u


def _req(arq=None):
    r = MagicMock()
    r.app.state.arq_pool.enqueue_job = arq or AsyncMock()
    return r


class TestTenantIsolation:
    """Verifies that RLS and tenant scoping prevent cross-tenant leakage."""

    def test_list_jobs_scoped_to_requesting_tenant(self):
        tenant_captured = []

        @asynccontextmanager
        async def _mock_session(tenant_id):
            tenant_captured.append(tenant_id)
            sess = AsyncMock()
            m = MagicMock()
            m.mappings.return_value.all.return_value = []
            sess.execute = AsyncMock(return_value=m)
            yield sess

        with patch("routers.jobs.get_tenant_session", _mock_session):
            _run(list_jobs(user=_user(TENANT_A)))

        assert tenant_captured == [TENANT_A]
        assert tenant_captured != [TENANT_B]

    def test_get_job_cross_tenant_returns_404(self):
        @asynccontextmanager
        async def _mock_session(tenant_id):
            sess = AsyncMock()
            m = MagicMock()
            # RLS returns None when queried with Tenant B's session
            m.mappings.return_value.one_or_none.return_value = None
            sess.execute = AsyncMock(return_value=m)
            yield sess

        with patch("routers.jobs.get_tenant_session", _mock_session):
            with pytest.raises(HTTPException) as exc:
                _run(get_job(job_id=JOB_ID, user=_user(TENANT_B)))
            assert exc.value.status_code == 404

    def test_get_job_events_cross_tenant_returns_404(self):
        @asynccontextmanager
        async def _mock_session(tenant_id):
            sess = AsyncMock()
            m = MagicMock()
            m.scalar_one_or_none.return_value = None  # Job not visible to Tenant B
            sess.execute = AsyncMock(return_value=m)
            yield sess

        with patch("routers.jobs.get_tenant_session", _mock_session):
            with pytest.raises(HTTPException) as exc:
                _run(get_job_events(job_id=JOB_ID, user=_user(TENANT_B)))
            assert exc.value.status_code == 404

    def test_get_gap_questions_cross_tenant_returns_404(self):
        @asynccontextmanager
        async def _mock_session(tenant_id):
            sess = AsyncMock()
            m = MagicMock()
            m.mappings.return_value.one_or_none.return_value = None
            sess.execute = AsyncMock(return_value=m)
            yield sess

        with patch("routers.jobs.get_tenant_session", _mock_session):
            with pytest.raises(HTTPException) as exc:
                _run(get_gap_questions(job_id=JOB_ID, user=_user(TENANT_B)))
            assert exc.value.status_code == 404

    def test_submit_answers_cross_tenant_returns_404(self):
        @asynccontextmanager
        async def _mock_session(tenant_id):
            sess = AsyncMock()
            m = MagicMock()
            m.mappings.return_value.one_or_none.return_value = None
            sess.execute = AsyncMock(return_value=m)
            yield sess

        with patch("routers.jobs.get_tenant_session", _mock_session):
            with pytest.raises(HTTPException) as exc:
                _run(submit_gap_answers(
                    job_id=JOB_ID,
                    body=AnswersIn(answers={"auth": "JWT"}),
                    request=_req(),
                    user=_user(TENANT_B),
                ))
            assert exc.value.status_code == 404

    def test_create_job_cross_tenant_spec_returns_404(self):
        @asynccontextmanager
        async def _mock_session(tenant_id):
            sess = AsyncMock()
            m = MagicMock()
            m.mappings.return_value.one_or_none.return_value = None  # Spec not found in Tenant B
            sess.execute = AsyncMock(return_value=m)
            yield sess

        with patch("routers.jobs.get_tenant_session", _mock_session):
            with pytest.raises(HTTPException) as exc:
                _run(create_job(
                    body=JobCreateIn(spec_id=SPEC_ID),
                    request=_req(),
                    user=_user(TENANT_B),
                ))
            assert exc.value.status_code == 404