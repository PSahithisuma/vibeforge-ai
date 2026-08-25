from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import text

from core.auth import AuthUser, get_current_user
from core.database import get_tenant_session
from core.redis_client import get_redis

router = APIRouter(prefix="/api/v1/jobs", tags=["stream"])

# Match jobs table status values written by the worker
_TERMINAL: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


def _normalise_payload(raw) -> dict:
    """
    job_events.payload may come back as dict (JSONB) or str (TEXT).
    Always return a dict so json.dumps never double-encodes.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"raw": raw}
    return {}


@router.get(
    "/{job_id}/stream",
    response_class=EventSourceResponse,
    summary="Stream job progress events via SSE",
)
async def stream_job_events(
    job_id: UUID,
    user: AuthUser = Depends(get_current_user),
) -> EventSourceResponse:
    """
    Server-Sent Events endpoint -- streams job_events in real-time.

    Path A (terminal job): replay all historical events from Postgres,
    emit a synthetic "done" event, then close.

    Path B (running job): subscribe to Redis pub/sub channel job:{job_id},
    forward each event to the client, close when event_type is
    "complete" or "error".

    Auth: tenant_id from JWT -> RLS enforced on DB queries.
    """
    async with get_tenant_session(user.tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT status FROM jobs WHERE id = :id"),
                {"id": str(job_id)},
            )
        ).one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    current_status: str = row.status

    # -- Generator ------------------------------------------------------------

    async def event_generator():

        # Path A: already terminal -- replay from Postgres ------------------
        if current_status in _TERMINAL:
            async with get_tenant_session(user.tenant_id) as session:
                events = (
                    await session.execute(
                        text("""
                            SELECT event_type, payload
                              FROM job_events
                             WHERE job_id = :job_id
                             ORDER BY seq ASC
                        """),
                        {"job_id": str(job_id)},
                    )
                ).mappings().all()

            for evt in events:
                payload = _normalise_payload(evt["payload"])
                yield {
                    "event": evt["event_type"],
                    "data": json.dumps({
                        "event_type": evt["event_type"],
                        "payload":    payload,
                    }),
                }

            # Synthetic terminal signal so the client can close cleanly
            yield {
                "event": "done",
                "data": json.dumps({"status": current_status}),
            }
            return

        # Path B: job running -- subscribe to Redis pub/sub -----------------
        redis = get_redis()
        pubsub = redis.pubsub()
        channel = f"job:{job_id}"
        await pubsub.subscribe(channel)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    # "subscribe" ack messages -- skip
                    continue

                raw: str = message["data"]
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type: str = parsed.get("event_type", "message")

                # Normalise payload in forwarded events too
                if "payload" in parsed and isinstance(parsed["payload"], str):
                    try:
                        parsed["payload"] = json.loads(parsed["payload"])
                    except (json.JSONDecodeError, ValueError):
                        pass

                yield {"event": event_type, "data": json.dumps(parsed)}

                # Close generator on terminal event -- client will disconnect
                if event_type in ("complete", "error"):
                    break

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return EventSourceResponse(event_generator())