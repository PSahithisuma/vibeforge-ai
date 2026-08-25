from __future__ import annotations

import json

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from core.auth import AuthUser, get_current_user
from core.database import get_tenant_session

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class JobEventOut(BaseModel):
    seq: int
    event_type: str
    payload: dict
    created_at: datetime


class JobOut(BaseModel):
    id: UUID
    tenant_id: UUID
    spec_id: UUID
    status: str
    job_type: str
    idempotency_key: str
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[JobOut])
async def list_jobs(
    user: AuthUser = Depends(get_current_user),
) -> list[JobOut]:
    """List the 50 most recent jobs for the authenticated tenant."""
    async with get_tenant_session(user.tenant_id) as session:
        rows = (
            await session.execute(
                text("""
                    SELECT id, tenant_id, spec_id, status, job_type, idempotency_key,
                           created_at, started_at, finished_at
                    FROM jobs
                    ORDER BY created_at DESC
                    LIMIT 50
                """),
            )
        ).mappings().all()

    return [JobOut(**r) for r in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: UUID,
    user: AuthUser = Depends(get_current_user),
) -> JobOut:
    """Get a single job by ID (RLS enforces tenant ownership)."""
    async with get_tenant_session(user.tenant_id) as session:
        row = (
            await session.execute(
                text("""
                    SELECT id, tenant_id, spec_id, status, job_type, idempotency_key,
                           created_at, started_at, finished_at
                    FROM jobs WHERE id = :id
                """),
                {"id": str(job_id)},
            )
        ).mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobOut(**row)


@router.get("/{job_id}/events", response_model=list[JobEventOut])
async def get_job_events(
    job_id: UUID,
    user: AuthUser = Depends(get_current_user),
) -> list[JobEventOut]:
    """Return all persisted events for a job in sequence order."""
    async with get_tenant_session(user.tenant_id) as session:
        # Verify job ownership first
        job = (
            await session.execute(
                text("SELECT id FROM jobs WHERE id = :id"),
                {"id": str(job_id)},
            )
        ).scalar_one_or_none()

        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        rows = (
            await session.execute(
                text("""
                    SELECT seq, event_type, payload, created_at
                    FROM job_events
                    WHERE job_id = :job_id
                    ORDER BY seq ASC
                """),
                {"job_id": str(job_id)},
            )
        ).mappings().all()

    return [JobEventOut(**r) for r in rows]


# ---------------------------------------------------------------------------
# Completeness Validator Layer 2 — Gap Q&A
# ---------------------------------------------------------------------------

class GapQuestionsOut(BaseModel):
    job_id: UUID
    status: str
    questions: list[str]


class AnswersIn(BaseModel):
    answers: dict[str, str]


class AnswersOut(BaseModel):
    job_id: UUID
    status: str
    remaining_questions: list[str]
    requeued: bool


@router.get("/{job_id}/questions", response_model=GapQuestionsOut)
async def get_gap_questions(
    job_id: UUID,
    user: AuthUser = Depends(get_current_user),
) -> GapQuestionsOut:
    """Return outstanding gap questions for a job in paused_human status."""
    async with get_tenant_session(user.tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT status, error_message FROM jobs WHERE id = :id"),
                {"id": str(job_id)},
            )
        ).mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if row["status"] != "paused_human":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not awaiting answers (status={row['status']})",
        )

    try:
        meta = json.loads(row["error_message"] or "{}")
        questions = meta.get("gap_questions", [])
    except (json.JSONDecodeError, TypeError):
        questions = []

    return GapQuestionsOut(job_id=job_id, status=row["status"], questions=questions)


@router.post("/{job_id}/answers", response_model=AnswersOut, status_code=202)
async def submit_gap_answers(
    job_id: UUID,
    body: AnswersIn,
    request: Request,
    user: AuthUser = Depends(get_current_user),
) -> AnswersOut:
    """
    Submit answers to completeness gap questions.
    All gaps cleared  -> re-queues the Arq job (status: queued).
    Gaps remain       -> persists partial answers (status: paused_human).
    """
    async with get_tenant_session(user.tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT status, spec_id, error_message FROM jobs WHERE id = :id"),
                {"id": str(job_id)},
            )
        ).mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if row["status"] != "paused_human":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not awaiting answers (status={row['status']})",
        )

    spec_data: dict = {}
    stack_profile = "java_spring"
    async with get_tenant_session(user.tenant_id) as session:
        spec_row = (
            await session.execute(
                text("SELECT compiled_spec FROM specs WHERE id = :id"),
                {"id": str(row["spec_id"])},
            )
        ).mappings().one_or_none()
    if spec_row:
        raw = spec_row["compiled_spec"]
        try:
            spec_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (json.JSONDecodeError, TypeError):
            spec_data = {}
        if isinstance(spec_data, dict):
            stack_profile = spec_data.get("stack", {}).get("backend", "java_spring")

    from agents.graphs.nodes.completeness_validator import CompletenessValidator
    remaining = CompletenessValidator().validate(
        spec_data,
        stack_profile=stack_profile,
        existing_answers=body.answers,
    )

    if remaining:
        async with get_tenant_session(user.tenant_id) as session:
            await session.execute(
                text("UPDATE jobs SET error_message = :msg WHERE id = :id"),
                {
                    "msg": json.dumps({
                        "gap_questions": remaining,
                        "partial_answers": body.answers,
                    }),
                    "id": str(job_id),
                },
            )
        return AnswersOut(
            job_id=job_id,
            status="paused_human",
            remaining_questions=remaining,
            requeued=False,
        )

    async with get_tenant_session(user.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE jobs SET status = 'queued', error_message = NULL WHERE id = :id"
            ),
            {"id": str(job_id)},
        )

    await request.app.state.arq_pool.enqueue_job(
        "generate_spec",
        job_id=str(job_id),
        tenant_id=str(user.tenant_id),
        spec_id=str(row["spec_id"]),
    )

    return AnswersOut(
        job_id=job_id,
        status="queued",
        remaining_questions=[],
        requeued=True,
    )


