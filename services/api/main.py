from __future__ import annotations

import contextlib
import logging

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.metrics import setup_metrics
from core.redis_client import close_redis
from routers import health, jobs, projects, specs, stream

settings = get_settings()

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
log = structlog.get_logger()


# ---------------------------------------------------------------------------
# App lifespan — startup / shutdown
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("vibeforge_api_starting", env=settings.API_ENV)

    # Arq pool — used to enqueue jobs via app.state.arq_pool.enqueue_job(...)
    app.state.arq_pool = await create_pool(
        RedisSettings(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            database=0,
        )
    )
    log.info("arq_pool_ready", redis_host=settings.REDIS_HOST)

    yield  # ← app is running

    await app.state.arq_pool.aclose()
    await close_redis()
    log.info("vibeforge_api_stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="VibeForge API",
    version="0.1.0",
    description=(
        "VibeForge platform — tenant-isolated async job pipeline, "
        "spec management, and real-time SSE observability."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    redirect_slashes=False,   # prevent 307 that strips Authorization on redirect
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # Tighten to known origins in Phase 1
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(health.router)
app.include_router(projects.router)
app.include_router(specs.router)
app.include_router(jobs.router)
app.include_router(stream.router)

# Prometheus /metrics — must be called AFTER routers are included
setup_metrics(app)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": "vibeforge-api",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }
