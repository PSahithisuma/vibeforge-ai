from __future__ import annotations

import asyncio
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

for _n in ["core.config", "core.auth", "core.database",
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
from routers.budget import (
    get_budget_summary,
    get_budget_history,
    set_budget_limit,
    SetBudgetLimitIn,
)

TENANT_A = uuid4()
TENANT_B = uuid4()


def _run(coro):
    return asyncio.run(coro)


def _user(tid: UUID):
    u = MagicMock()
    u.tenant_id = tid
    return u


def _patch_session(*rows):
    row_idx = [0]

    @asynccontextmanager
    async def _ctx(tenant_id):
        sess = AsyncMock()

        async def _mock_execute(stmt, params=None):
            i = row_idx[0]
            row_idx[0] += 1
            m = MagicMock()
            row = rows[i] if i < len(rows) else None
            m.mappings.return_value.one_or_none.return_value = row
            m.mappings.return_value.all.return_value = row if isinstance(row, list) else []
            return m

        sess.execute = _mock_execute
        yield sess

    return _ctx


class TestBudgetEndpoints:
    def test_get_summary_returns_valid_metrics(self):
        job_stats = {"total": 5}
        event_stats = {"esc_count": 1, "mem_count": 3}
        ctx = _patch_session(job_stats, event_stats)

        with patch("routers.budget.get_tenant_session", ctx):
            summary = _run(get_budget_summary(tenant_id=TENANT_A, user=_user(TENANT_A)))

        assert summary.tenant_id == TENANT_A
        assert summary.total_jobs == 5
        assert summary.commercial_escalations_count == 1
        assert summary.escalations_avoided_via_memory == 3
        assert summary.cost_saved_usd == 1.50
        assert summary.budget_remaining_usd > 0

    def test_get_summary_tenant_mismatch_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            _run(get_budget_summary(tenant_id=TENANT_A, user=_user(TENANT_B)))
        assert exc.value.status_code == 403

    def test_get_history_returns_spend_records(self):
        events = [
            {"id": uuid4(), "job_id": uuid4(), "event_type": "escalation", "created_at": "2026-08-25T10:00:00"},
            {"id": uuid4(), "job_id": uuid4(), "event_type": "planning", "created_at": "2026-08-25T09:00:00"},
        ]
        ctx = _patch_session(events)

        with patch("routers.budget.get_tenant_session", ctx):
            history = _run(get_budget_history(tenant_id=TENANT_A, limit=10, user=_user(TENANT_A)))

        assert history.tenant_id == TENANT_A
        assert len(history.records) == 2
        assert history.records[0].phase == "escalation"
        assert history.records[0].model == "claude-3-5-sonnet"

    def test_get_history_tenant_mismatch_raises_403(self):
        with pytest.raises(HTTPException) as exc:
            _run(get_budget_history(tenant_id=TENANT_A, user=_user(TENANT_B)))
        assert exc.value.status_code == 403

    def test_set_limit_updates_successfully(self):
        body = SetBudgetLimitIn(budget_limit_usd=250.0)
        res = _run(set_budget_limit(tenant_id=TENANT_A, body=body, user=_user(TENANT_A)))
        assert res.budget_limit_usd == 250.0
        assert res.tenant_id == TENANT_A

    def test_set_limit_negative_raises_400(self):
        body = SetBudgetLimitIn(budget_limit_usd=-50.0)
        with pytest.raises(HTTPException) as exc:
            _run(set_budget_limit(tenant_id=TENANT_A, body=body, user=_user(TENANT_A)))
        assert exc.value.status_code == 400