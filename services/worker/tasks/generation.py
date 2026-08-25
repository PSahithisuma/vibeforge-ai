"""
VibeForge -- Generation Task (Phase 1 wiring)
=============================================
Arq task that runs when a user clicks "Generate Spec" in the UI.

Flow:
    1. Load compiled spec from Postgres
    2. Build LiteLLM client (optional -- Phase 0 works without it)
    3. Run the LangGraph generation pipeline
    4. Stream all events to:
       a. Postgres job_events  (persistence / Path A replay)
       b. Redis pub/sub job:{job_id}  (Path B real-time)
    5. Publish terminal signal ("complete" | "error") to Redis
    6. Update jobs table with final status

Wiring:
    - LLM:   LiteLLM proxy at LITELLM_BASE_URL  (optional)
    - Graph: run_generation_job() from agents/graphs/generation_graph.py
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from agents.graphs.generation_graph import (
    JobEvent,
    JobStatus,
    run_generation_job,
)

logger = logging.getLogger(__name__)


# -- DB helper -----------------------------------------------------------------

async def _write_event(
    conn,
    job_id: str,
    tenant_id: str,
    event: JobEvent,
) -> None:
    """Persist one JobEvent to the job_events table."""
    await conn.execute(
        """
        INSERT INTO job_events
            (event_id, job_id, tenant_id, event_type, payload, created_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        ON CONFLICT (event_id) DO NOTHING
        """,
        getattr(event, "event_id", None) or str(uuid4()),
        job_id,
        tenant_id,
        event.phase.value,
        json.dumps(event.payload),
        getattr(event, "ts", None) or datetime.now(timezone.utc).isoformat(),
    )


async def _publish_event(redis, job_id: str, event: JobEvent) -> None:
    """Publish one JobEvent to the Redis pub/sub channel for this job."""
    msg = json.dumps({
        "event_type": event.phase.value,
        "job_id":     job_id,
        "node":       event.node,
        "phase":      event.phase.value,
        "payload":    event.payload,
        "ts":         getattr(event, "ts", ""),
    })
    await redis.publish(f"job:{job_id}", msg)


# -- LLM client factory -------------------------------------------------------

def _build_llm_client() -> Optional[Any]:
    """
    Build a LiteLLMClient if LITELLM_BASE_URL is configured.
    Returns None in Phase 0 / test environments.
    """
    base_url = os.environ.get("LITELLM_BASE_URL", "")
    api_key  = os.environ.get("LITELLM_API_KEY", "")
    if not base_url:
        logger.info("[generate_spec] LITELLM_BASE_URL not set -- Phase 0 stub mode")
        return None
    try:
        from agents.llm.litellm_client import LiteLLMClient
        return LiteLLMClient(base_url=base_url, api_key=api_key)
    except ImportError:
        logger.warning("[generate_spec] LiteLLMClient not importable -- Phase 0 stub mode")
        return None


# -- Arq task -----------------------------------------------------------------

async def generate_spec(
    ctx: dict,
    *,
    job_id: str,
    tenant_id: str,
    spec_id: str,
) -> dict[str, str]:
    """
    Arq worker task: run the full generation pipeline for one job.

    ctx["redis"] -- shared aioredis connection from the worker pool
    """
    import asyncpg

    postgres_dsn: str = os.environ.get("DATABASE_URL", "")
    redis = ctx.get("redis")

    logger.info(
        "[generate_spec] starting  job=%s  spec=%s  tenant=%s",
        job_id, spec_id, tenant_id,
    )

    conn = await asyncpg.connect(postgres_dsn)
    try:
        # 1. Mark job as running
        await conn.execute(
            "UPDATE jobs SET status = 'running', started_at = NOW() WHERE id = $1",
            job_id,
        )

        # 2. Load compiled spec
        spec_row = await conn.fetchrow(
            "SELECT compiled_spec FROM specs WHERE id = $1 AND tenant_id = $2",
            spec_id, tenant_id,
        )
        if not spec_row:
            raise ValueError(f"Spec {spec_id!r} not found for tenant {tenant_id!r}")

        raw = spec_row["compiled_spec"]
        spec_data: dict = json.loads(raw) if isinstance(raw, str) else (raw or {})
        stack_profile: str = spec_data.get("stack", {}).get("backend", "java_spring")

        # 3. Build LLM client
        llm_client = _build_llm_client()

        # 4. Run pipeline
        final_state = await run_generation_job(
            job_id=job_id,
            tenant_id=tenant_id,
            postgres_dsn=postgres_dsn,
            llm_client=llm_client,
            spec_snapshot=spec_data,
            stack_profile=stack_profile,
        )

        # 5. Stream events -> Postgres + Redis
        for raw_event in final_state.events:
            event = raw_event if isinstance(raw_event, JobEvent) else JobEvent(raw_event)
            await _write_event(conn, job_id, tenant_id, event)
            if redis:
                await _publish_event(redis, job_id, event)

        # 6. Publish terminal signal
        delivered     = final_state.job_status == JobStatus.DELIVERED
        terminal_type = "complete" if delivered else "error"
        if redis:
            await redis.publish(
                f"job:{job_id}",
                json.dumps({
                    "event_type":  terminal_type,
                    "job_id":      job_id,
                    "status":      final_state.job_status.value,
                    "preview_url": final_state.preview_url or "",
                    "gitea_url":   final_state.gitea_repo_url or "",
                }),
            )

        # 7. Update jobs table
        db_status = "completed" if delivered else final_state.job_status.value
        await conn.execute(
            """
            UPDATE jobs
               SET status         = $1,
                   completed_at   = NOW(),
                   preview_url    = $2,
                   gitea_repo_url = $3
             WHERE id = $4
            """,
            db_status,
            final_state.preview_url,
            final_state.gitea_repo_url,
            job_id,
        )

        logger.info(
            "[generate_spec] done  job=%s  status=%s  events=%d",
            job_id, db_status, len(final_state.events),
        )
        return {"job_id": job_id, "status": db_status}

    except Exception as exc:
        logger.error(
            "[generate_spec] FAILED  job=%s  error=%s",
            job_id, exc, exc_info=True,
        )
        try:
            await conn.execute(
                """
                UPDATE jobs
                   SET status        = 'failed',
                       completed_at  = NOW(),
                       error_message = $1
                 WHERE id = $2
                """,
                str(exc), job_id,
            )
            if redis:
                await redis.publish(
                    f"job:{job_id}",
                    json.dumps({
                        "event_type": "error",
                        "job_id":     job_id,
                        "error":      str(exc),
                    }),
                )
        except Exception:
            pass
        raise

    finally:
        await conn.close()