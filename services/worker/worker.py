from __future__ import annotations

import asyncio
import logging
import os

import asyncpg
from arq import run_worker
from arq.connections import RedisSettings
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from tasks.dummy import run_dummy_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("vibeforge.worker")


# ---------------------------------------------------------------------------
# Read config from environment (same vars as API service via docker-compose)
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


_DB_URL = _env(
    "DATABASE_URL",
    "postgresql+asyncpg://vibeforge:vibeforge_dev_secret@postgres:5432/vibeforge",
)
# asyncpg create_pool uses postgresql:// — strip the SQLAlchemy prefix
_PG_DSN = _DB_URL.replace("postgresql+asyncpg://", "postgresql://")

_REDIS_HOST     = _env("REDIS_HOST", "redis")
_REDIS_PORT     = int(_env("REDIS_PORT", "6379"))
_REDIS_PASSWORD = _env("REDIS_PASSWORD", "redis_dev_secret")
_METRICS_PORT   = int(_env("WORKER_METRICS_PORT", "9000"))

# =============================================================================
# Prometheus worker metrics
# =============================================================================

WORKER_ACTIVE_JOBS = Gauge(
    "vibeforge_worker_active_jobs",
    "Number of jobs currently being processed by this worker",
)

WORKER_JOB_DURATION = Histogram(
    "vibeforge_worker_job_duration_seconds",
    "Time taken to process a job end-to-end",
    ["job_type", "status"],
    buckets=[1, 2, 5, 10, 30, 60, 120, 300],
)

WORKER_JOBS_PROCESSED = Counter(
    "vibeforge_worker_jobs_processed_total",
    "Total jobs processed by this worker",
    ["job_type", "status"],
)


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

async def startup(ctx: dict) -> None:
    """
    Create a shared asyncpg connection pool available to all task functions
    via ctx["db_pool"]. Called once when the Arq worker process starts.
    Also starts the Prometheus metrics HTTP server on WORKER_METRICS_PORT.
    """
    logger.info("worker startup: starting Prometheus metrics server on port %d", _METRICS_PORT)
    start_http_server(_METRICS_PORT)

    logger.info("worker startup: connecting to Postgres…")
    ctx["db_pool"] = await asyncpg.create_pool(
        dsn=_PG_DSN,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    # Expose metrics references in ctx so tasks can update them
    ctx["active_jobs_gauge"]   = WORKER_ACTIVE_JOBS
    ctx["job_duration_hist"]   = WORKER_JOB_DURATION
    ctx["jobs_processed_ctr"]  = WORKER_JOBS_PROCESSED
    logger.info("worker startup: ready")


async def shutdown(ctx: dict) -> None:
    """Close the DB pool cleanly on Ctrl-C or SIGTERM."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool:
        await pool.close()
    logger.info("worker shutdown: pool closed")


# ---------------------------------------------------------------------------
# Arq WorkerSettings — the entry point Arq reads
# ---------------------------------------------------------------------------

class WorkerSettings:
    functions      = [run_dummy_job]
    redis_settings = RedisSettings(
        host=_REDIS_HOST,
        port=_REDIS_PORT,
        password=_REDIS_PASSWORD,
        database=0,
    )
    on_startup  = startup
    on_shutdown = shutdown
    max_jobs    = 10
    job_timeout = 300   # 5-minute hard limit per job
    keep_result = 3600  # keep Arq result key in Redis for 1 hour


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_worker(WorkerSettings))
