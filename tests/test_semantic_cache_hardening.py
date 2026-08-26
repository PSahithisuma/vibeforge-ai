from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from agents.cache.semantic_cache import (
    CacheKey,
    InMemoryCacheStore,
    SemanticCache,
)

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())


def _run(coro):
    return asyncio.run(coro)


class TestSemanticCacheHardening:
    def _key(self, canon_hash: str = "a" * 64) -> CacheKey:
        return SemanticCache.build_key(
            canonical_hash=canon_hash,
            stack_profile="java_spring",
            scaffold_version="1.0.0",
            ruleset_version="1.0.0",
        )

    def test_gate_pass_only_enforcement(self):
        cache = SemanticCache(store=InMemoryCacheStore())
        key = self._key()

        # Writing with gate_passed=False must raise ValueError (Contract C9)
        with pytest.raises(ValueError) as exc:
            _run(cache.write(
                key=key,
                artifact_bundle_url="s3://bad_bundle.zip",
                gitea_repo_url="https://git/bad",
                spec_summary="Failed build",
                job_id=str(uuid4()),
                tenant_id=TENANT_A,
                gate_passed=False,
            ))
        assert "Contract C9 Violation" in str(exc.value)

    def test_tenant_isolation_cross_tenant_miss(self):
        cache = SemanticCache(store=InMemoryCacheStore())
        key = self._key()

        # Tenant A writes to cache
        _run(cache.write(
            key=key,
            artifact_bundle_url="s3://tenant_a_bundle.zip",
            gitea_repo_url="https://git/a",
            spec_summary="Tenant A App",
            job_id=str(uuid4()),
            tenant_id=TENANT_A,
            gate_passed=True,
        ))

        # Tenant A should hit
        hit_a = _run(cache.lookup(key, tenant_id=TENANT_A))
        assert hit_a is not None
        assert hit_a.is_exact

        # Tenant B should miss (strict tenant scoping)
        hit_b = _run(cache.lookup(key, tenant_id=TENANT_B))
        assert hit_b is None

    def test_ttl_expiration_evicts_entry(self):
        cache = SemanticCache(store=InMemoryCacheStore(), default_ttl_seconds=1)
        key = self._key()

        # Write with 1 second TTL
        entry = _run(cache.write(
            key=key,
            artifact_bundle_url="s3://expiring.zip",
            gitea_repo_url="https://git/exp",
            spec_summary="Short TTL app",
            job_id=str(uuid4()),
            tenant_id=TENANT_A,
            gate_passed=True,
            ttl_seconds=1,
        ))

        # Artificially age the entry to 10 seconds ago
        entry.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

        # Lookup should detect expiration and return None (miss)
        hit = _run(cache.lookup(key, tenant_id=TENANT_A))
        assert hit is None

    def test_auto_promotion_to_shared_after_3_hits(self):
        cache = SemanticCache(store=InMemoryCacheStore())
        key = self._key()

        entry = _run(cache.write(
            key=key,
            artifact_bundle_url="s3://popular.zip",
            gitea_repo_url="https://git/pop",
            spec_summary="Popular app",
            job_id=str(uuid4()),
            tenant_id=TENANT_A,
            gate_passed=True,
        ))

        assert entry.shared is False

        # Hit 1 & 2 by Tenant A
        _run(cache.lookup(key, tenant_id=TENANT_A))
        _run(cache.lookup(key, tenant_id=TENANT_A))
        assert entry.shared is False

        # Hit 3 triggers auto-promotion to shared global
        _run(cache.lookup(key, tenant_id=TENANT_A))
        assert entry.shared is True

        # Now Tenant B can also hit this shared entry!
        hit_b = _run(cache.lookup(key, tenant_id=TENANT_B))
        assert hit_b is not None
        assert hit_b.entry.artifact_bundle_url == "s3://popular.zip"
