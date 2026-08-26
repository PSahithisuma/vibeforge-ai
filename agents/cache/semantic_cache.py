"""
VibeForge — Post-QA Semantic Cache (Phase 3 / Phase 5 Hardening)
===============================================================
Cache key = canonical Spec IR hash + stack_profile + scaffold_version + ruleset_version.
Written ONLY after full gate pass (Contract C9). Never silently substituted — near-hits
are suggested to the user, who decides whether to accept.

Hardening Rules (Phase 5):
  1. Gate-pass-only enforcement: writing failed gate results raises ValueError.
  2. Tenant-scoped TTL: entries expire after a configurable duration (default: 7 days).
  3. Multi-tenant privacy: Tenant B cannot read Tenant A's private cache.
  4. Auto-promotion: Entries with >= 3 reuses are promoted to shared global cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


# ── Cache key ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CacheKey:
    """
    Immutable cache key. Same spec + same stack + same versions = same key.
    """
    canonical_hash: str       # from ApplicationSpec.canonical_hash (freeze())
    stack_profile: str        # java_spring | python_fastapi | dotnet
    scaffold_version: str     # semver tag of the scaffold template
    ruleset_version: str      # semver tag of the compliance ruleset

    def compute(self) -> str:
        """Deterministic string key for exact lookup."""
        raw = "|".join([
            self.canonical_hash,
            self.stack_profile,
            self.scaffold_version,
            self.ruleset_version,
        ])
        return hashlib.sha256(raw.encode()).hexdigest()

    def __str__(self) -> str:
        return (
            f"CacheKey(spec={self.canonical_hash[:12]}... "
            f"stack={self.stack_profile} "
            f"scaffold=v{self.scaffold_version} "
            f"ruleset=v{self.ruleset_version})"
        )


# ── Cache entry ────────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    """
    One cached generation result.
    Written only after full gate pass (Contract C9).
    """
    entry_id: str = field(default_factory=lambda: str(uuid4()))
    cache_key_hash: str = ""            # CacheKey.compute()

    # The cached artifacts
    artifact_bundle_url: str = ""       # MinIO URL to the complete bundle
    gitea_repo_url: str = ""            # Gitea repo URL
    preview_url: str = ""               # Traefik preview URL

    # Metadata for near-hit suggestions
    spec_summary: str = ""              # human-readable spec description
    vertical: str = ""
    entity_count: int = 0
    endpoint_count: int = 0
    stack_profile: str = ""

    # Provenance & Isolation
    job_id: str = ""
    tenant_id: str = ""
    shared: bool = False                # True if promoted to global shared cache
    gate_passed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    expires_at: Optional[str] = None    # ISO timestamp for TTL expiration

    # Usage stats
    hit_count: int = 0
    last_hit_at: Optional[str] = None

    # Embedding for near-hit search
    embedding_text: str = ""
    embedding: Optional[list[float]] = None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Checks whether the cache entry has exceeded its TTL."""
        if not self.expires_at:
            return False
        current_time = now or datetime.now(timezone.utc)
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return current_time >= exp
        except Exception:
            return False


@dataclass
class CacheHit:
    """A cache lookup result."""
    entry: CacheEntry
    hit_type: str                       # "exact" | "near"
    similarity_score: float = 1.0       # 1.0 for exact, <1.0 for near

    @property
    def is_exact(self) -> bool:
        return self.hit_type == "exact"

    @property
    def is_near(self) -> bool:
        return self.hit_type == "near"


# ── In-memory cache store (for tests & local use) ─────────────────────────────

