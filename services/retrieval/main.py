from __future__ import annotations

"""
VibeForge Retrieval Service
============================
FastAPI microservice that answers RAG queries against Qdrant.

Endpoints:
  POST /retrieve           → query chunks for a tenant (Contract C8: tenant-scoped)
  GET  /health             → liveness check
  GET  /metrics            → Prometheus

Contract C8 — cross-tenant retrieval is structurally impossible:
  Every Qdrant search is issued against the collection for the requesting
  tenant (vibeforge_{tenant_id_nodash}) AND includes a MUST filter on
  tenant_id in the payload. Two separate gates, either alone is sufficient.
"""

import logging
import os
import time

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, start_http_server
from prometheus_fastapi_instrumentator import Instrumentator
from qdrant_client import QdrantClient
from qdrant_client import models as qm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("retrieval")


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


QDRANT_URL   = _env("QDRANT_URL", "http://qdrant:6333")
METRICS_PORT = int(_env("RETRIEVAL_METRICS_PORT", "9002"))
DEFAULT_TOP_K = int(_env("DEFAULT_TOP_K", "5"))
MAX_TOP_K     = int(_env("MAX_TOP_K", "20"))
EMBED_DIM     = 1024  # bge-m3 dense vector dimension


# ──────────────────────────────────────────────────────────────────────────────
# Prometheus
# Guard each metric against duplicate registration — happens when the container
# crashes and uvicorn restarts the process within the same Python runtime.
# ──────────────────────────────────────────────────────────────────────────────
from prometheus_client import REGISTRY  # noqa: E402

def _counter(name, doc, labels=()):
    try:
        return Counter(name, doc, list(labels))
    except ValueError:
        return REGISTRY._names_to_collectors.get(name)

def _histogram(name, doc, buckets=None):
    kwargs = {"buckets": buckets} if buckets else {}
    try:
        return Histogram(name, doc, **kwargs)
    except ValueError:
        return REGISTRY._names_to_collectors.get(name)

RETRIEVE_REQUESTS = _counter(
    "vibeforge_retrieval_requests_total",
    "Total retrieval requests",
    ["tenant_id", "result"],
)
RETRIEVE_DURATION = _histogram(
    "vibeforge_retrieval_duration_seconds",
    "End-to-end latency of a retrieval call",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)
CHUNKS_RETURNED = _counter(
    "vibeforge_retrieval_chunks_returned_total",
    "Total chunks returned across all retrieval requests",
)


# ──────────────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────────────
class RetrieveRequest(BaseModel):
    query:     str  = Field(..., min_length=1, max_length=2000, description="Natural-language query")
    tenant_id: str  = Field(..., description="Tenant UUID (injected by caller from JWT)")
    top_k:     int  = Field(DEFAULT_TOP_K, ge=1, le=MAX_TOP_K, description="Number of chunks to return")


class RetrievedChunk(BaseModel):
    text:      str
    score:     float
    filename:  str
    chunk_idx: int
    doc_id:    str


class RetrieveResponse(BaseModel):
    query:     str
    tenant_id: str
    chunks:    list[RetrievedChunk]
    latency_ms: float


# ──────────────────────────────────────────────────────────────────────────────
# Retriever (singleton loaded at startup)
# ──────────────────────────────────────────────────────────────────────────────
class Retriever:
    """
    Loads bge-m3 once at startup, holds a Qdrant client, answers queries.
    All retrieval is tenant-scoped (Contract C8).
    """

    def __init__(self) -> None:
        logger.info("Loading BAAI/bge-m3 model…")
        from FlagEmbedding import BGEM3FlagModel  # noqa: PLC0415
        self.model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
        logger.info("bge-m3 loaded ✓")

        self.qdrant = QdrantClient(url=QDRANT_URL)
        logger.info("Qdrant client ready at %s ✓", QDRANT_URL)

    def _collection(self, tenant_id: str) -> str:
        return f"vibeforge_{tenant_id.replace('-', '')}"

    def retrieve(self, query: str, tenant_id: str, top_k: int = DEFAULT_TOP_K) -> list[RetrievedChunk]:
        """
        Embed the query with bge-m3, search Qdrant with two-layer tenant isolation:
          Layer 1 — collection is tenant-specific (vibeforge_{tenant_id_nodash})
          Layer 2 — MUST filter on tenant_id payload field (Contract C8)
        Returns up to top_k results sorted by cosine similarity (descending).
        """
        coll = self._collection(tenant_id)

        # Check collection exists (tenant may have no documents yet)
        existing = {c.name for c in self.qdrant.get_collections().collections}
        if coll not in existing:
            logger.info("No collection for tenant %s — returning empty", tenant_id)
            return []

        # Embed query
        dense = self.model.encode([query], max_length=512)["dense_vecs"][0]

        # Search — MUST filter enforces Contract C8 at Qdrant layer
        results = self.qdrant.search(
            collection_name=coll,
            query_vector=dense.tolist(),
            query_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="tenant_id",
                        match=qm.MatchValue(value=tenant_id),
                    )
                ]
            ),
            limit=top_k,
            with_payload=True,
            score_threshold=0.3,   # discard very low-similarity results
        )

        return [
            RetrievedChunk(
                text=r.payload.get("text", ""),
                score=round(r.score, 4),
                filename=r.payload.get("filename", ""),
                chunk_idx=r.payload.get("chunk_idx", 0),
                doc_id=r.payload.get("doc_id", ""),
            )
            for r in results
        ]


# Module-level singleton — populated in lifespan
_retriever: Retriever | None = None


# ──────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _retriever
    start_http_server(METRICS_PORT)
    logger.info("Prometheus metrics on port %d", METRICS_PORT)
    _retriever = Retriever()
    logger.info("Retrieval service ready ✓")
    yield
    logger.info("Retrieval service shutdown")


app = FastAPI(
    title="VibeForge Retrieval Service",
    description="Tenant-scoped RAG retrieval — Contract C8",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

Instrumentator(
    excluded_handlers=["/metrics", "/health"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "retrieval"}


@app.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(body: RetrieveRequest) -> RetrieveResponse:
    """
    Retrieve the top-k most relevant document chunks for a query,
    scoped strictly to the requesting tenant (Contract C8).

    The caller (API gateway) is responsible for extracting tenant_id
    from the JWT and passing it here. Never trust tenant_id from an
    unauthenticated external caller.
    """
    if _retriever is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retriever not initialised",
        )

    t0 = time.monotonic()
    try:
        chunks = _retriever.retrieve(
            query=body.query,
            tenant_id=body.tenant_id,
            top_k=body.top_k,
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        RETRIEVE_REQUESTS.labels(tenant_id=body.tenant_id, result="ok").inc()
        RETRIEVE_DURATION.observe((time.monotonic() - t0))
        CHUNKS_RETURNED.inc(len(chunks))

        return RetrieveResponse(
            query=body.query,
            tenant_id=body.tenant_id,
            chunks=chunks,
            latency_ms=elapsed_ms,
        )

    except Exception as exc:
        RETRIEVE_REQUESTS.labels(tenant_id=body.tenant_id, result="error").inc()
        logger.exception("Retrieval error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Retrieval failed",
        ) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
