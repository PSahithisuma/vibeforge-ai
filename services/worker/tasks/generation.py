"""
VibeForge — Generation Task (Phase 1 wiring)
=============================================
This replaces dummy.py. It is the real Arq task that runs when
a user clicks "Generate Spec" in the UI.

Flow:
    1. Load the spec from Postgres (compiled from option selections)
    2. Run CompletenessValidator Layer 1 — block if required fields missing
    3. Run CompletenessValidator Layer 2 — generate gap questions via Qwen3-8B
    4. If spec is complete → run the LangGraph generation pipeline
    5. Stream progress events via Postgres job_events table

Wiring:
    - LLM client: LiteLLM proxy at http://litellm:4000 (routes to Ollama)
    - Retrieval: RetrievalService at http://retrieval:8001
    - Gate: MetacognitionGate (zero LLM, always runs first)
    - Generation graph: run_generation_job() from agents/graphs/generation_graph.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import asyncpg
import httpx

logger = logging.getLogger(__name__)

# ── Path setup — agents/ must be importable from the worker container ──────────
# The worker mounts /app/worker as /app, and project root as /project
# agents/ is at /project/agents/
AGENTS_ROOT = Path(os.getenv("AGENTS_ROOT", "/project"))
for p in [str(AGENTS_ROOT), str(AGENTS_ROOT / "agents"), str(AGENTS_ROOT / "core")]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Environment ────────────────────────────────────────────────────────────────
DATABASE_URL      = os.getenv("DATABASE_URL", "postgresql://vibeforge:vibeforge_dev_secret@postgres:5432/vibeforge")
LITELLM_BASE_URL  = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
RETRIEVAL_URL     = os.getenv("RETRIEVAL_URL", "http://retrieval:8001")
PACK_DIR          = os.getenv("PACK_DIR", str(AGENTS_ROOT / "packs" / "ecommerce"))


# ── Retrieval function — calls the Retrieval Service HTTP endpoint ─────────────

async def retrieval_fn_factory(tenant_id: str):
    """
    Returns an async retrieval function scoped to this tenant.
    The Domain Wizard passes this to the MetacognitionGate.
    Contract C8: tenant_id is always sent.
    """
    async def _retrieve(query: str) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{RETRIEVAL_URL}/retrieve",
                    json={"query": query, "tenant_id": tenant_id, "top_k": 5},
                )
                resp.raise_for_status()
                data = resp.json()
                return [chunk["text"] for chunk in data.get("chunks", [])]
        except Exception as e:
            logger.warning("[Worker] Retrieval failed: %s — proceeding without RAG", e)
            return []
    return _retrieve


# ── Job event writer ───────────────────────────────────────────────────────────

async def write_event(
    conn: asyncpg.Connection,
    job_id: str,
    phase: str,
    status: str,
    data: dict,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
) -> None:
    """Write one progress event to job_events table."""
    try:
        event_type = f"{phase}:{status}"
        await conn.execute(
            """
            INSERT INTO job_events (tenant_id, job_id, event_type, payload, created_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
            """,
            tenant_id, job_id, event_type, json.dumps(data),
        )
    except Exception as e:
        logger.warning("[Worker] Could not write event: %s", e)


async def update_job_status(
    conn: asyncpg.Connection,
    job_id: str,
    status: str,
    result: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    """Update the job status in the jobs table."""
    try:
        await conn.execute(
            """
            UPDATE jobs
            SET status = $1,
                result_ref   = $2,
                error_detail = $3::jsonb,
                finished_at  = CASE WHEN $1 IN ('completed', 'failed') THEN NOW() ELSE NULL END,
                started_at   = CASE WHEN $1 = 'running' AND started_at IS NULL THEN NOW() ELSE started_at END
            WHERE id = $4
            """,
            status,
            json.dumps(result) if result else None,
            json.dumps({"error": error}) if error else None,
            job_id,
        )
    except Exception as e:
        logger.warning("[Worker] Could not update job status: %s", e)


# ── Main generation task ───────────────────────────────────────────────────────

async def run_generation(ctx, job_id: str, tenant_id: str, spec_data: dict) -> dict:
    """
    Arq task: runs the full generation pipeline for one job.

    Args:
        ctx:        Arq context (contains redis connection)
        job_id:     UUID of the job
        tenant_id:  Tenant that owns this job
        spec_data:  The compiled ApplicationSpec as a dict

    Returns:
        dict with status and any result data
    """
    logger.info("[Worker] Starting job %s for tenant %s", job_id, tenant_id)

    # Connect to Postgres for event streaming
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await update_job_status(conn, job_id, "running")
        await write_event(conn, job_id, "startup", "started", {
            "message": "Generation job started",
            "job_id": job_id,
                                    }, tenant_id)

        # ── Step 1: Import agents ─────────────────────────────────────────────
        try:
            from agents.llm_client import make_llm_client, LiteLLMClient
            from agents.gates.metacognition import MetacognitionGate
            from agents.conversation.completeness_validator import CompletenessValidator
            from agents.conversation.domain_wizard import DomainWizard, WizardTurnContext
            from agents.graphs.generation_graph import run_generation_job
            from agents.option_graph.engine import OptionGraphEngine

            await write_event(conn, job_id, "startup", "agents_loaded", {
                "message": "All agents loaded successfully",
                                                    }, tenant_id)
        except ImportError as e:
            error_msg = f"Could not import agents: {e}"
            logger.error("[Worker] %s", error_msg)
            await write_event(conn, job_id, "startup", "failed", {"error": error_msg}, tenant_id)
            await update_job_status(conn, job_id, "failed", error=error_msg)
            return {"status": "failed", "error": error_msg}

        # ── Step 2: Build LLM client (LiteLLM proxy → Ollama → Qwen3-8B) ─────
        llm_client = LiteLLMClient(
            base_url=LITELLM_BASE_URL,
            api_key=os.getenv("LITELLM_MASTER_KEY", "vibeforge"),
        )

        await write_event(conn, job_id, "startup", "llm_ready", {
            "message": f"LLM client wired to {LITELLM_BASE_URL}",
            "model": "agent-model → qwen3:8b via Ollama",
                                    }, tenant_id)

        # ── Step 3: Run Completeness Validator Layer 1 (zero LLM) ────────────
        await write_event(conn, job_id, "validation", "started", {
            "message": "Running completeness checks",
                                    }, tenant_id)

        validator = CompletenessValidator(llm_client=llm_client)
        layer1_result = validator.validate_sync(spec_data)

        if not layer1_result.can_proceed_to_review:
            error_msg = f"Spec incomplete: {layer1_result.missing_required}"
            await write_event(conn, job_id, "validation", "failed", {
                "message": "Spec failed completeness checks",
                "missing": layer1_result.missing_required,
                "completeness_pct": layer1_result.completeness_percent,
                                                    }, tenant_id)
            await update_job_status(conn, job_id, "failed", error=error_msg)
            return {"status": "failed", "error": error_msg, "missing": layer1_result.missing_required}

        await write_event(conn, job_id, "validation", "passed", {
            "message": "Spec passed all completeness checks",
            "completeness_pct": layer1_result.completeness_percent,
                                    }, tenant_id)

        # ── Step 4: Run Completeness Validator Layer 2 (Qwen3-8B gap analysis)
        await write_event(conn, job_id, "gap_analysis", "started", {
            "message": "Running gap analysis with Qwen3-8B",
                                    }, tenant_id)

        try:
            full_result = await validator.validate(spec_data, run_gap_analysis=True)
            gap_questions = [
                {
                    "question_id": gq.question_id,
                    "question": gq.question,
                    "choice_chips": gq.choice_chips,
                    "priority": gq.priority,
                }
                for gq in full_result.gap_questions
            ]
            await write_event(conn, job_id, "gap_analysis", "complete", {
                "message": f"Found {len(gap_questions)} gap questions",
                "gap_questions": gap_questions,
                                                    }, tenant_id)
        except Exception as e:
            logger.warning("[Worker] Gap analysis failed: %s — continuing", e)
            await write_event(conn, job_id, "gap_analysis", "skipped", {
                "message": f"Gap analysis skipped: {e}",
                                                    }, tenant_id)
            gap_questions = []

        # ── Step 5: Build spec object for the generation graph ────────────────
        await write_event(conn, job_id, "planning", "started", {
            "message": "Building spec for generation pipeline",
                                    }, tenant_id)

        # Extract entity names for the planner
        entity_names = [
            e.get("name", "") for e in
            spec_data.get("domain_model", {}).get("entities", [])
        ]

        # ── Step 6: Run the LangGraph generation pipeline ─────────────────────
        await write_event(conn, job_id, "generation", "started", {
            "message": "Starting LangGraph generation pipeline",
            "entities": entity_names,
            "module_count_estimate": len(entity_names) * 3 + 1,
                                    }, tenant_id)

        try:
            final_state = await run_generation_job(
                job_id=job_id,
                tenant_id=tenant_id,
                spec_entity_names=entity_names,
                postgres_dsn=DATABASE_URL,
            )

            # Stream the generation events from the graph state
            for event in getattr(final_state, "events", []):
                await write_event(
                    conn, job_id,
                    getattr(event, "phase", "generation"),
                    getattr(event, "node", "unknown"),
                    getattr(event, "data", {}),
                )

            await write_event(conn, job_id, "generation", "complete", {
                "message": "Generation pipeline complete",
                "fix_iterations": getattr(final_state, "fix_count", 0),
                "file_count": len(getattr(final_state, "assembled_files", {})),
                                                    }, tenant_id)

        except Exception as e:
            logger.error("[Worker] Generation pipeline failed: %s", e)
            await write_event(conn, job_id, "generation", "failed", {
                "message": f"Generation failed: {str(e)[:200]}",
                                                    }, tenant_id)
            await update_job_status(conn, job_id, "failed", error=str(e)[:500])
            return {"status": "failed", "error": str(e)}

        # ── Step 7: Mark job complete ─────────────────────────────────────────
        result = {
            "gap_questions": gap_questions,
            "entity_count": len(entity_names),
            "completeness_pct": layer1_result.completeness_percent,
        }
        await update_job_status(conn, job_id, "completed", result=result)
        await write_event(conn, job_id, "delivery", "complete", {
            "message": "Job completed successfully",
            "result": result,
                                    }, tenant_id)

        logger.info("[Worker] Job %s completed successfully", job_id)
        return {"status": "completed", "result": result}

    except Exception as e:
        logger.error("[Worker] Unexpected error in job %s: %s", job_id, e)
        try:
            await update_job_status(conn, job_id, "failed", error=str(e)[:500])
        except Exception:
            pass
        return {"status": "failed", "error": str(e)}

    finally:
        await conn.close()


# ── Spec Sheet validation task (lightweight — no generation) ───────────────────

async def validate_spec(ctx, job_id: str, tenant_id: str, spec_data: dict) -> dict:
    """
    Arq task: validates a spec and returns gap questions.
    Called when user clicks "Check completeness" without triggering generation.
    """
    logger.info("[Worker] Validating spec for job %s", job_id)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        from agents.llm_client import LiteLLMClient
        from agents.conversation.completeness_validator import CompletenessValidator

        llm_client = LiteLLMClient(
            base_url=LITELLM_BASE_URL,
            api_key=os.getenv("LITELLM_MASTER_KEY", "vibeforge"),
        )

        validator = CompletenessValidator(llm_client=llm_client)
        result = await validator.validate(spec_data, run_gap_analysis=True)

        return {
            "status": "ok",
            "is_complete": result.is_complete,
            "completeness_pct": result.completeness_percent,
            "missing_required": result.missing_required,
            "gap_questions": [
                {
                    "question_id": gq.question_id,
                    "question": gq.question,
                    "choice_chips": gq.choice_chips,
                    "priority": gq.priority,
                }
                for gq in result.gap_questions
            ],
        }
    except Exception as e:
        logger.error("[Worker] Validation failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        await conn.close()