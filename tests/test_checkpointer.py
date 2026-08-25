from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import patch
from uuid import uuid4

from agents.graphs.generation_graph import (
    FileMap,
    FixPlan,
    GateResult,
    GateStepResult,
    GenerationPhase,
    GenerationState,
    JobStatus,
    ModulePlan,
    node_fixer,
    node_plan,
    node_reviewer,
    run_generation_job,
)


class _MockLLMClient:
    async def __call__(self, model, system, user, **kwargs):
        return '{"modules": [], "synthesis_mode": "sequential", "total_modules": 0}'


class _FakeEntity:
    def __init__(self, name): self.name = name


class _FakeSpec:
    def __init__(self, *entity_names):
        class _DM:
            entities = [_FakeEntity(n) for n in entity_names]
        self.domain_model   = _DM()
        self.project_id     = "test-project"
        self.spec_id        = "test-spec"
        self.spec_version   = 1
        self.canonical_hash = "testhash"


def _run(coro):
    return asyncio.run(coro)


def _base_state(**kwargs):
    fpath = "src/main/java/com/vibeforge/product/ProductEntity.java"
    defaults = dict(
        job_id=str(uuid4()),
        tenant_id="test",
        spec_entity_names=["Product"],
        scaffold_files={"pom.xml": "<!-- stub -->"},
        module_plan=[ModulePlan(module_id="m0", name="ProductEntity",
                                module_type="entity", dependencies=[])],
        file_maps=[FileMap(module_id="m0", files={fpath: "// stub"})],
        assembled_files={fpath: "// stub"},
    )
    defaults.update(kwargs)
    return GenerationState(**defaults)


def _is_memory_saver(name):
    return "MemorySaver" in name


class TestStateSerializability:
    def test_llm_client_excluded_from_model_dump(self):
        client = _MockLLMClient()
        state  = GenerationState(job_id=str(uuid4()), llm_client=client)
        assert "llm_client" not in state.model_dump()

    def test_llm_client_still_accessible_as_attribute(self):
        client = _MockLLMClient()
        state  = GenerationState(job_id=str(uuid4()), llm_client=client)
        assert state.llm_client is client

    def test_full_state_is_json_serializable(self):
        state = GenerationState(
            job_id=str(uuid4()), tenant_id="test",
            spec_entity_names=["Product", "Order"],
            assembled_files={"src/Foo.java": "// code"},
            scaffold_files={"pom.xml": "<!-- xml -->"},
            module_plan=[ModulePlan(module_id="m0", name="Foo", module_type="entity")],
            file_maps=[FileMap(module_id="m0", files={"src/Foo.java": "// code"})],
        )
        try:
            json.dumps(state.model_dump())
        except TypeError as e:
            raise AssertionError(f"C2 violated: {e}")

    def test_model_dump_json_excludes_llm_client(self):
        client = _MockLLMClient()
        state  = GenerationState(job_id=str(uuid4()), llm_client=client)
        assert "llm_client" not in json.loads(state.model_dump_json())

    def test_state_survives_postgres_round_trip(self):
        client       = _MockLLMClient()
        state        = _base_state(llm_client=client)
        deserialized = GenerationState.model_validate(
            json.loads(json.dumps(state.model_dump()))
        )
        assert deserialized.llm_client is None
        assert deserialized.spec_entity_names == ["Product"]
        assert deserialized.stack_profile == "java_spring"


class TestLLMClientViaConfig:
    def test_node_plan_with_config_llm_client(self):
        client = _MockLLMClient()
        state  = _base_state()
        config = {"configurable": {"llm_client": client}}
        assert state.llm_client is None
        result = _run(node_plan(state, config))
        out    = GenerationState.model_validate(result)
        assert len(out.module_plan) > 0
        assert "llm_client" not in out.model_dump()

    def test_node_plan_backward_compat_no_config(self):
        result = _run(node_plan(_base_state()))
        assert len(GenerationState.model_validate(result).module_plan) > 0

    def test_node_plan_backward_compat_state_llm(self):
        state  = _base_state(llm_client=_MockLLMClient())
        result = _run(node_plan(state))
        assert len(GenerationState.model_validate(result).module_plan) > 0

    def test_node_reviewer_with_config_llm_client(self):
        client = _MockLLMClient()
        state  = _base_state(gate_result=GateResult(
            passed=False,
            steps=[GateStepResult(step="compile", passed=False, output="BUILD FAILURE")],
            failing_files=["src/Foo.java"],
        ))
        config = {"configurable": {"llm_client": client}}
        assert state.llm_client is None
        result = _run(node_reviewer(state, config))
        out    = GenerationState.model_validate(result)
        assert out.fix_plan is not None
        assert "llm_client" not in out.model_dump()

    def test_node_reviewer_backward_compat_no_config(self):
        state  = _base_state(gate_result=GateResult(
            passed=False,
            steps=[GateStepResult(step="compile", passed=False, output="ERROR")],
            failing_files=["src/Foo.java"],
        ))
        result = _run(node_reviewer(state))
        assert GenerationState.model_validate(result).fix_plan is not None

    def test_node_fixer_with_config_llm_client(self):
        client = _MockLLMClient()
        fpath  = "src/main/java/com/vibeforge/stub/Stub.java"
        state  = _base_state(fix_plan=FixPlan(
            files_to_fix=[fpath],
            errors_by_file={fpath: ["error: cannot find symbol"]},
            iteration=1,
        ))
        config = {"configurable": {"llm_client": client}}
        assert state.llm_client is None
        result = _run(node_fixer(state, config))
        out    = GenerationState.model_validate(result)
        assert out.fix_iteration == 1
        assert "llm_client" not in out.model_dump()

    def test_node_fixer_backward_compat_no_config(self):
        fpath = "src/main/java/com/vibeforge/stub/Stub.java"
        state = _base_state(fix_plan=FixPlan(
            files_to_fix=[fpath],
            errors_by_file={fpath: ["error: cannot find symbol"]},
            iteration=1,
        ))
        result = _run(node_fixer(state))
        assert GenerationState.model_validate(result).fix_iteration == 1


