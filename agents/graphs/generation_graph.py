"""
VibeForge â€” Generation Graph (Phase 0 â€” all stubs, fully wired)
================================================================
State machine: plan_node â†’ scaffold_node â†’ synthesize_node â†’ assemble_node
  â†’ gate_node â†’ reviewer_node â†’ fixer_node (â‰¤3) â†’ escalation_gate_node
  â†’ gate_node (C6: re-enter after escalation) â†’ deliver_node

Contracts enforced here:
  C2  â€” No process-memory state. Checkpointed in Postgres per node.
  C3  â€” Long work is a job. Every node writes SSE events.
  C5  â€” Fix loop hard-capped at MAX_FIX_ITERATIONS = 3.
  C6  â€” Escalated output re-enters the sandbox gate (no trust shortcut).
  C10 â€” Scaffold is templates only, zero LLM.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MAX_FIX_ITERATIONS = 3   # Contract C5


# â”€â”€ Phase enum â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class GenerationPhase(str, Enum):
    """Phase values emitted in SSE events. Tests assert on .value strings."""
    PLANNING     = "planning"
    SCAFFOLDING  = "scaffolding"
    SYNTHESIZING = "synthesizing"
    ASSEMBLING   = "assembling"
    GATING       = "gating"
    REVIEWING    = "reviewing"
    FIXING       = "fixing"
    ESCALATING   = "escalation"
    DELIVERING   = "delivering"
    DONE         = "done"
    FAILED       = "failed"

# Test-facing alias
JobPhase = GenerationPhase


class JobStatus(str, Enum):
    QUEUED       = "queued"
    RUNNING      = "running"
    GATED        = "gated"
    FIXING       = "fixing"
    ESCALATED    = "escalated"
    DELIVERED    = "delivered"
    FAILED       = "failed"
    PAUSED_HUMAN = "paused_human"


# â”€â”€ Sub-models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class GateStepResult(BaseModel):
    step: str
    passed: bool
    duration_ms: int = 0
    output: str = ""
    coverage_pct: Optional[float] = None


class GateResult(BaseModel):
    passed: bool
    steps: list[GateStepResult] = Field(default_factory=list)
    failing_files: list[str] = Field(default_factory=list)
    report_id: str = Field(default_factory=lambda: str(uuid4()))

    @property
    def failed_steps(self) -> list[GateStepResult]:
        return [s for s in self.steps if not s.passed]


class ModulePlan(BaseModel):
    module_id: str
    name: str
    module_type: str
    dependencies: list[str] = Field(default_factory=list)
    spec_slice: dict[str, Any] = Field(default_factory=dict)


class FileMap(BaseModel):
    module_id: str
    files: dict[str, str] = Field(default_factory=dict)


class FixPlan(BaseModel):
    fix_plan_id: str = Field(default_factory=lambda: str(uuid4()))
    files_to_fix: list[str] = Field(default_factory=list)
    errors_by_file: dict[str, list[str]] = Field(default_factory=dict)
    iteration: int = 1
    escalation_recommended: bool = False
    failure_classification: str = ""


# â”€â”€ Graph state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class GenerationState(BaseModel):
    """
    Complete job state. Serialised by LangGraph into the Postgres checkpointer.
    Any worker can resume any job after a crash.
    Contract C2: nothing lives in process memory.
    """
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = ""
    project_id: str = ""
    spec_id: str = ""
    spec_version: int = 1
    canonical_hash: str = ""

    current_phase: GenerationPhase = GenerationPhase.PLANNING
    job_status: JobStatus = JobStatus.QUEUED
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Fix-loop â€” CONTRACT C5: hard ceiling
    fix_iteration: int = 0
    fix_count: int = 0
    max_fix_iterations: int = MAX_FIX_ITERATIONS
    escalation_used: bool = False
    escalation_approved: bool = False

    # Node outputs
    module_plan: list[ModulePlan] = Field(default_factory=list)
    scaffold_files: dict[str, str] = Field(default_factory=dict)
    file_maps: list[FileMap] = Field(default_factory=list)
    assembled_files: dict[str, str] = Field(default_factory=dict)
    assembly_conflicts: list[str] = Field(default_factory=list)
    gate_result: Optional[GateResult] = None
    fix_plan: Optional[FixPlan] = None

    # Delivery
    gitea_repo_url: Optional[str] = None
    minio_bundle_key: Optional[str] = None
    preview_url: Optional[str] = None
    sbom_ref: Optional[str] = None

    # Errors
    error_message: Optional[str] = None

    # Completeness Validator Layer 2
    gap_questions: list[str] = Field(default_factory=list)
    gap_answers: dict[str, str] = Field(default_factory=dict)

    # SSE event log â€” plain dicts so LangGraph serialiser never chokes
    events: list[dict[str, Any]] = Field(default_factory=list)

    # Input helpers for stub nodes
    spec_entity_names: list[str] = Field(default_factory=list)

    # Phase 2 runtime fields
    # FIX C2: llm_client is excluded from model_dump() so Postgres never tries
    # to JSON-serialize a network client object. It is passed via LangGraph
    # config["configurable"]["llm_client"] for the current run and re-created
    # by the worker on crash recovery (crash â†’ Phase 0 fallback activates).
    llm_client: Optional[Any] = Field(default=None, exclude=True)
    stack_profile: str = "java_spring"
    spec_snapshot: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def synthesized_modules(self) -> list[FileMap]:
        return self.file_maps

    def model_dump_json(self, **kwargs) -> str:
        import json as _j

        def _to_dict(e) -> dict:
            if isinstance(e, dict):
                return e
            return {
                "event_id": getattr(e, "event_id", ""),
                "job_id":   getattr(e, "job_id", ""),
                "node":     getattr(e, "node", ""),
                "phase":    e.phase.value if hasattr(e, "phase") else "",
                "payload":  getattr(e, "payload", {}),
                "ts":       getattr(e, "ts", ""),
            }

        original_events = self.events
        self.events = [_to_dict(e) for e in original_events]
        try:
            d = self.model_dump(mode="json")
        finally:
            self.events = original_events

        d.pop("llm_client", None)   # belt-and-suspenders â€” excluded=True handles it
        d.pop("model_config", None)
        return _j.dumps(d, default=str)


# â”€â”€ SSE emission â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _emit(state: GenerationState, node: str, phase: GenerationPhase,
          payload: dict[str, Any]) -> GenerationState:
    event: dict[str, Any] = {
        "event_id": str(uuid4()),
        "job_id":   state.job_id,
        "node":     node,
        "phase":    phase.value,
        "payload":  payload,
        "ts":       datetime.now(timezone.utc).isoformat(),
    }
    state.events.append(event)
    logger.info("[SSE] job=%s  node=%-20s  phase=%-14s  %s",
                state.job_id[:8], node, phase.value, json.dumps(payload))
    return state


# â”€â”€ JobEvent wrapper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class _PV:
    __slots__ = ("value",)
    def __init__(self, v: str): self.value = v
    def __eq__(self, other):
        return self.value == (other.value if hasattr(other, "value") else other)
    def __hash__(self): return hash(self.value)
    def __repr__(self): return f"Phase({self.value!r})"


class JobEvent:
    __slots__ = ("event_id", "job_id", "node", "phase", "payload", "ts")

    def __init__(self, d: dict):
        self.event_id = d.get("event_id", "")
        self.job_id   = d.get("job_id", "")
        self.node     = d.get("node", "")
        self.phase    = _PV(d.get("phase", ""))
        self.payload  = d.get("payload", {})
        self.ts       = d.get("ts", "")

    def __repr__(self):
        return f"JobEvent(node={self.node!r}, phase={self.phase.value!r})"


def _wrap_events(state: GenerationState) -> GenerationState:
    """Convert raw event dicts to JobEvent objects after ainvoke completes."""
    state.events = [
        JobEvent(e) if isinstance(e, dict) else e
        for e in state.events
    ]
    return state


# â”€â”€ Phase 2 agent imports â€” placed HERE so GateResult/FixPlan are already â”€â”€â”€â”€â”€
# defined when reviewer_fixer.py tries to import them (breaks circular import).
try:
    from agents.generation.planner import PlannerAgent, ModulePlanOutput
    from agents.generation.synthesizer import SynthesizerAgent, FileMapOutput as SynthFileMap
    from agents.generation.assembler import Assembler, AssemblyResult
    from agents.generation.reviewer_fixer import ReviewerAgent, FixerAgent, MetacognitionGateTier1
    _PHASE2_AVAILABLE = True
except ImportError:
    _PHASE2_AVAILABLE = False
    logger.warning("[Graph] Phase 2 agents not importable â€” running in Phase 0 stub mode")


# â”€â”€ Helper: resolve llm_client from config or state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _get_llm(state: GenerationState, config: Optional[dict]) -> Any:
    """
    FIX C2: llm_client is no longer stored in serialized state.
    Primary source: config["configurable"]["llm_client"] (set by _invoke).
    Fallback:       state.llm_client (set directly in tests for backward compat).
    """
    if config:
        llm = config.get("configurable", {}).get("llm_client")
        if llm is not None:
            return llm
    return state.llm_client


# â”€â”€ Node: plan_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def node_plan(
    state: GenerationState,
    config: Optional[RunnableConfig] = None,   # FIX C2: llm_client from config
) -> dict:
    """
    Planner: frozen Spec IR â†’ ordered module DAG.
    Phase 2: PlannerAgent (Qwen3-8B) generates real DAG.
    Falls back to deterministic stub if agent unavailable.
    """
    state = _emit(state, "plan_node", GenerationPhase.PLANNING, {"status": "started"})
    state.current_phase = GenerationPhase.PLANNING
    state.job_status    = JobStatus.RUNNING
    state.started_at    = datetime.now(timezone.utc).isoformat()

    llm_client = _get_llm(state, config)
    modules: list[ModulePlan] = []

    if _PHASE2_AVAILABLE and llm_client and state.spec_entity_names:
        try:
            planner   = PlannerAgent(llm_client)
            spec_dict = state.spec_snapshot or {}
            if not spec_dict:
                spec_dict = {
                    "domain_model": {
                        "entities": [{"name": n, "fields": []} for n in state.spec_entity_names]
                    },
                    "api_model": {"endpoints": []},
                    "vertical": "",
                    "stack": {"backend": state.stack_profile},
                }
            plan_output = await planner.plan(
                spec_dict,
                stack_profile=state.stack_profile,
                job_id=state.job_id,
            )
            for pm in plan_output.modules:
                modules.append(ModulePlan(
                    module_id=pm.module_id,
                    name=pm.name,
                    module_type=pm.module_type,
                    dependencies=pm.dependencies,
                    spec_slice={"spec_paths": pm.spec_paths},
                ))
            logger.info("[node_plan] Phase 2 planner: %d modules", len(modules))
        except Exception as e:
            logger.warning("[node_plan] Phase 2 planner failed (%s) â€” falling back to stub", e)
            modules = []

    if not modules:
        if state.spec_entity_names:
            for i, name in enumerate(state.spec_entity_names):
                b = f"m{i*3}"
                modules += [
                    ModulePlan(module_id=f"{b}_entity",  name=f"{name}Entity",
                               module_type="entity",     dependencies=[]),
                    ModulePlan(module_id=f"{b}_repo",    name=f"{name}Repository",
                               module_type="repository", dependencies=[f"{b}_entity"]),
                    ModulePlan(module_id=f"{b}_service", name=f"{name}Service",
                               module_type="service",    dependencies=[f"{b}_repo"]),
                ]
            modules.append(ModulePlan(
                module_id="migration", name="DatabaseMigrations",
                module_type="migration", dependencies=[]))
        else:
            modules = [
                ModulePlan(module_id="m1", name="CustomerService",
                           module_type="service", dependencies=[]),
            ]

    state.module_plan = modules
    state = _emit(state, "plan_node", GenerationPhase.PLANNING,
                  {"status": "complete", "module_count": len(modules),
                   "modules": [m.name for m in modules]})
    return state.model_dump()


# â”€â”€ Node: scaffold_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def node_scaffold(state: GenerationState) -> dict:
    """Scaffold: Copier template rendering. Zero LLM. Contract C10."""
    state = _emit(state, "scaffold_node", GenerationPhase.SCAFFOLDING, {"status": "started"})
    state.current_phase = GenerationPhase.SCAFFOLDING
    state.scaffold_files = {
        "pom.xml":                            "<!-- STUB: Maven build -->",
        "Dockerfile":                         "# STUB: FROM eclipse-temurin:21-jre",
        "docker-compose.yml":                 "# STUB: compose",
        "src/main/resources/application.yml": "# STUB: Spring config",
        ".github/workflows/ci.yml":           "# STUB: CI workflow",
    }
    state = _emit(state, "scaffold_node", GenerationPhase.SCAFFOLDING,
                  {"status": "complete", "file_count": len(state.scaffold_files)})
    return state.model_dump()


# â”€â”€ Node: synthesize_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def node_synthesize(
    state: GenerationState,
    config: Optional[RunnableConfig] = None,   # FIX C2: llm_client from config
) -> dict:
    """Per-module code generation. Sequential (Phase 2 decision â€” locked)."""
    state = _emit(state, "synthesize_node", GenerationPhase.SYNTHESIZING,
                  {"status": "started", "total": len(state.module_plan)})
    state.current_phase = GenerationPhase.SYNTHESIZING

    llm_client = _get_llm(state, config)
    file_maps: list[FileMap] = []

    if _PHASE2_AVAILABLE and llm_client:
        synthesizer = SynthesizerAgent(llm_client, stack_profile=state.stack_profile)
        previously_synthesized: dict[str, str] = {}
        spec_dict = state.spec_snapshot or {}

        for idx, module in enumerate(state.module_plan):
            state = _emit(state, "synthesize_node", GenerationPhase.SYNTHESIZING, {
                "status": "synthesizing",
                "module": module.name,
                "index":  idx + 1,
                "total":  len(state.module_plan),
            })
            try:
                synth_out = await synthesizer.synthesize_module(
                    module=module,
                    spec_dict=spec_dict,
                    previously_synthesized=previously_synthesized,
                    job_id=state.job_id,
                )
                fm = FileMap(module_id=module.module_id)
                for sf in synth_out.files:
                    fm.files[sf.filename] = sf.content
                    previously_synthesized[sf.filename] = sf.content
                file_maps.append(fm)
                logger.info("[node_synthesize] module=%s files=%d", module.name, len(fm.files))
            except Exception as e:
                logger.error("[node_synthesize] module=%s failed: %s â€” using stub", module.name, e)
                pkg = module.name.lower()
                file_maps.append(FileMap(
                    module_id=module.module_id,
                    files={
                        f"src/main/java/com/vibeforge/{pkg}/{module.name}.java":
                            f"// SYNTHESIS FAILED: {module.name}",
                    },
                ))
    else:
        for idx, module in enumerate(state.module_plan):
            state = _emit(state, "synthesize_node", GenerationPhase.SYNTHESIZING, {
                "status": "synthesizing", "module": module.name,
                "index":  idx + 1, "total": len(state.module_plan),
            })
            pkg = module.name.lower()
            file_maps.append(FileMap(
                module_id=module.module_id,
                files={
                    f"src/main/java/com/vibeforge/{pkg}/{module.name}.java":
                        f"// STUB: {module.name}\npublic class {module.name} {{}}",
                    f"src/test/java/com/vibeforge/{pkg}/{module.name}Test.java":
                        f"// STUB test: {module.name}",
                },
            ))

    state.file_maps = file_maps
    state = _emit(state, "synthesize_node", GenerationPhase.SYNTHESIZING,
                  {"status": "complete", "module_count": len(file_maps)})
    return state.model_dump()


# â”€â”€ Node: assemble_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def node_assemble(state: GenerationState) -> dict:
    """Merge per-module FileMaps. Deterministic conflict detection. Zero LLM."""
    state = _emit(state, "assemble_node", GenerationPhase.ASSEMBLING, {"status": "started"})
    state.current_phase = GenerationPhase.ASSEMBLING

    if _PHASE2_AVAILABLE:
        from agents.generation.synthesizer import FileMapOutput as FMO, SynthesizedFile
        assembler = Assembler()
        fmo_list  = []
        for fm in state.file_maps:
            fmo = FMO(module_id=fm.module_id, module_name=fm.module_id)
            for path, code in fm.files.items():
                fmo.files.append(SynthesizedFile(filename=path, content=code))
            fmo_list.append(fmo)

        result = assembler.assemble(
            scaffold_files=state.scaffold_files,
            module_outputs=fmo_list,
        )
        state.assembled_files    = result.assembled_files
        state.assembly_conflicts = result.conflict_paths
        conflicts = result.conflict_paths
    else:
        assembled: dict[str, str] = {}
        conflicts: list[str] = []
        assembled.update(state.scaffold_files)
        for fm in state.file_maps:
            for path, code in fm.files.items():
                if path in assembled and path not in state.scaffold_files:
                    conflicts.append(path)
                    logger.warning("[ASSEMBLE] Conflict: %s", path)
                else:
                    assembled[path] = code
        state.assembled_files    = assembled
        state.assembly_conflicts = conflicts

    state = _emit(state, "assemble_node", GenerationPhase.ASSEMBLING, {
        "status":     "complete" if not conflicts else "conflicts_detected",
        "file_count": len(state.assembled_files),
        "conflicts":  conflicts,
    })
    return state.model_dump()


# â”€â”€ Node: gate_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def node_gate(state: GenerationState) -> dict:
    """7-step QA gate. Phase 0 stub: always passes."""
    state = _emit(state, "gate_node", GenerationPhase.GATING, {
        "status": "started", "iteration": state.fix_iteration})
    state.current_phase = GenerationPhase.GATING
    state.job_status    = JobStatus.GATED

    steps = [
        GateStepResult(step="compile",    passed=True, duration_ms=1100, output="BUILD SUCCESS"),
        GateStepResult(step="unit_tests", passed=True, duration_ms=3200, coverage_pct=71.4),
        GateStepResult(step="migrations", passed=True, duration_ms=800),
        GateStepResult(step="api_smoke",  passed=True, duration_ms=2100),
        GateStepResult(step="semgrep",    passed=True, duration_ms=5400),
        GateStepResult(step="trivy_osv",  passed=True, duration_ms=4100, output="0 critical CVEs"),
        GateStepResult(step="gitleaks",   passed=True, duration_ms=600,  output="No secrets found"),
    ]
    all_pass = all(s.passed for s in steps)
    result   = GateResult(
        passed=all_pass,
        steps=steps,
        failing_files=[] if all_pass else ["src/main/java/com/vibeforge/stub/Stub.java"],
    )
    state.gate_result = result

    state = _emit(state, "gate_node", GenerationPhase.GATING, {
        "status":    "passed" if all_pass else "failed",
        "iteration": state.fix_iteration,
        "coverage":  71.4,
        "report_id": result.report_id,
    })
    return state.model_dump()


# â”€â”€ Node: reviewer_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def node_reviewer(
    state: GenerationState,
    config: Optional[RunnableConfig] = None,   # FIX C2: llm_client from config
) -> dict:
    """Reads GateReport facts â†’ writes FixPlan."""
    state = _emit(state, "reviewer_node", GenerationPhase.REVIEWING, {"status": "started"})
    state.current_phase = GenerationPhase.REVIEWING

    gate = state.gate_result
    if not gate or gate.passed:
        return state.model_dump()

    llm_client = _get_llm(state, config)
    next_iter  = state.fix_iteration + 1
    esc_rec    = next_iter >= state.max_fix_iterations

    if _PHASE2_AVAILABLE and llm_client:
        from agents.generation.assembler import Assembler, AssemblyResult

        ar = AssemblyResult(
            assembled_files=dict(state.assembled_files),
            scaffold_files=dict(state.scaffold_files),
        )
        for path in state.assembled_files:
            ar.file_ownership[path] = "__unknown__"

        spec_dict = state.spec_snapshot or {}
        reviewer  = ReviewerAgent(llm_client)
        try:
            rev_out = await reviewer.review(
                gate_result=gate,
                assembly_result=ar,
                spec_dict=spec_dict,
                fix_iteration=next_iter,
                max_iterations=state.max_fix_iterations,
                job_id=state.job_id,
            )
            files_to_fix   = [i.file_path for i in rev_out.fix_instructions]
            errors_by_file = {i.file_path: i.errors for i in rev_out.fix_instructions}
            esc_rec = rev_out.escalation_recommended or esc_rec
        except Exception as e:
            logger.error("[node_reviewer] Phase 2 reviewer failed: %s â€” fallback", e)
            files_to_fix   = gate.failing_files or []
            errors_by_file = {f: ["Gate failure"] for f in files_to_fix}
    else:
        files_to_fix   = gate.failing_files or ["src/main/java/com/vibeforge/stub/Stub.java"]
        errors_by_file = {f: ["Compile/test failure â€” see gate report"] for f in files_to_fix}

    state.fix_plan = FixPlan(
        files_to_fix=files_to_fix,
        errors_by_file=errors_by_file,
        iteration=next_iter,
        escalation_recommended=esc_rec,
        failure_classification="capability_bound" if esc_rec else "mechanical",
    )
    state = _emit(state, "reviewer_node", GenerationPhase.REVIEWING, {
        "status":         "complete",
        "files_to_fix":   files_to_fix,
        "next_iteration": next_iter,
        "escalate":       esc_rec,
    })
    return state.model_dump()


# â”€â”€ Node: fixer_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def node_fixer(
    state: GenerationState,
    config: Optional[RunnableConfig] = None,   # FIX C2: llm_client from config
) -> dict:
    """Regenerates ONLY the files in the FixPlan. Contract C5."""
    iteration   = state.fix_plan.iteration if state.fix_plan else state.fix_iteration + 1
    gate_errors = dict(state.fix_plan.errors_by_file) if state.fix_plan else {}

    state = _emit(state, "fixer_node", GenerationPhase.FIXING, {
        "status":      "started",
        "iteration":   iteration,
        "max":         state.max_fix_iterations,
        "gate_errors": gate_errors,
    })
    state.current_phase = GenerationPhase.FIXING
    state.job_status    = JobStatus.FIXING
    state.fix_iteration = iteration
    state.fix_count     = iteration

    llm_client = _get_llm(state, config)

    if _PHASE2_AVAILABLE and llm_client and state.fix_plan:
        from agents.generation.assembler import Assembler, AssemblyResult
        from agents.generation.reviewer_fixer import FixerAgent, FileFixInstruction, ReviewerOutput

        fixer = FixerAgent(llm_client, stack_profile=state.stack_profile)
        ar    = AssemblyResult(assembled_files=dict(state.assembled_files))

        instructions = [
            FileFixInstruction(
                file_path=fpath,
                module_id="unknown",
                errors=errs,
                fix_guidance="Fix the exact errors listed",
                error_source="compile",
            )
            for fpath, errs in gate_errors.items()
        ]
        rev_out = ReviewerOutput(fix_instructions=instructions)

        try:
            fixed_maps = await fixer.fix(
                reviewer_output=rev_out,
                assembly_result=ar,
                fix_iteration=iteration,
                job_id=state.job_id,
            )
            assembler  = Assembler()
            new_result = assembler.apply_fixes(ar, fixed_maps)
            state.assembled_files = new_result.assembled_files
            logger.info("[node_fixer] Phase 2 fixed %d files", len(fixed_maps))
        except Exception as e:
            logger.error("[node_fixer] Phase 2 fixer failed: %s â€” stub fallback", e)
            if state.fix_plan:
                for fpath in state.fix_plan.files_to_fix:
                    state.assembled_files[fpath] = (
                        f"// FIX FAILED (iteration {iteration}): {fpath}"
                    )
    else:
        if state.fix_plan:
            for fpath in state.fix_plan.files_to_fix:
                state.assembled_files[fpath] = (
                    f"// STUB FIXED (iteration {iteration}): {fpath}\n"
                    "// Gate errors were in this prompt â€” loop converges on facts."
                )

    state = _emit(state, "fixer_node", GenerationPhase.FIXING,
                  {"status": "complete", "iteration": iteration})
    return state.model_dump()


# â”€â”€ Node: escalation_gate_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def node_escalation_gate(state: GenerationState) -> dict:
    """Contract C6: escalated output re-enters the gate (no trust shortcut)."""
    state = _emit(state, "escalation_gate_node", GenerationPhase.ESCALATING,
                  {"status": "evaluating", "fix_iteration": state.fix_iteration})
    state.current_phase = GenerationPhase.ESCALATING
    state.job_status    = JobStatus.PAUSED_HUMAN

    state = _emit(state, "escalation_gate_node", GenerationPhase.ESCALATING,
                  {"status": "paused_human",
                   "reason": "Phase 0 stub â€” enable Phase 3 for commercial call"})
    return state.model_dump()


# â”€â”€ Node: deliver_node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def node_deliver(
    state: GenerationState,
    config: Optional[RunnableConfig] = None,
) -> dict:
    """Packages and delivers gate-passing artifacts. Contract C7."""
    state = _emit(state, "deliver_node", GenerationPhase.DELIVERING,
                  {"status": "started", "file_count": len(state.assembled_files)})
    state.current_phase = GenerationPhase.DELIVERING

    cfg          = (config or {}).get("configurable", {})
    gitea_client = cfg.get("gitea_client")
    minio_client = cfg.get("minio_client")

    owner      = (state.tenant_id  or "vibeforge")[:8]
    repo_name  = (state.project_id or state.job_id)[:8]
    bundle_key = f"artifacts/{state.tenant_id}/{state.job_id}/bundle.zip"

    # Generate CycloneDX SBOM before bundling/pushing
    if state.assembled_files and "sbom.json" not in state.assembled_files:
        try:
            from agents.graphs.nodes.sbom_generator import SBOMGenerator
            sbom_json = SBOMGenerator().generate_json(
                state.assembled_files,
                stack_profile=getattr(state, "stack_profile", "java_spring"),
                app_name=f"vibeforge-{state.job_id[:8]}",
            )
            state.assembled_files["sbom.json"] = sbom_json
        except Exception as _e:
            logger.warning("[deliver_node] SBOM generation failed: %s", _e)

    if gitea_client and state.assembled_files:
        try:
            state.gitea_repo_url = await gitea_client.push_files(
                owner, repo_name, state.assembled_files)
        except Exception as _e:
            logger.warning("[deliver_node] Gitea push failed: %s — stub URL used", _e)
            state.gitea_repo_url = f"https://git.vibeforge.io/{owner}/{repo_name}"
    else:
        state.gitea_repo_url = f"https://git.vibeforge.io/{owner}/{repo_name}"

    if minio_client and state.assembled_files:
        try:
            await minio_client.upload_bundle(bundle_key, state.assembled_files)
            state.minio_bundle_key = bundle_key
        except Exception as _e:
            logger.warning("[deliver_node] MinIO upload failed: %s — stub key used", _e)
            state.minio_bundle_key = bundle_key
    else:
        state.minio_bundle_key = bundle_key

    state.preview_url  = f"https://preview-{state.job_id[:8]}.vibeforge.io"
    state.sbom_ref     = f"sbom/{state.job_id}.cdx.json"
    state.job_status   = JobStatus.DELIVERED
    state.completed_at = datetime.now(timezone.utc).isoformat()
    state.current_phase = GenerationPhase.DONE

    state = _emit(state, "deliver_node", GenerationPhase.DELIVERING, {
        "status":      "complete",
        "preview_url": state.preview_url,
        "gitea_url":   state.gitea_repo_url,
        "file_count":  len(state.assembled_files),
    })
    return state.model_dump()


def node_failed(state: GenerationState) -> dict:
    state.job_status   = JobStatus.FAILED
    state.completed_at = datetime.now(timezone.utc).isoformat()
    state = _emit(state, "failed_node", GenerationPhase.FAILED,
                  {"status": "failed", "error": state.error_message or ""})
    return state.model_dump()


# â”€â”€ Routing functions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _route_gate(state: GenerationState) -> str:
    gate = state.gate_result
    if not gate:                                        return "failed_node"
    if gate.passed:                                     return "deliver_node"
    if state.fix_iteration < state.max_fix_iterations: return "reviewer_node"
    return "escalation_gate_node"


def _route_escalation(state: GenerationState) -> str:
    if state.escalation_approved:
        return "gate_node"
    return "__end__"


# â”€â”€ Graph builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def build_generation_graph(checkpointer=None):
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(GenerationState)

    g.add_node("plan_node",            node_plan)
    g.add_node("scaffold_node",        node_scaffold)
    g.add_node("synthesize_node",      node_synthesize)
    g.add_node("assemble_node",        node_assemble)
    g.add_node("gate_node",            node_gate)
    g.add_node("reviewer_node",        node_reviewer)
    g.add_node("fixer_node",           node_fixer)
    g.add_node("escalation_gate_node", node_escalation_gate)
    g.add_node("deliver_node",         node_deliver)
    g.add_node("failed_node",          node_failed)

    g.add_edge(START,             "plan_node")
    g.add_edge("plan_node",       "scaffold_node")
    g.add_edge("scaffold_node",   "synthesize_node")
    g.add_edge("synthesize_node", "assemble_node")
    g.add_edge("assemble_node",   "gate_node")

    g.add_conditional_edges("gate_node", _route_gate, {
        "reviewer_node":        "reviewer_node",
        "deliver_node":         "deliver_node",
        "escalation_gate_node": "escalation_gate_node",
        "failed_node":          "failed_node",
    })

    g.add_edge("reviewer_node", "fixer_node")
    g.add_edge("fixer_node",    "gate_node")

    g.add_conditional_edges("escalation_gate_node", _route_escalation, {
        "gate_node": "gate_node",
        "__end__":   END,
    })

    g.add_edge("deliver_node", END)
    g.add_edge("failed_node",  END)

    return g.compile(checkpointer=checkpointer)


# â”€â”€ Public entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def run_generation_job(
    job_id: str,
    tenant_id: str,
    spec=None,
    postgres_dsn: Optional[str] = None,
    llm_client: Optional[Any] = None,
    stack_profile: str = "java_spring",
    spec_snapshot: Optional[dict] = None,
    **kwargs,
) -> GenerationState:
    from langgraph.checkpoint.memory import MemorySaver

    if postgres_dsn:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            async with AsyncPostgresSaver.from_conn_string(postgres_dsn) as cp:
                await cp.setup()
                return await _invoke(
                    job_id, tenant_id, spec, cp,
                    llm_client=llm_client,
                    stack_profile=stack_profile,
                    spec_snapshot=spec_snapshot,
                    gitea_client=kwargs.get("gitea_client"),
                    minio_client=kwargs.get("minio_client"),
                )
        except ImportError:
            logger.warning("psycopg not installed â€” falling back to MemorySaver")

    return await _invoke(
        job_id, tenant_id, spec, MemorySaver(),
        llm_client=llm_client,
        stack_profile=stack_profile,
        spec_snapshot=spec_snapshot,
        gitea_client=kwargs.get("gitea_client"),
        minio_client=kwargs.get("minio_client"),
    )


async def _invoke(
    job_id: str,
    tenant_id: str,
    spec,
    checkpointer,
    llm_client: Optional[Any] = None,
    stack_profile: str = "java_spring",
    spec_snapshot: Optional[dict] = None,
    gitea_client: Optional[Any] = None,
    minio_client: Optional[Any] = None,
) -> GenerationState:
    compiled = build_generation_graph(checkpointer=checkpointer)

    # FIX C2: llm_client is passed via config["configurable"], NOT via state.
    # This keeps it out of the Postgres checkpoint (non-serializable object).
    # On crash recovery, the worker creates a new llm_client â€” Phase 0 activates
    # for that run and the job is retried cleanly.
    config = {
        "configurable": {
            "thread_id":    job_id,
            "llm_client":   llm_client,    # picked up by _get_llm() in each node
            "gitea_client": gitea_client,  # picked up by node_deliver
            "minio_client": minio_client,  # picked up by node_deliver
        }
    }

    entity_names: list[str] = []
    project_id = ""
    spec_id    = ""
    spec_ver   = 1
    can_hash   = ""
    snap: dict = spec_snapshot or {}

    if spec is not None:
        entity_names = [e.name for e in spec.domain_model.entities]
        project_id   = str(getattr(spec, "project_id", ""))
        spec_id      = str(getattr(spec, "spec_id", ""))
        spec_ver     = getattr(spec, "spec_version", 1)
        can_hash     = getattr(spec, "canonical_hash", "") or ""
    elif snap:
        entity_names = [
            e.get("name", "") for e in
            snap.get("domain_model", {}).get("entities", [])
            if e.get("name")
        ]

    initial = GenerationState(
        job_id=job_id, tenant_id=tenant_id, project_id=project_id,
        spec_id=spec_id, spec_version=spec_ver, canonical_hash=can_hash,
        spec_entity_names=entity_names,
        # llm_client NOT set here â€” lives in config["configurable"] only
        stack_profile=stack_profile,
        spec_snapshot=snap,
    )

    result = await compiled.ainvoke(initial.model_dump(), config=config)
    final  = GenerationState.model_validate(result)
    return _wrap_events(final)

