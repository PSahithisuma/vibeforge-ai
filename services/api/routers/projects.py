from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from core.auth import AuthUser, get_current_user
from core.database import get_tenant_session

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    tenant_id: UUID


class GenerateOut(BaseModel):
    job_id: UUID
    project_id: UUID
    idempotency_key: str
    status: str = "queued"
    stream_url: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    user: AuthUser = Depends(get_current_user),
) -> ProjectOut:
    """Create a new project under the authenticated tenant."""
    async with get_tenant_session(user.tenant_id) as session:
        row = (
            await session.execute(
                text("""
                    INSERT INTO projects (tenant_id, owner_id, name, description)
                    VALUES (:tenant_id, :owner_id, :name, :description)
                    RETURNING id, name, description, tenant_id
                """),
                {
                    "tenant_id": str(user.tenant_id),
                    "owner_id": str(user.id),
                    "name": body.name,
                    "description": body.description,
                },
            )
        ).mappings().one()

    return ProjectOut(**row)


@router.get("/", response_model=list[ProjectOut])
async def list_projects(
    user: AuthUser = Depends(get_current_user),
) -> list[ProjectOut]:
    """List all projects for the authenticated tenant (RLS enforced)."""
    async with get_tenant_session(user.tenant_id) as session:
        rows = (
            await session.execute(
                text("""
                    SELECT id, name, description, tenant_id
                    FROM projects
                    ORDER BY created_at DESC
                    LIMIT 100
                """),
            )
        ).mappings().all()

    return [ProjectOut(**r) for r in rows]


@router.post(
    "/{project_id}/generate",
    response_model=GenerateOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_generation(
    project_id: UUID,
    request: Request,
    user: AuthUser = Depends(get_current_user),
) -> GenerateOut:
    """
    Freeze a stub spec and enqueue a generation job.
    Returns the job_id and the SSE stream URL to poll for progress.

    Phase 0: spec is a JSON stub — no LLM involved.
    The Arq worker picks up the job and emits 5 progress events over ~10 s.
    """
    async with get_tenant_session(user.tenant_id) as session:
        # 404 if project not in this tenant (RLS also blocks it, but 404 > 403)
        project = (
            await session.execute(
                text("SELECT id FROM projects WHERE id = :id"),
                {"id": str(project_id)},
            )
        ).scalar_one_or_none()

        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        # Create a stub ApplicationSpec (Phase 0: empty schema envelope)
        spec_id = (
            await session.execute(
                text("""
                    INSERT INTO application_specs
                        (tenant_id, project_id, created_by, version, spec_data, frozen_at)
                    VALUES
                        (:tenant_id, :project_id, :created_by,
                         1, '{"schema_version": "1.0"}'::jsonb, NOW())
                    RETURNING id
                """),
                {
                    "tenant_id": str(user.tenant_id),
                    "project_id": str(project_id),
                    "created_by": str(user.id),
                },
            )
        ).scalar_one()

        # Idempotency key — same spec + version never spawns a second Arq job
        idempotency_key = f"gen-{spec_id}-v1"

        # Insert job row (queued state — Arq hasn't picked it up yet)
        job_id = (
            await session.execute(
                text("""
                    INSERT INTO jobs (tenant_id, spec_id, idempotency_key, job_type, status)
                    VALUES (:tenant_id, :spec_id, :ikey, 'generation', 'queued')
                    ON CONFLICT (idempotency_key) DO UPDATE SET status = jobs.status
                    RETURNING id
                """),
                {
                    "tenant_id": str(user.tenant_id),
                    "spec_id": str(spec_id),
                    "ikey": idempotency_key,
                },
            )
        ).scalar_one()

    # Enqueue Arq job — _job_id = idempotency_key prevents double-dispatch
    await request.app.state.arq_pool.enqueue_job(
        "run_dummy_job",
        _job_id=idempotency_key,
        job_id=str(job_id),
        tenant_id=str(user.tenant_id),
    )

    base_url = str(request.base_url).rstrip("/")
    return GenerateOut(
        job_id=job_id,
        project_id=project_id,
        idempotency_key=idempotency_key,
        stream_url=f"{base_url}/api/v1/jobs/{job_id}/stream",
    )
