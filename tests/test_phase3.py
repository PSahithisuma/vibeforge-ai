"""
Phase 3 test suite — Escalation Gate, Escalation Memory, Semantic Cache.
All external dependencies (Qdrant, commercial APIs) are mocked.
Run: python -m pytest tests/test_phase3.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).parent.parent
for p in [ROOT, ROOT / "agents", ROOT / "core"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agents.memory.escalation_gate import (
    EscalationGate, EscalationContext, EscalationVerdict,
    PIIScanner, PromptCompressor, CommercialModelAdapter,
)
from agents.memory.escalation_memory import (
    EscalationMemory, ProblemSignature, ErrorClass,
    InMemoryEscalationStore, MemoryRecord,
)
from agents.cache.semantic_cache import (
    SemanticCache, CacheKey, CacheEntry, CacheHit,
    InMemoryCacheStore,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_ctx(**kwargs) -> EscalationContext:
    defaults = dict(
        job_id=str(uuid4()),
        tenant_id=str(uuid4()),
        fix_iteration=3,
        max_fix_iterations=3,
        tenant_escalation_enabled=True,
        tenant_external_llm_consent=True,
        budget_remaining=100.0,
        escalation_cost_estimate=5.0,
        escalation_used=False,
        failing_files=["src/OrderService.java"],
        errors_by_file={"src/OrderService.java": ["error: cannot find symbol 'OrderRepository'"]},
        assembled_files={"src/OrderService.java": "class OrderService { }"},
        stack_profile="java_spring",
        vertical="ecommerce",
    )
    defaults.update(kwargs)
    return EscalationContext(**defaults)


def make_mock_commercial(response: dict):
    """Mock CommercialModelAdapter that returns a fixed response."""
    class MockAdapter(CommercialModelAdapter):
        def __init__(self):
            self._model = "claude-opus-4-6"
            self._max_tokens = 8192
            self._client = None
        async def call(self, prompt, job_id=""):
            return json.dumps(response), 1500, 0.02
        def parse_fixed_files(self, response_str):
            try:
                return json.loads(response_str)
            except Exception:
                return {}
    return MockAdapter()


# ══════════════════════════════════════════════════════════════════════════════
# PII SCANNER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPIIScanner:

    def setup_method(self):
        self.scanner = PIIScanner()

    def test_clean_code_passes(self):
        code = """
