from __future__ import annotations

import pytest
import asyncio
from agents.memory.escalation_memory import (
    EscalationMemory,
    ProblemSignature,
    ErrorClass,
    InMemoryEscalationStore,
)

TID_A = "00000000-0000-0000-0000-000000000001"
TID_B = "00000000-0000-0000-0000-000000000002"


def _run(coro):
    return asyncio.run(coro)


class TestEscalationMemory:
    def test_store_and_lookup_same_tenant(self):
        em = EscalationMemory()
        sig = ProblemSignature(
            error_class=ErrorClass.COMPILE_ERROR,
            stack_profile="java_spring",
            rule_id="NULL_DEREF",
            error_pattern="NullPointerException in OrderService",
            vertical="ecommerce",
        )
        _run(em.store_success(
            signature=sig,
            fixed_files={"src/OrderService.java": "if (order != null)"},
            tenant_id=TID_A,
            job_id="job-1", model_used="qwen3:8b",
        ))

        hits = _run(em.lookup(signature=sig, tenant_id=TID_A, min_similarity=0.8))
        assert len(hits) == 1
        assert hits[0].record.tenant_id == TID_A
        assert "src/OrderService.java" in hits[0].record.fixed_files

    def test_tenant_isolation_cross_tenant_lookup_empty(self):
        em = EscalationMemory()
        sig = ProblemSignature(
            error_class=ErrorClass.SEMGREP_VIOLATION,
            stack_profile="java_spring",
            rule_id="SQL_INJECTION",
            error_pattern="Unsanitized input in Repo",
            vertical="banking",
        )
        _run(em.store_success(
            signature=sig,
            fixed_files={"src/Repo.java": "sanitize(input)"},
            tenant_id=TID_A,
            job_id="job-2", model_used="qwen3:8b",
        ))

        # Tenant B queries identical problem -> must NOT see Tenant A's private fix
        hits = _run(em.lookup(signature=sig, tenant_id=TID_B, min_similarity=0.5))
        assert len(hits) == 0

    def test_auto_promotion_to_shared_after_3_reuses(self):
        em = EscalationMemory()
        sig = ProblemSignature(
            error_class=ErrorClass.TEST_FAILURE,
            stack_profile="java_spring",
            rule_id="ASSERT_FAILURE",
            error_pattern="AssertEquals failure in RouteTest",
            vertical="logistics",
        )
        rec = _run(em.store_success(
            signature=sig,
            fixed_files={"src/Route.java": "return true;"},
            tenant_id=TID_A,
            job_id="job-3", model_used="qwen3:8b",
        ))

        assert rec.shared is False
        assert rec.reuse_count == 0

        # Reuse 1 & 2
        em._store.increment_reuse(rec.record_id)
        em._store.increment_reuse(rec.record_id)
        assert rec.shared is False

        # Reuse 3 -> Auto-promoted to shared (Contract C14)
        promoted = em._store.increment_reuse(rec.record_id)
        assert promoted.shared is True
        assert promoted.promoted_at is not None

        # Now Tenant B CAN discover the shared fix
        hits = _run(em.lookup(signature=sig, tenant_id=TID_B, min_similarity=0.8))
        assert len(hits) == 1
        assert hits[0].is_shared is True