class InMemoryCacheStore:
    """
    Test/offline replacement for the production cache backend.
    Enforces TTL expiration, tenant isolation, and auto-promotion.
    """

    def __init__(self):
        self._entries: dict[str, CacheEntry] = {}  # cache_key_hash → entry

    def write(self, key: CacheKey, entry: CacheEntry) -> None:
        key_hash = key.compute()
        entry.cache_key_hash = key_hash
        self._entries[key_hash] = entry
        logger.info("[SemanticCache] Written: key=%s entry=%s", str(key), entry.entry_id)

    def lookup_exact(self, key: CacheKey, tenant_id: str = "") -> Optional[CacheEntry]:
        key_hash = key.compute()
        entry = self._entries.get(key_hash)
        if not entry:
            return None

        # Check TTL expiration
        if entry.is_expired():
            logger.info("[SemanticCache] Evicting expired entry: %s", key_hash)
            del self._entries[key_hash]
            return None

        # Check Tenant Scoping
        if tenant_id and not entry.shared and entry.tenant_id != tenant_id:
            logger.info("[SemanticCache] Tenant mismatch (private entry) for key=%s", key_hash)
            return None

        entry.hit_count += 1
        entry.last_hit_at = datetime.now(timezone.utc).isoformat()

        # Contract C14 / C9: Auto-promote after 3 reuses
        if entry.hit_count >= 3 and not entry.shared:
            entry.shared = True
            logger.info("[SemanticCache] Auto-promoted entry to shared global: %s", entry.entry_id)

        return entry

    def lookup_near(
        self,
        key: CacheKey,
        tenant_id: str = "",
        min_similarity: float = 0.92,
        top_k: int = 3,
    ) -> list[CacheHit]:
        hits: list[CacheHit] = []
        query_text = self._key_to_embedding_text(key)
        query_tokens = set(query_text.lower().split())

        expired_keys = []
        for kh, entry in list(self._entries.items()):
            if entry.is_expired():
                expired_keys.append(kh)
                continue

            if tenant_id and not entry.shared and entry.tenant_id != tenant_id:
                continue

            if not entry.embedding_text:
                continue
            entry_tokens = set(entry.embedding_text.lower().split())
            if not query_tokens or not entry_tokens:
                continue
            intersection = len(query_tokens & entry_tokens)
            union = len(query_tokens | entry_tokens)
            similarity = intersection / union if union > 0 else 0.0

            if similarity >= min_similarity:
                hits.append(CacheHit(
                    entry=entry,
                    hit_type="near",
                    similarity_score=similarity,
                ))

        # Evict expired entries
        for kh in expired_keys:
            del self._entries[kh]

        hits.sort(key=lambda h: h.similarity_score, reverse=True)
        return hits[:top_k]

    @staticmethod
    def _key_to_embedding_text(key: CacheKey) -> str:
        return f"stack:{key.stack_profile} scaffold:{key.scaffold_version} ruleset:{key.ruleset_version}"

    def invalidate(self, cache_key_hash: str) -> bool:
        if cache_key_hash in self._entries:
            del self._entries[cache_key_hash]
            return True
        return False

    @staticmethod
    def format_near_hit_suggestion(hit: CacheHit) -> dict[str, Any]:
        """
        Formats a near-hit suggestion for the user (Contract C7).
        Near-hits are suggested, never silently auto-substituted.
        """
        score_pct = int(round(hit.similarity_score * 100))
        return {
            "type": "near_cache_hit",
            "similarity_score": hit.similarity_score,
            "similarity_pct": f"{score_pct}%",
            "spec_summary": hit.entry.spec_summary,
            "artifact_bundle_url": hit.entry.artifact_bundle_url,
            "gitea_repo_url": hit.entry.gitea_repo_url,
            "preview_url": hit.entry.preview_url,
            "message": (
                f"Found similar generated project ({score_pct}% match): "
                f"'{hit.entry.spec_summary}'. Use as starting baseline?"
            ),
            "user_choices": [
                "Use cached baseline and adapt",
                "Generate fresh from scratch",
            ],
        }

    def stats(self) -> dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "total_hits": sum(e.hit_count for e in self._entries.values()),
        }


# ── Post-QA Semantic Cache ─────────────────────────────────────────────────────