public class OrderService {
    private final OrderRepository repo;
    public Order findById(UUID id) { return repo.findById(id).orElseThrow(); }
}
"""
        passed, findings = self.scanner.scan(code)
        assert passed, f"Expected pass but found: {findings}"

    def test_detects_email(self):
        text = "Customer email: john.doe@company.com — send invoice here"
        passed, findings = self.scanner.scan(text)
        assert not passed
        assert any("email" in f for f in findings)

    def test_detects_aws_key(self):
        text = "String awsKey = \"AKIAIOSFODNN7EXAMPLE\";"
        passed, findings = self.scanner.scan(text)
        assert not passed
        assert any("aws_key" in f for f in findings)

    def test_detects_private_key_header(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
        passed, findings = self.scanner.scan(text)
        assert not passed
        assert any("private_key" in f for f in findings)

    def test_detects_db_password(self):
        text = "password = 'supersecretpassword123'"
        passed, findings = self.scanner.scan(text)
        assert not passed

    def test_allowlist_test_email(self):
        text = "// Example: test@example.com — replace with real email"
        passed, findings = self.scanner.scan(text)
        assert passed, f"test@example.com should be allowlisted: {findings}"

    def test_allowlist_env_var_reference(self):
        text = "password = ${DB_PASSWORD}"
        passed, findings = self.scanner.scan(text)
        assert passed, f"Env var reference should be allowlisted: {findings}"

    def test_detects_jwt_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.TJVA95OrM7E2cBab30RMHrHDcEfxjoYZgeFONFh7HgQ"
        passed, findings = self.scanner.scan(text)
        assert not passed
        assert any("jwt" in f for f in findings)


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT COMPRESSOR TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPromptCompressor:

    def setup_method(self):
        self.compressor = PromptCompressor()

    def test_produces_prompt_within_token_budget(self):
        errors = {"src/X.java": ["error: cannot find symbol"]}
        files = {"src/X.java": "class X { /* broken */ }"}
        prompt, tokens = self.compressor.compress(errors, files, "java_spring", "ecommerce")
        assert tokens <= self.compressor.MAX_TOKENS
        assert len(prompt) <= self.compressor.MAX_CHARS

    def test_prompt_contains_exact_errors(self):
        errors = {"src/X.java": ["SPECIFIC_ERROR_TOKEN_99999"]}
        files = {"src/X.java": "class X{}"}
        prompt, _ = self.compressor.compress(errors, files, "java_spring", "ecommerce")
        assert "SPECIFIC_ERROR_TOKEN_99999" in prompt

    def test_prompt_contains_stack_context(self):
        errors = {"src/X.java": ["error"]}
        files = {"src/X.java": "class X{}"}
        prompt, _ = self.compressor.compress(errors, files, "python_fastapi", "banking")
        assert "python_fastapi" in prompt
        assert "banking" in prompt

    def test_large_file_gets_truncated(self):
        large_content = "x" * (self.compressor.MAX_CHARS * 2)
        errors = {"src/Big.java": ["error"]}
        files = {"src/Big.java": large_content}
        prompt, tokens = self.compressor.compress(errors, files, "java_spring", "ecommerce")
        assert tokens <= self.compressor.MAX_TOKENS


# ══════════════════════════════════════════════════════════════════════════════
# ESCALATION GATE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestEscalationGate:

    def test_blocked_when_already_escalated(self):
        gate = EscalationGate()
        ctx = make_ctx(escalation_used=True)
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.verdict == EscalationVerdict.BLOCKED_LIMIT

    def test_blocked_before_3_iterations(self):
        gate = EscalationGate()
        ctx = make_ctx(fix_iteration=1)
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.verdict == EscalationVerdict.BLOCKED_ITER

    def test_blocked_without_tenant_consent(self):
        gate = EscalationGate()
        ctx = make_ctx(tenant_escalation_enabled=False)
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.verdict == EscalationVerdict.BLOCKED_CONSENT

    def test_blocked_without_external_llm_consent(self):
        gate = EscalationGate()
        ctx = make_ctx(tenant_external_llm_consent=False)
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.verdict == EscalationVerdict.BLOCKED_CONSENT

    def test_blocked_when_budget_insufficient(self):
        gate = EscalationGate()
        ctx = make_ctx(budget_remaining=2.0, escalation_cost_estimate=5.0)
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.verdict == EscalationVerdict.BLOCKED_BUDGET

    def test_blocked_when_pii_in_prompt(self):
        gate = EscalationGate()
        ctx = make_ctx(
            assembled_files={
                "src/OrderService.java": "// customer email: john@realdomain.com"
            }
        )
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.verdict == EscalationVerdict.BLOCKED_PII
        assert not result.pii_scan_passed

    def test_approved_when_all_conditions_met(self):
        fixed_response = {"src/OrderService.java": "class OrderService { /* fixed */ }"}
        adapter = make_mock_commercial(fixed_response)
        gate = EscalationGate(commercial_adapter=adapter)
        ctx = make_ctx()
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.verdict == EscalationVerdict.APPROVED
        assert result.approved
        assert result.pii_scan_passed
        assert "src/OrderService.java" in result.fixed_files

    def test_approved_result_has_cost_info(self):
        fixed_response = {"src/X.java": "class X{}"}
        adapter = make_mock_commercial(fixed_response)
        gate = EscalationGate(commercial_adapter=adapter)
        ctx = make_ctx()
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.cost_usd > 0
        assert result.tokens_used > 0
        assert result.model_used != ""

    def test_contract_c18_pii_scan_always_runs(self):
        """Contract C18: PII scan runs even when all other conditions pass."""
        scan_called = {"yes": False}
        class TrackingScanner(PIIScanner):
            def scan(self, text):
                scan_called["yes"] = True
                return True, []
        gate = EscalationGate(
            commercial_adapter=make_mock_commercial({"x.java": "class X{}"}),
            pii_scanner=TrackingScanner(),
        )
        ctx = make_ctx()
        asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert scan_called["yes"], "Contract C18: PII scan must always run before commercial call"

    def test_contract_c6_output_not_auto_trusted(self):
        """
        Contract C6: EscalationGate returns fixed_files to the caller.
        The caller is responsible for re-entering the sandbox gate.
        The gate itself does NOT mark the job as complete.
        """
        adapter = make_mock_commercial({"src/X.java": "class X{}"})
        gate = EscalationGate(commercial_adapter=adapter)
        ctx = make_ctx()
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        # The result contains fixed_files but NOT a gate_passed flag
        # The caller must run the sandbox gate on these files
        assert hasattr(result, "fixed_files")
        assert not hasattr(result, "gate_passed"), \
            "Contract C6: EscalationGate must not mark output as gate-passed"


# ══════════════════════════════════════════════════════════════════════════════
# ESCALATION MEMORY TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestEscalationMemory:

    def setup_method(self):
        self.store = InMemoryEscalationStore()
        self.memory = EscalationMemory(store=self.store)
        self.tenant_id = str(uuid4())

    def _sig(self, error_class=ErrorClass.COMPILE_ERROR) -> ProblemSignature:
        return ProblemSignature(
            error_class=error_class,
            stack_profile="java_spring",
            rule_id="",
            error_pattern="cannot find symbol X",
            module_type="service",
            vertical="ecommerce",
        )

    def test_store_and_lookup(self):
        sig = self._sig()
        asyncio.get_event_loop().run_until_complete(
            self.memory.store_success(
                signature=sig,
                fixed_files={"src/X.java": "class X { /* fixed */ }"},
                tenant_id=self.tenant_id,
                job_id=str(uuid4()),
                model_used="claude-opus-4-6",
            )
        )
        hits = asyncio.get_event_loop().run_until_complete(
            self.memory.lookup(sig, self.tenant_id)
        )
        assert len(hits) >= 1
        assert hits[0].record.model_used == "claude-opus-4-6"

    def test_no_hit_for_different_tenant(self):
        sig = self._sig()
        other_tenant = str(uuid4())
        asyncio.get_event_loop().run_until_complete(
            self.memory.store_success(
                signature=sig,
                fixed_files={"src/X.java": "class X{}"},
                tenant_id=other_tenant,
                job_id=str(uuid4()),
                model_used="claude-opus-4-6",
            )
        )
        # Different tenant should not see it (not shared yet)
        hits = asyncio.get_event_loop().run_until_complete(
            self.memory.lookup(sig, self.tenant_id)
        )
        assert len(hits) == 0

    def test_auto_promote_at_3_reuses(self):
        """Contract C14: auto-promote to shared after 3 reuses."""
        sig = self._sig()
        asyncio.get_event_loop().run_until_complete(
            self.memory.store_success(
                signature=sig,
                fixed_files={"src/X.java": "class X{}"},
                tenant_id=self.tenant_id,
                job_id=str(uuid4()),
                model_used="claude-opus-4-6",
            )
        )
        records = self.store.get_all(self.tenant_id)
        record_id = records[0].record_id

        # 3 reuses → auto-promote
        for _ in range(3):
            asyncio.get_event_loop().run_until_complete(
                self.memory.record_reuse(record_id)
            )

        records = self.store.get_all(self.tenant_id)
        assert records[0].shared is True
        assert records[0].reuse_count == 3
        assert records[0].promoted_at is not None

    def test_shared_record_visible_to_other_tenants(self):
        """After promotion, other tenants can see it."""
        sig = self._sig()
        other_tenant = str(uuid4())
        asyncio.get_event_loop().run_until_complete(
            self.memory.store_success(
                signature=sig,
                fixed_files={"src/X.java": "class X{}"},
                tenant_id=other_tenant,
                job_id=str(uuid4()),
                model_used="claude-opus-4-6",
            )
        )
        # Manually promote
        records = self.store.get_all(other_tenant)
        for _ in range(3):
            self.store.increment_reuse(records[0].record_id)

        # Now visible to our tenant
        hits = asyncio.get_event_loop().run_until_complete(
            self.memory.lookup(sig, self.tenant_id)
        )
        assert len(hits) >= 1

    def test_problem_signature_fingerprint_is_deterministic(self):
        sig1 = self._sig()
        sig2 = self._sig()
        assert sig1.fingerprint() == sig2.fingerprint()

    def test_different_signatures_have_different_fingerprints(self):
        sig1 = self._sig(ErrorClass.COMPILE_ERROR)
        sig2 = self._sig(ErrorClass.TEST_FAILURE)
        assert sig1.fingerprint() != sig2.fingerprint()

    def test_signature_from_gate_errors(self):
        sig = ProblemSignature.from_gate_errors(
            failing_step="compile",
            error_lines=["OrderService.java:45: error: cannot find symbol 'OrderRepository'"],
            stack_profile="java_spring",
            module_type="service",
            vertical="ecommerce",
        )
        assert sig.error_class == ErrorClass.COMPILE_ERROR
        assert sig.stack_profile == "java_spring"
        assert sig.error_pattern != ""

    def test_lookup_returns_empty_when_no_match(self):
        sig = self._sig()
        hits = asyncio.get_event_loop().run_until_complete(
            self.memory.lookup(sig, self.tenant_id)
        )
        assert hits == []


# ══════════════════════════════════════════════════════════════════════════════
# SEMANTIC CACHE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSemanticCache:

    def setup_method(self):
        self.cache = SemanticCache(store=InMemoryCacheStore())
        self.tenant_id = str(uuid4())
        self.job_id = str(uuid4())

    def _key(self, hash_suffix="abc123") -> CacheKey:
        return CacheKey(
            canonical_hash=f"hash_{hash_suffix}" + "0" * 50,
            stack_profile="java_spring",
            scaffold_version="1.0.0",
            ruleset_version="1.0.0",
        )

    def test_miss_on_empty_cache(self):
        key = self._key()
        hit = asyncio.get_event_loop().run_until_complete(
            self.cache.lookup(key, self.tenant_id)
        )
        assert hit is None

    def test_exact_hit_after_write(self):
        key = self._key()
        asyncio.get_event_loop().run_until_complete(
            self.cache.write(
                key=key,
                artifact_bundle_url="s3://bucket/bundle.zip",
                gitea_repo_url="https://git.vibeforge.io/test/test",
                spec_summary="E-commerce with 5 entities",
                job_id=self.job_id,
                tenant_id=self.tenant_id,
                vertical="ecommerce",
                entity_count=5,
            )
        )
        hit = asyncio.get_event_loop().run_until_complete(
            self.cache.lookup(key, self.tenant_id)
        )
        assert hit is not None
        assert hit.is_exact
        assert hit.similarity_score == 1.0
        assert hit.entry.artifact_bundle_url == "s3://bucket/bundle.zip"

    def test_different_stack_is_cache_miss(self):
        key1 = self._key()
        asyncio.get_event_loop().run_until_complete(
            self.cache.write(
                key=key1,
                artifact_bundle_url="s3://bundle1.zip",
                gitea_repo_url="https://git.test",
                spec_summary="Test spec",
                job_id=self.job_id,
                tenant_id=self.tenant_id,
            )
        )
        # Same spec hash, different stack = different cache key
        key2 = CacheKey(
            canonical_hash=key1.canonical_hash,
            stack_profile="python_fastapi",   # different
            scaffold_version="1.0.0",
            ruleset_version="1.0.0",
        )
        hit = asyncio.get_event_loop().run_until_complete(
            self.cache.lookup(key2, self.tenant_id)
        )
        assert hit is None

    def test_different_scaffold_version_is_cache_miss(self):
        key1 = self._key()
        asyncio.get_event_loop().run_until_complete(
            self.cache.write(
                key=key1,
                artifact_bundle_url="s3://bundle1.zip",
                gitea_repo_url="https://git.test",
                spec_summary="Test spec",
                job_id=self.job_id,
                tenant_id=self.tenant_id,
            )
        )
        key2 = CacheKey(
            canonical_hash=key1.canonical_hash,
            stack_profile="java_spring",
            scaffold_version="2.0.0",   # different scaffold version
            ruleset_version="1.0.0",
        )
        hit = asyncio.get_event_loop().run_until_complete(
            self.cache.lookup(key2, self.tenant_id)
        )
        assert hit is None

    def test_cache_key_is_deterministic(self):
        key1 = self._key("xyz")
        key2 = self._key("xyz")
        assert key1.compute() == key2.compute()

    def test_cache_key_changes_with_version(self):
        key1 = CacheKey("hash" + "0" * 60, "java_spring", "1.0.0", "1.0.0")
        key2 = CacheKey("hash" + "0" * 60, "java_spring", "2.0.0", "1.0.0")
        assert key1.compute() != key2.compute()

    def test_contract_c7_near_hit_not_auto_substituted(self):
        """Contract C7: near-hits are suggested, never auto-substituted."""
        hit = CacheHit(
            entry=CacheEntry(
                artifact_bundle_url="s3://cached.zip",
                spec_summary="Previous similar spec",
            ),
            hit_type="near",
            similarity_score=0.94,
        )
        # The cache returns a near hit — but the API only suggests it
        suggestion = self.cache.format_near_hit_suggestion(hit)
        assert suggestion["type"] == "near_cache_hit"
        assert "user_choices" in suggestion
        assert len(suggestion["user_choices"]) >= 2
        # Must include a "generate fresh" option — user can always decline
        assert any("fresh" in c.lower() or "generate" in c.lower()
                   for c in suggestion["user_choices"])

    def test_invalidate_removes_entry(self):
        key = self._key()
        asyncio.get_event_loop().run_until_complete(
            self.cache.write(
                key=key,
                artifact_bundle_url="s3://bundle.zip",
                gitea_repo_url="https://git.test",
                spec_summary="Test",
                job_id=self.job_id,
                tenant_id=self.tenant_id,
            )
        )
        removed = asyncio.get_event_loop().run_until_complete(
            self.cache.invalidate(key)
        )
        assert removed is True
        hit = asyncio.get_event_loop().run_until_complete(
            self.cache.lookup(key, self.tenant_id)
        )
        assert hit is None

    def test_stats(self):
        key = self._key()
        asyncio.get_event_loop().run_until_complete(
            self.cache.write(
                key=key,
                artifact_bundle_url="s3://bundle.zip",
                gitea_repo_url="",
                spec_summary="Test",
                job_id=self.job_id,
                tenant_id=self.tenant_id,
            )
        )
        stats = self.cache.stats()
        assert stats["total_entries"] >= 1


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — all 3 Phase 3 components together
# ══════════════════════════════════════════════════════════════════════════════

class TestPhase3Integration:

    def test_memory_checked_before_escalation_gate(self):
        """
        When a memory hit exists, it should be used instead of calling
        the commercial model. This is the cost-saving cascade.
        """
        store = InMemoryEscalationStore()
        memory = EscalationMemory(store=store)
        tenant_id = str(uuid4())

        sig = ProblemSignature(
            error_class=ErrorClass.COMPILE_ERROR,
            stack_profile="java_spring",
            error_pattern="cannot find symbol",
            module_type="service",
            vertical="ecommerce",
        )

        # Pre-store a successful fix
        asyncio.get_event_loop().run_until_complete(
            memory.store_success(
                signature=sig,
                fixed_files={"src/OrderService.java": "class OrderService { /* from memory */ }"},
                tenant_id=tenant_id,
                job_id=str(uuid4()),
                model_used="claude-opus-4-6",
            )
        )

        # Lookup should return the cached fix
        hits = asyncio.get_event_loop().run_until_complete(
            memory.lookup(sig, tenant_id)
        )
        assert len(hits) >= 1
        assert "from memory" in hits[0].record.fixed_files.get("src/OrderService.java", "")

    def test_cache_hit_prevents_generation(self):
        """Exact cache hit means generation is skipped entirely."""
        cache = SemanticCache(store=InMemoryCacheStore())
        tenant_id = str(uuid4())

        key = SemanticCache.build_key(
            canonical_hash="a" * 64,
            stack_profile="java_spring",
        )

        # Write to cache
        asyncio.get_event_loop().run_until_complete(
            cache.write(
                key=key,
                artifact_bundle_url="s3://cached_bundle.zip",
                gitea_repo_url="https://git.vibeforge.io/cached",
                spec_summary="Cached e-commerce app",
                job_id=str(uuid4()),
                tenant_id=tenant_id,
                vertical="ecommerce",
            )
        )

        # Second job with same spec → should hit cache
        hit = asyncio.get_event_loop().run_until_complete(
            cache.lookup(key, tenant_id)
        )
        assert hit is not None
        assert hit.is_exact
        assert hit.entry.artifact_bundle_url == "s3://cached_bundle.zip"
        # In production: return this URL, skip generation entirely

    def test_full_cost_reduction_cascade(self):
        """
        Demonstrates the 3-level cost reduction cascade:
        Level 1: exact cache hit → skip generation
        Level 2: memory hit → skip commercial call
        Level 3: escalation gate → commercial call as last resort
        """
        cache = SemanticCache(store=InMemoryCacheStore())
        memory = EscalationMemory(store=InMemoryEscalationStore())
        gate = EscalationGate(
            commercial_adapter=make_mock_commercial({"src/X.java": "class X{}"})
        )
        tenant_id = str(uuid4())
        key = SemanticCache.build_key("b" * 64, "java_spring")

        # Level 1: cache miss (no cached result yet)
        hit = asyncio.get_event_loop().run_until_complete(cache.lookup(key, tenant_id))
        assert hit is None  # miss → proceed to generation

        # Level 2: memory miss (no similar failure seen before)
        sig = ProblemSignature(ErrorClass.COMPILE_ERROR, "java_spring")
        mem_hits = asyncio.get_event_loop().run_until_complete(memory.lookup(sig, tenant_id))
        assert mem_hits == []  # miss → proceed to escalation

        # Level 3: escalation gate fires (all conditions met)
        ctx = make_ctx(tenant_id=tenant_id)
        result = asyncio.get_event_loop().run_until_complete(gate.evaluate(ctx))
        assert result.approved  # commercial call succeeds

        # After success: store in memory for next time
        asyncio.get_event_loop().run_until_complete(
            memory.store_success(sig, result.fixed_files, tenant_id, ctx.job_id, result.model_used)
        )

        # After gate passes: write to cache
        asyncio.get_event_loop().run_until_complete(
            cache.write(key, "s3://new_bundle.zip", "https://git.test",
                       "New app", ctx.job_id, tenant_id)
        )

        # Next time: Level 1 hits immediately
        hit2 = asyncio.get_event_loop().run_until_complete(cache.lookup(key, tenant_id))
        assert hit2 is not None and hit2.is_exact