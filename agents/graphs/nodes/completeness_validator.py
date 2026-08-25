from __future__ import annotations

import json
import re

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Rule-based gap detection (Phase 0) ───────────────────────────────────────

_BASE_CHECKS: list[tuple[str, str]] = [
    ("auth",         "Does the system require user authentication? (JWT / OAuth2 / API key / none)"),
    ("database",     "Which database engine? (PostgreSQL / MySQL / MongoDB / SQLite)"),
    ("file_storage", "Does the system need file or image upload? (yes / no)"),
    ("pagination",   "Should list/search endpoints support pagination? (yes / no)"),
]

_STACK_CHECKS: dict[str, list[tuple[str, str]]] = {
    "java_spring": [
        ("build_tool",       "Which build tool? (Maven / Gradle)"),
        ("spring_security",  "Should Spring Security be included? (yes / no)"),
    ],
    "python_fastapi": [
        ("alembic",   "Should Alembic handle DB migrations? (yes / no)"),
        ("async_db",  "Should the DB driver be async (asyncpg / aiomysql)? (yes / no)"),
    ],
    "node_express": [
        ("orm",        "Which ORM? (Prisma / TypeORM / Sequelize / none)"),
        ("typescript", "Should the project use TypeScript? (yes / no)"),
    ],
}

_DOMAIN_KEYWORDS: dict[str, list[tuple[str, str]]] = {
    "payment": [
        ("payment_provider", "Which payment provider? (Stripe / PayPal / Razorpay / other)"),
        ("currency",         "Which currencies should be supported?"),
    ],
    "email": [
        ("email_provider", "Which email provider? (SendGrid / SES / SMTP / other)"),
    ],
    "notification": [
        ("notification_channel", "Which notification channels? (email / SMS / push / all)"),
    ],
    "search": [
        ("search_backend", "Which search backend? (Elasticsearch / PostgreSQL FTS / other)"),
    ],
}

_IMPLICIT: dict[str, list[str]] = {
    "auth":                ["jwt", "oauth", "api_key", "no auth", "public"],
    "database":            ["postgresql", "postgres", "mysql", "mongodb", "sqlite"],
    "file_storage":        ["s3", "minio", "gcs", "blob", "upload", "no file"],
    "pagination":          ["page", "limit", "offset", "cursor", "no pagination"],
    "build_tool":          ["maven", "gradle"],
    "spring_security":     ["spring security", "spring_security", "no security"],
    "alembic":             ["alembic", "no migration"],
    "async_db":            ["asyncpg", "aiomysql", "sync"],
    "orm":                 ["prisma", "typeorm", "sequelize", "no orm"],
    "typescript":          ["typescript", "javascript"],
    "payment_provider":    ["stripe", "paypal", "razorpay"],
    "currency":            ["usd", "eur", "inr", "currency"],
    "email_provider":      ["sendgrid", "ses", "smtp"],
    "notification_channel": [],
    "search_backend": ["elasticsearch", "opensearch"],
}


class CompletenessValidator:
    """
    Analyses a spec snapshot and returns a list of gap questions.

    Phase 0: deterministic rule-based detection (no LLM required).
    Phase 2: optional llm_client enriches the question list via Qwen3-8B.
    """

    def __init__(self, llm_client: Optional[Any] = None) -> None:
        self._llm = llm_client

    def validate(
        self,
        spec_snapshot: dict[str, Any],
        stack_profile: str = "java_spring",
        existing_answers: Optional[dict[str, str]] = None,
    ) -> list[str]:
        """
        Return gap questions as a list of strings.
        Empty list means the spec is complete enough to proceed.
        Questions already answered (in existing_answers or implied in the spec)
        are skipped.
        """
        answered: set[str] = set(existing_answers or {})
        spec_text: str = str(spec_snapshot).lower()
        gaps: list[str] = []

        for key, question in _BASE_CHECKS:
            if key not in answered and not self._implied(key, spec_text):
                gaps.append(question)

        for key, question in _STACK_CHECKS.get(stack_profile, []):
            if key not in answered and not self._implied(key, spec_text):
                gaps.append(question)

        for keyword, checks in _DOMAIN_KEYWORDS.items():
            if keyword in spec_text:
                for key, question in checks:
                    if key not in answered and not self._implied(key, spec_text):
                        gaps.append(question)

        if self._llm and gaps:
            try:
                extra = self._llm_enrich(spec_snapshot, gaps)
                gaps = _deduplicate(gaps + extra)
            except Exception as exc:
                logger.warning("[CompletenessValidator] LLM enrichment failed: %s", exc)

        logger.info(
            "[CompletenessValidator] stack=%s  gaps=%d  already_answered=%d",
            stack_profile, len(gaps), len(answered),
        )
        return gaps

    def _implied(self, key: str, spec_text: str) -> bool:
        """True if the spec text already implies an answer for this key."""
        return any(hint in spec_text for hint in _IMPLICIT.get(key, []))

    def _llm_enrich(self, spec: dict[str, Any], current_gaps: list[str]) -> list[str]:
        """
        Queries Qwen3-8B to detect non-obvious domain/architecture gaps in the spec.
        Returns a list of clean, actionable question strings.
        """
        if not self._llm:
            return []

        prompt = (
            "You are an expert software architect evaluating a backend specification for completeness.\n"
            f"Spec Snapshot:\n{json.dumps(spec, indent=2)}\n\n"
            f"Already identified gaps:\n" + "\n".join(f"- {g}" for g in current_gaps) + "\n\n"
            "Identify up to 3 critical missing technical requirements (e.g. rate limiting, caching, audit logging, webhook retry policy).\n"
            "Respond ONLY with a valid JSON list of question strings, e.g. [\"Question 1?\", \"Question 2?\"]. "
            "If no critical gaps remain, return []."
        )

        try:
            if hasattr(self._llm, "invoke"):
                res = self._llm.invoke(prompt)
                content = res.content if hasattr(res, "content") else str(res)
            elif callable(self._llm):
                res = self._llm(prompt)
                content = res.content if hasattr(res, "content") else str(res)
            else:
                return []

            # 1. Try parsing JSON array directly
            match = re.search(r"\[.*?\]", content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, list):
                        return [str(q).strip() for q in parsed if str(q).strip().endswith("?")]
                except Exception:
                    pass

            # 2. Fallback: Parse question lines
            questions = []
            for line in content.splitlines():
                clean = line.strip().lstrip("0123456789.-* ")
                if clean.endswith("?") and len(clean) > 10:
                    questions.append(clean)
            return questions[:3]

        except Exception as exc:
            logger.warning("[CompletenessValidator] _llm_enrich failed: %s", exc)
            return []


def _deduplicate(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