class SemanticCache:
    """
    Post-QA semantic cache with Phase 5 hardening (Contract C9).
    """

    NEAR_HIT_THRESHOLD = 0.92
    DEFAULT_TTL_SECONDS = 604800  # 7 days

    def __init__(self, store=None, default_ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._store = store or InMemoryCacheStore()
        self._default_ttl_seconds = default_ttl_seconds

    async def lookup(
        self,
        key: CacheKey,
        tenant_id: str = "",
    ) -> Optional[CacheHit]:
        exact = self._store.lookup_exact(key, tenant_id=tenant_id)
        if exact:
            return CacheHit(entry=exact, hit_type="exact", similarity_score=1.0)

        near_hits = self._store.lookup_near(
            key=key,
            tenant_id=tenant_id,
            min_similarity=self.NEAR_HIT_THRESHOLD,
            top_k=1,
        )
        if near_hits:
            return near_hits[0]

        return None

    async def write(
        self,
        key: CacheKey,
        artifact_bundle_url: str,
        gitea_repo_url: str,
        spec_summary: str,
        job_id: str,
        tenant_id: str,
        vertical: str = "",
        entity_count: int = 0,
        endpoint_count: int = 0,
        preview_url: str = "",
        gate_passed: bool = True,
        ttl_seconds: Optional[int] = None,
    ) -> CacheEntry:
        """
        Write to cache after full gate pass.
        CONTRACT C9: gate_passed must be True.
        """
        if not gate_passed:
            raise ValueError("Contract C9 Violation: Cannot write failed gate builds to semantic cache.")

        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
            if ttl > 0 else None
        )

        embedding_text = self._build_embedding_text(
            key=key,
            spec_summary=spec_summary,
            vertical=vertical,
            entity_count=entity_count,
        )

        entry = CacheEntry(
            cache_key_hash=key.compute(),
            artifact_bundle_url=artifact_bundle_url,
            gitea_repo_url=gitea_repo_url,
            preview_url=preview_url,
            spec_summary=spec_summary,
            vertical=vertical,
            entity_count=entity_count,
            endpoint_count=endpoint_count,
            stack_profile=key.stack_profile,
            job_id=job_id,
            tenant_id=tenant_id,
            expires_at=expires_at,
            embedding_text=embedding_text,
        )

        self._store.write(key, entry)
        return entry

    async def invalidate(self, key: CacheKey) -> bool:
        key_hash = key.compute()
        return self._store.invalidate(key_hash)

    @staticmethod
    def format_near_hit_suggestion(hit: CacheHit) -> dict[str, Any]:
        """
        Formats a near-hit suggestion for the user (Contract C7).
        Near-hits are suggested, never silently auto-substituted.
        """
        score_pct = int(round(hit.similarity_score * 100))
        return {
            "type": "near_cache_hit",
            "similarity_score": hit.similarity_score,
            "similarity_pct": f"{score_pct}%",
            "spec_summary": hit.entry.spec_summary,
            "artifact_bundle_url": hit.entry.artifact_bundle_url,
            "gitea_repo_url": hit.entry.gitea_repo_url,
            "preview_url": hit.entry.preview_url,
            "message": (
                f"Found similar generated project ({score_pct}% match): "
                f"'{hit.entry.spec_summary}'. Use as starting baseline?"
            ),
            "user_choices": [
                "Use cached baseline and adapt",
                "Generate fresh from scratch",
            ],
        }

    def stats(self) -> dict[str, Any]:
        return self._store.stats()

    @staticmethod
    def _build_embedding_text(
        key: CacheKey,
        spec_summary: str,
        vertical: str,
        entity_count: int,
    ) -> str:
        parts = [
            f"vertical:{vertical}",
            f"stack:{key.stack_profile}",
            f"entities:{entity_count}",
            f"scaffold:{key.scaffold_version}",
            f"ruleset:{key.ruleset_version}",
        ]
        if spec_summary:
            parts.append(f"summary:{spec_summary[:200]}")
        return " ".join(parts)

    @staticmethod
    def build_key(
        canonical_hash: str,
        stack_profile: str,
        scaffold_version: str = "1.0.0",
        ruleset_version: str = "1.0.0",
    ) -> CacheKey:
        return CacheKey(
            canonical_hash=canonical_hash,
            stack_profile=stack_profile,
            scaffold_version=scaffold_version,
            ruleset_version=ruleset_version,
        )