class TestCheckpointerPathSelection:
    def test_no_postgres_dsn_uses_memory_saver(self):
        invoked_with = []

        async def fake_invoke(job_id, tenant_id, spec, checkpointer, **kwargs):
            invoked_with.append(type(checkpointer).__name__)
            return GenerationState(job_id=job_id, tenant_id=tenant_id)

        async def run():
            with patch("agents.graphs.generation_graph._invoke", side_effect=fake_invoke):
                await run_generation_job(job_id=str(uuid4()), tenant_id="test",
                                         spec=_FakeSpec("Product"), postgres_dsn=None)
        _run(run())
        assert len(invoked_with) == 1 and _is_memory_saver(invoked_with[0])

    def test_import_error_falls_back_to_memory_saver(self):
        invoked_with = []

        async def fake_invoke(job_id, tenant_id, spec, checkpointer, **kwargs):
            invoked_with.append(type(checkpointer).__name__)
            return GenerationState(job_id=job_id, tenant_id=tenant_id)

        async def run():
            with patch("agents.graphs.generation_graph._invoke", side_effect=fake_invoke), \
                 patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": None}):
                await run_generation_job(job_id=str(uuid4()), tenant_id="test",
                                         spec=_FakeSpec("Product"),
                                         postgres_dsn="postgresql://test:test@localhost/test")
        _run(run())
        assert any(_is_memory_saver(n) for n in invoked_with)

    def test_llm_client_forwarded_to_invoke(self):
        received = {}

        async def fake_invoke(job_id, tenant_id, spec, checkpointer, **kwargs):
            received.update(kwargs)
            return GenerationState(job_id=job_id, tenant_id=tenant_id)

        client = _MockLLMClient()

        async def run():
            with patch("agents.graphs.generation_graph._invoke", side_effect=fake_invoke):
                await run_generation_job(job_id=str(uuid4()), tenant_id="test",
                                         spec=_FakeSpec("Product"), llm_client=client)
        _run(run())
        assert received.get("llm_client") is client

    def test_stack_profile_forwarded_to_invoke(self):
        received = {}

        async def fake_invoke(job_id, tenant_id, spec, checkpointer, **kwargs):
            received.update(kwargs)
            return GenerationState(job_id=job_id, tenant_id=tenant_id)

        async def run():
            with patch("agents.graphs.generation_graph._invoke", side_effect=fake_invoke):
                await run_generation_job(job_id=str(uuid4()), tenant_id="test",
                                         spec=_FakeSpec("Product"), stack_profile="python_fastapi")
        _run(run())
        assert received.get("stack_profile") == "python_fastapi"


class TestCrashRecovery:
    def test_pipeline_delivers_after_crash_recovery_state(self):
        state = _run(run_generation_job(job_id=str(uuid4()), tenant_id="test",
                                        spec=_FakeSpec("Product"), llm_client=None))
        assert state.job_status == JobStatus.DELIVERED

    def test_pipeline_delivers_with_live_llm_client(self):
        state = _run(run_generation_job(job_id=str(uuid4()), tenant_id="test",
                                        spec=_FakeSpec("Product"), llm_client=_MockLLMClient()))
        assert state.job_status == JobStatus.DELIVERED

    def test_spec_snapshot_preserved_through_postgres(self):
        snap = {"domain_model": {"entities": [{"name": "Product", "fields": []}]},
                "api_model": {"endpoints": []}, "stack": {"backend": "python_fastapi"}}
        state = _run(run_generation_job(job_id=str(uuid4()), tenant_id="test", spec_snapshot=snap))
        assert state.job_status == JobStatus.DELIVERED
