from __future__ import annotations

"""
tests/test_fix_loop.py
─────────────────────────────────────────────────────────────────────────────
Fix loop and escalation path tests.

Contract coverage:
  C5 — Fix loop hard-capped at MAX_FIX_ITERATIONS = 3.
  C6 — Escalated output re-enters the sandbox gate (no trust shortcut).

These tests cover code paths that the Phase 0 gate stub never exercises:
  • node_reviewer        — triggered when gate fails
  • node_fixer           — triggered after reviewer produces fix plan
  • node_escalation_gate — triggered when fix iterations exhausted
  • _route_gate          — routing logic for all gate outcomes
  • _route_escalation    — routing logic for escalation approval
"""

import asyncio
from unittest.mock import patch
from uuid import uuid4

from agents.graphs.generation_graph import (
    MAX_FIX_ITERATIONS,
    FileMap,
    FixPlan,
    GateResult,
    GateStepResult,
    GenerationPhase,
    GenerationState,
    JobStatus,
    ModulePlan,
    _emit,
    _route_escalation,
    _route_gate,
    node_escalation_gate,
    node_fixer,
    node_gate as _REAL_NODE_GATE,   # captured before any patch — prevents recursion
    node_reviewer,
    run_generation_job,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _failing_gate_result(failing_files=None) -> GateResult:
    files = failing_files or ["src/main/java/com/vibeforge/stub/Stub.java"]
    return GateResult(
        passed=False,
        steps=[
            GateStepResult(step="compile",    passed=False,
                           output="BUILD FAILURE\nerror: cannot find symbol"),
            GateStepResult(step="unit_tests", passed=True,  duration_ms=0),
            GateStepResult(step="migrations", passed=True),
            GateStepResult(step="api_smoke",  passed=True),
            GateStepResult(step="semgrep",    passed=True),
            GateStepResult(step="trivy_osv",  passed=True),
            GateStepResult(step="gitleaks",   passed=True),
        ],
        failing_files=files,
    )


def _passing_gate_result() -> GateResult:
    return GateResult(
        passed=True,
        steps=[
            GateStepResult(step="compile",    passed=True, output="BUILD SUCCESS"),
            GateStepResult(step="unit_tests", passed=True, coverage_pct=75.0),
            GateStepResult(step="migrations", passed=True),
            GateStepResult(step="api_smoke",  passed=True),
            GateStepResult(step="semgrep",    passed=True),
            GateStepResult(step="trivy_osv",  passed=True),
            GateStepResult(step="gitleaks",   passed=True),
        ],
    )


def _base_state(**kwargs) -> GenerationState:
    fpath = "src/main/java/com/vibeforge/product/ProductEntity.java"
    defaults = dict(
        job_id=str(uuid4()),
        tenant_id="test",
        spec_entity_names=["Product"],
        scaffold_files={"pom.xml": "<!-- stub -->"},
        module_plan=[
            ModulePlan(module_id="m0_entity", name="ProductEntity",
                       module_type="entity", dependencies=[]),
        ],
        file_maps=[
            FileMap(module_id="m0_entity", files={fpath: "// stub"}),
        ],
        assembled_files={fpath: "// stub"},
    )
    defaults.update(kwargs)
    return GenerationState(**defaults)


class _FakeEntity:
    def __init__(self, name: str):
        self.name = name


class _FakeSpec:
    def __init__(self, *entity_names: str):
        class _DM:
            entities = [_FakeEntity(n) for n in entity_names]
        self.domain_model   = _DM()
        self.project_id     = "test-project"
        self.spec_id        = "test-spec"
        self.spec_version   = 1
        self.canonical_hash = "testhash"


def _run(coro):
    return asyncio.run(coro)


def _make_always_failing_gate():
    def _gate(state: GenerationState) -> dict:
        if isinstance(state, dict):
            state = GenerationState.model_validate(state)
        failing = _failing_gate_result()
        state.gate_result   = failing
        state.current_phase = GenerationPhase.GATING
        state.job_status    = JobStatus.GATED
        state = _emit(state, "gate_node", GenerationPhase.GATING, {
            "status": "failed", "iteration": state.fix_iteration,
            "coverage": 0.0, "report_id": failing.report_id,
        })
        return state.model_dump()
    return _gate


def _make_fail_then_pass_gate(fail_count: int = 1):
    calls = [0]

    def _gate(state: GenerationState) -> dict:
        if isinstance(state, dict):
            state = GenerationState.model_validate(state)
        calls[0] += 1
        if calls[0] <= fail_count:
            failing = _failing_gate_result()
            state.gate_result   = failing
            state.current_phase = GenerationPhase.GATING
            state.job_status    = JobStatus.GATED
            state = _emit(state, "gate_node", GenerationPhase.GATING, {
                "status": "failed", "iteration": state.fix_iteration,
                "coverage": 0.0, "report_id": failing.report_id,
            })
            return state.model_dump()
        # FIX: use _REAL_NODE_GATE captured at import time — NOT the patched mock
        return _REAL_NODE_GATE(state)

    return _gate


# ── Routing ───────────────────────────────────────────────────────────────────

class TestRoutingFunctions:
    def test_route_gate_passes_goes_to_deliver(self):
        assert _route_gate(_base_state(gate_result=_passing_gate_result())) == "deliver_node"

    def test_route_gate_no_result_goes_to_failed(self):
        assert _route_gate(_base_state(gate_result=None)) == "failed_node"

    def test_route_gate_fail_within_limit_goes_to_reviewer(self):
        state = _base_state(gate_result=_failing_gate_result(), fix_iteration=0, max_fix_iterations=3)
        assert _route_gate(state) == "reviewer_node"

    def test_route_gate_fail_at_limit_goes_to_escalation(self):
        state = _base_state(gate_result=_failing_gate_result(), fix_iteration=3, max_fix_iterations=3)
        assert _route_gate(state) == "escalation_gate_node"

    def test_route_gate_fail_beyond_limit_goes_to_escalation(self):
        state = _base_state(gate_result=_failing_gate_result(), fix_iteration=5, max_fix_iterations=3)
        assert _route_gate(state) == "escalation_gate_node"

    def test_route_gate_exactly_at_max(self):
        state = _base_state(
            gate_result=_failing_gate_result(),
            fix_iteration=MAX_FIX_ITERATIONS,
            max_fix_iterations=MAX_FIX_ITERATIONS,
        )
        assert _route_gate(state) == "escalation_gate_node"

    def test_route_escalation_approved_goes_to_gate(self):
        assert _route_escalation(_base_state(escalation_approved=True)) == "gate_node"

    def test_route_escalation_not_approved_ends(self):
        assert _route_escalation(_base_state(escalation_approved=False)) == "__end__"


# ── node_reviewer ─────────────────────────────────────────────────────────────

class TestNodeReviewer:
    def test_reviewer_skips_when_gate_passes(self):
        result = _run(node_reviewer(_base_state(gate_result=_passing_gate_result())))
        assert GenerationState.model_validate(result).fix_plan is None

    def test_reviewer_produces_fix_plan_on_failure(self):
        state = _base_state(gate_result=_failing_gate_result(), fix_iteration=0)
        out = GenerationState.model_validate(_run(node_reviewer(state)))
        assert out.fix_plan is not None
        assert len(out.fix_plan.files_to_fix) > 0

    def test_reviewer_fix_plan_iteration_increments(self):
        state = _base_state(gate_result=_failing_gate_result(), fix_iteration=1)
        out = GenerationState.model_validate(_run(node_reviewer(state)))
        assert out.fix_plan.iteration == 2

    def test_reviewer_sets_escalation_flag_near_limit(self):
        state = _base_state(gate_result=_failing_gate_result(), fix_iteration=2, max_fix_iterations=3)
        out = GenerationState.model_validate(_run(node_reviewer(state)))
        assert out.fix_plan.escalation_recommended is True

    def test_reviewer_fix_plan_has_errors_per_file(self):
        state = _base_state(gate_result=_failing_gate_result())
        out = GenerationState.model_validate(_run(node_reviewer(state)))
        for fpath in out.fix_plan.files_to_fix:
            assert fpath in out.fix_plan.errors_by_file
            assert len(out.fix_plan.errors_by_file[fpath]) > 0

    def test_reviewer_emits_reviewing_events(self):
        state = _base_state(gate_result=_failing_gate_result())
        out = GenerationState.model_validate(_run(node_reviewer(state)))
        assert GenerationPhase.REVIEWING.value in {e["phase"] for e in out.events}

    def test_reviewer_sets_reviewing_phase(self):
        state = _base_state(gate_result=_failing_gate_result())
        out = GenerationState.model_validate(_run(node_reviewer(state)))
        assert out.current_phase == GenerationPhase.REVIEWING


# ── node_fixer ────────────────────────────────────────────────────────────────

class TestNodeFixer:
    FPATH = "src/main/java/com/vibeforge/stub/Stub.java"

    def _state(self, iteration: int = 1) -> GenerationState:
        return _base_state(
            fix_iteration=0,
            assembled_files={self.FPATH: "// broken stub code"},
            fix_plan=FixPlan(
                files_to_fix=[self.FPATH],
                errors_by_file={self.FPATH: ["error: cannot find symbol 'OrderRepository'"]},
                iteration=iteration,
                escalation_recommended=False,
                failure_classification="mechanical",
            ),
        )

    def test_fixer_increments_fix_iteration(self):
        out = GenerationState.model_validate(_run(node_fixer(self._state(1))))
        assert out.fix_iteration == 1 and out.fix_count == 1

    def test_fixer_updates_assembled_files(self):
        out = GenerationState.model_validate(_run(node_fixer(self._state(1))))
        assert self.FPATH in out.assembled_files
        assert out.assembled_files[self.FPATH] != "// broken stub code"

    def test_fixer_sets_fixing_status(self):
        out = GenerationState.model_validate(_run(node_fixer(self._state())))
        assert out.job_status == JobStatus.FIXING

    def test_fixer_sets_fixing_phase(self):
        out = GenerationState.model_validate(_run(node_fixer(self._state())))
        assert out.current_phase == GenerationPhase.FIXING

    def test_fixer_emits_fixing_events(self):
        out = GenerationState.model_validate(_run(node_fixer(self._state())))
        assert GenerationPhase.FIXING.value in {e["phase"] for e in out.events}

    def test_c5_gate_errors_in_fixer_start_event(self):
        """Contract C5: gate_errors must be non-empty in fixer start event."""
        out = GenerationState.model_validate(_run(node_fixer(self._state(1))))
        started = next(
            (e for e in out.events
             if e["phase"] == GenerationPhase.FIXING.value
             and e["payload"].get("status") == "started"),
            None,
        )
        assert started is not None, "No fixer start event emitted"
        assert "gate_errors" in started["payload"], "C5 violated: gate_errors missing"
        assert started["payload"]["gate_errors"],   "C5 violated: gate_errors is empty"

    def test_fixer_without_fix_plan_still_increments(self):
        out = GenerationState.model_validate(_run(node_fixer(_base_state(fix_plan=None))))
        assert out.fix_iteration == 1

    def test_fixer_at_max_iteration(self):
        out = GenerationState.model_validate(_run(node_fixer(self._state(MAX_FIX_ITERATIONS))))
        assert out.fix_iteration == MAX_FIX_ITERATIONS


# ── node_escalation_gate ──────────────────────────────────────────────────────

class TestNodeEscalationGate:
    def test_sets_paused_human_status(self):
        out = GenerationState.model_validate(node_escalation_gate(_base_state(fix_iteration=3)))
        assert out.job_status == JobStatus.PAUSED_HUMAN

    def test_sets_escalating_phase(self):
        out = GenerationState.model_validate(node_escalation_gate(_base_state(fix_iteration=3)))
        assert out.current_phase == GenerationPhase.ESCALATING

    def test_emits_escalating_events(self):
        out = GenerationState.model_validate(node_escalation_gate(_base_state(fix_iteration=3)))
        assert GenerationPhase.ESCALATING.value in {e["phase"] for e in out.events}

    def test_emits_paused_human_payload(self):
        out = GenerationState.model_validate(node_escalation_gate(_base_state(fix_iteration=3)))
        statuses = [e["payload"].get("status") for e in out.events]
        assert "paused_human" in statuses

    def test_emits_evaluating_then_paused(self):
        out = GenerationState.model_validate(node_escalation_gate(_base_state(fix_iteration=3)))
        esc = [e for e in out.events if e["phase"] == GenerationPhase.ESCALATING.value]
        assert len(esc) == 2
        assert esc[0]["payload"]["status"] == "evaluating"
        assert esc[1]["payload"]["status"] == "paused_human"


# ── Integration ───────────────────────────────────────────────────────────────

class TestFixLoopIntegration:
    def test_gate_fail_once_delivers(self):
        with patch("agents.graphs.generation_graph.node_gate",
                   side_effect=_make_fail_then_pass_gate(1)):
            state = _run(run_generation_job(str(uuid4()), "test", _FakeSpec("Product", "Order")))
        phases = {e.phase.value for e in state.events}
        assert GenerationPhase.REVIEWING.value  in phases
        assert GenerationPhase.FIXING.value     in phases
        assert GenerationPhase.DELIVERING.value in phases
        assert state.job_status == JobStatus.DELIVERED
        assert state.fix_count >= 1

    def test_gate_fail_twice_fix_count_2(self):
        with patch("agents.graphs.generation_graph.node_gate",
                   side_effect=_make_fail_then_pass_gate(2)):
            state = _run(run_generation_job(str(uuid4()), "test", _FakeSpec("Product")))
        assert state.fix_count == 2
        assert state.job_status == JobStatus.DELIVERED

    def test_c5_gate_always_fails_escalates_within_cap(self):
        """Contract C5: escalation after MAX_FIX_ITERATIONS, never exceeded."""
        with patch("agents.graphs.generation_graph.node_gate",
                   side_effect=_make_always_failing_gate()):
            state = _run(run_generation_job(str(uuid4()), "test", _FakeSpec("Product")))
        phases = {e.phase.value for e in state.events}
        assert GenerationPhase.ESCALATING.value in phases
        assert state.fix_count <= MAX_FIX_ITERATIONS, \
            f"C5 violated: fix_count={state.fix_count} > MAX={MAX_FIX_ITERATIONS}"
        assert state.job_status == JobStatus.PAUSED_HUMAN

    def test_c5_gate_errors_in_every_fixer_start_event(self):
        """Contract C5: every fixer start event across all iterations has gate_errors."""
        with patch("agents.graphs.generation_graph.node_gate",
                   side_effect=_make_fail_then_pass_gate(2)):
            state = _run(run_generation_job(str(uuid4()), "test", _FakeSpec("Product")))
        starts = [
            e for e in state.events
            if e.node == "fixer_node" and e.payload.get("status") == "started"
        ]
        assert starts, "No fixer start events found"
        for evt in starts:
            assert "gate_errors" in evt.payload, f"C5 violated: gate_errors missing in {evt}"
            assert evt.payload["gate_errors"],   f"C5 violated: gate_errors empty in {evt}"


# ── Contract C6 ───────────────────────────────────────────────────────────────

class TestContractC6:
    def test_approved_escalation_routes_to_gate(self):
        """C6: escalated output must re-enter gate_node, not skip to deliver."""
        assert _route_escalation(_base_state(escalation_approved=True)) == "gate_node"

    def test_unapproved_escalation_ends(self):
        assert _route_escalation(_base_state(escalation_approved=False)) == "__end__"

    def test_default_state_escalation_not_approved(self):
        """C6: escalation_approved must default to False — no accidental gate bypass."""
        state = GenerationState(job_id=str(uuid4()), tenant_id="test")
        assert state.escalation_approved is False