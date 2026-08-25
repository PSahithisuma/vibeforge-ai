"""
Sandbox Service — Cloud Run
Triggers Cloud Build QA Gate per job and tracks GateReport results.

POST /run    → trigger Cloud Build for a job
GET  /report/{job_id} → fetch GateReport from Cloud Storage
GET  /health  → liveness
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from google.cloud import build_v1, storage
from pydantic import BaseModel

app = FastAPI(title="VibeForge Sandbox")

PROJECT_ID       = os.environ["PROJECT_ID"]
REGION           = os.environ.get("REGION", "us-central1")
ARTIFACTS_BUCKET = os.environ["ARTIFACTS_BUCKET"]
DATABASE_URL     = os.environ["DATABASE_URL"]
API_URL          = os.environ["API_URL"]

# Stack → cloudbuild config file mapping (Contract C12)
# Adding a new stack = add one line here. Zero other changes.
STACK_CONFIGS: dict[str, str] = {
    "java_springboot": "infra/cloud_build/cloudbuild_java.yaml",
    "python_fastapi":  "infra/cloud_build/cloudbuild_python.yaml",
}


# ── Pydantic Models ───────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    job_id:      str
    tenant_id:   str
    artifact_url: str          # GCS URI: gs://vf-artifacts-dev/jobs/{job_id}/app.zip
    stack:       Literal["java_springboot", "python_fastapi"]

class StepResult(BaseModel):
    name:       str
    passed:     bool
    duration_s: float
    detail:     dict = {}

class GateReport(BaseModel):
    job_id:       str
    tenant_id:    str
    stack:        str
    overall:      Literal["pass", "fail"]
    steps:        list[StepResult]
    coverage_pct: float | None = None
    sbom_url:     str | None   = None
    created_at:   datetime


# ── Database Pool ─────────────────────────────────────────────────────────────

pool: asyncpg.Pool | None = None

@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)

@app.on_event("shutdown")
async def shutdown():
    if pool:
        await pool.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "sandbox"}


@app.post("/run", status_code=202)
async def trigger_gate(req: RunRequest):
    """Trigger Cloud Build QA Gate for a job."""
    if req.stack not in STACK_CONFIGS:
        raise HTTPException(400, f"Unknown stack: {req.stack}. Known: {list(STACK_CONFIGS)}")

    cloudbuild_config = STACK_CONFIGS[req.stack]

    # Load cloudbuild YAML from GCS or local path
    client = build_v1.CloudBuildClient()

    # Build substitutions — passed to cloudbuild.yaml as ${_VAR}
    substitutions = {
        "_JOB_ID":           req.job_id,
        "_TENANT_ID":        req.tenant_id,
        "_STACK":            req.stack,
        "_REGION":           REGION,
        "_ARTIFACTS_BUCKET": ARTIFACTS_BUCKET,
        "_ARTIFACT_GCS_URI": req.artifact_url,
    }

    # Read cloudbuild config from repo (Cloud Build fetches from Cloud Source Repos)
    # For simplicity we inline the config filename as a tag on the build
    build = build_v1.Build(
        tags=[f"job-{req.job_id}", f"tenant-{req.tenant_id}", req.stack],
        substitutions=substitutions,
        # Cloud Build reads cloudbuild.yaml from the connected repo
        # In production: wire to Cloud Source Repositories trigger
        # For standalone trigger: pass build steps directly
        source=build_v1.Source(
            storage_source=build_v1.StorageSource(
                bucket=ARTIFACTS_BUCKET,
                object_=f"jobs/{req.job_id}/app.zip",
            )
        ),
    )

    # Submit build
    operation = client.create_build(project_id=PROJECT_ID, build=build)
    build_id = operation.metadata.build.id

    # Record in database
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL app.current_tenant_id = $1", req.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO gate_runs (id, job_id, tenant_id, stack, build_id, status, created_at)
                VALUES ($1, $2, $3, $4, $5, 'running', NOW())
                """,
                str(uuid.uuid4()), req.job_id, req.tenant_id, req.stack, build_id,
            )

    return {"build_id": build_id, "job_id": req.job_id, "status": "running"}


@app.get("/report/{job_id}")
async def get_gate_report(job_id: str, tenant_id: str):
    """Fetch GateReport from Cloud Storage."""
    gcs = storage.Client()
    bucket = gcs.bucket(ARTIFACTS_BUCKET)
    blob = bucket.blob(f"gate-runs/{job_id}/gate_report.json")

    if not blob.exists():
        raise HTTPException(404, "GateReport not found — build may still be running")

    data = json.loads(blob.download_as_text())
    return GateReport(**data)