class JobCreateIn(BaseModel):
    spec_id: UUID
    idempotency_key: Optional[str] = None
    initial_answers: Optional[dict[str, str]] = None


class JobCreateOut(BaseModel):
    id: UUID
    tenant_id: UUID
    spec_id: UUID
    status: str
    gap_questions: list[str] = []
    requeued: bool


@router.post("/", response_model=JobCreateOut, status_code=201)
async def create_job(
    body: JobCreateIn,
    request: Request,
    user: AuthUser = Depends(get_current_user),
) -> JobCreateOut:
    """
    Create a new generation job for a compiled spec.
    Validates spec completeness before enqueuing:
    - If gaps found -> status = 'paused_human', gap_questions returned.
    - If complete   -> status = 'queued', enqueued to Arq worker.
    """
    job_id = uuid4()
    idempotency_key = body.idempotency_key or str(job_id)
    initial_answers = body.initial_answers or {}

    # 1. Fetch spec
    spec_data: dict = {}
    stack_profile = "java_spring"
    async with get_tenant_session(user.tenant_id) as session:
        spec_row = (
            await session.execute(
                text("SELECT compiled_spec FROM specs WHERE id = :id"),
                {"id": str(body.spec_id)},
            )
        ).mappings().one_or_none()

    if spec_row is None:
        raise HTTPException(status_code=404, detail="Spec not found")

    raw = spec_row["compiled_spec"]
    try:
        spec_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        spec_data = {}

    if isinstance(spec_data, dict):
        stack_profile = spec_data.get("stack", {}).get("backend", "java_spring")

    # 2. Check completeness
    from agents.graphs.nodes.completeness_validator import CompletenessValidator
    gaps = CompletenessValidator().validate(
        spec_data,
        stack_profile=stack_profile,
        existing_answers=initial_answers,
    )

    if gaps:
        error_msg = json.dumps({
            "gap_questions": gaps,
            "partial_answers": initial_answers,
        })
        async with get_tenant_session(user.tenant_id) as session:
            await session.execute(
                text("""
                    INSERT INTO jobs (id, tenant_id, spec_id, status, job_type, idempotency_key, error_message, created_at)
                    VALUES (:id, :tenant_id, :spec_id, 'paused_human', 'generation', :idempotency_key, :error_message, NOW())
                """),
                {
                    "id": str(job_id),
                    "tenant_id": str(user.tenant_id),
                    "spec_id": str(body.spec_id),
                    "idempotency_key": idempotency_key,
                    "error_message": error_msg,
                },
            )
        return JobCreateOut(
            id=job_id,
            tenant_id=user.tenant_id,
            spec_id=body.spec_id,
            status="paused_human",
            gap_questions=gaps,
            requeued=False,
        )

    # 3. Complete spec -> queue job
    async with get_tenant_session(user.tenant_id) as session:
        await session.execute(
            text("""
                INSERT INTO jobs (id, tenant_id, spec_id, status, job_type, idempotency_key, created_at)
                VALUES (:id, :tenant_id, :spec_id, 'queued', 'generation', :idempotency_key, NOW())
            """),
            {
                "id": str(job_id),
                "tenant_id": str(user.tenant_id),
                "spec_id": str(body.spec_id),
                "idempotency_key": idempotency_key,
            },
        )

    await request.app.state.arq_pool.enqueue_job(
        "generate_spec",
        job_id=str(job_id),
        tenant_id=str(user.tenant_id),
        spec_id=str(body.spec_id),
    )

    return JobCreateOut(
        id=job_id,
        tenant_id=user.tenant_id,
        spec_id=body.spec_id,
        status="queued",
        gap_questions=[],
        requeued=True,
    )
