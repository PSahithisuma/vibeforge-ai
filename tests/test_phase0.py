"""
VibeForge Phase 0 — Test Suite
================================
Validates all four Phase 0 deliverables without any GPU or LLM.

Run:
    cd vibeforge && python -m pytest tests/test_phase0.py -v
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

# ── Spec IR tests ──────────────────────────────────────────────────────────────

class TestSpecIR:

    def test_empty_spec_creates_successfully(self):
        from core.spec_ir import make_empty_spec, Vertical
        spec = make_empty_spec(
            tenant_id=uuid4(),
            project_id=uuid4(),
            vertical=Vertical.ECOMMERCE,
        )
        assert spec.spec_version == 1
        assert spec.frozen is False
        assert spec.canonical_hash == ""

    def test_freeze_computes_hash(self):
        from core.spec_ir import make_empty_spec, SpecStatus
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        frozen = spec.freeze()
        assert frozen.frozen is True
        assert frozen.status == SpecStatus.FROZEN
        assert len(frozen.canonical_hash) == 64  # SHA-256 hex

    def test_same_spec_same_hash(self):
        from core.spec_ir import make_empty_spec
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        h1 = spec.compute_hash()
        h2 = spec.compute_hash()
        assert h1 == h2

    def test_different_specs_different_hashes(self):
        from core.spec_ir import make_empty_spec, Vertical
        spec_a = make_empty_spec(tenant_id=uuid4(), project_id=uuid4(), vertical=Vertical.ECOMMERCE)
        spec_b = make_empty_spec(tenant_id=uuid4(), project_id=uuid4(), vertical=Vertical.BANKING)
        assert spec_a.compute_hash() != spec_b.compute_hash()

    def test_cannot_freeze_twice(self):
        from core.spec_ir import make_empty_spec
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        frozen = spec.freeze()
        with pytest.raises(ValueError, match="already frozen"):
            frozen.freeze()

    def test_cannot_mutate_frozen_spec(self):
        from core.spec_ir import make_empty_spec, SpecDelta
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        frozen = spec.freeze()
        delta = SpecDelta(patch={"vertical": "banking"})
        with pytest.raises(ValueError, match="frozen"):
            frozen.apply_delta(delta)

    def test_apply_delta_increments_version(self):
        from core.spec_ir import make_empty_spec, SpecDelta, ProvenanceEntry
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        assert spec.spec_version == 1

        delta = SpecDelta(
            amendment_id="test_amendment",
            patch={"domain_pack_id": "ecommerce_v1"},
            provenance_entries=[
                ProvenanceEntry(
                    json_path="$.domain_pack_id",
                    source_type="option_selection",
                    source_id="test_option",
                    value_snapshot="ecommerce_v1",
                )
            ],
            impact_summary="Updated domain pack",
        )

        new_spec = spec.apply_delta(delta)
        assert new_spec.spec_version == 2
        assert new_spec.domain_pack_id == "ecommerce_v1"
        assert len(new_spec.provenance) == 1

    def test_summarize_returns_key_metrics(self):
        from core.spec_ir import make_empty_spec
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        summary = spec.summarize()
        assert "spec_version" in summary
        assert "entity_count" in summary
        assert "endpoint_count" in summary
        assert summary["frozen"] is False

    def test_hash_excludes_volatile_fields(self):
        """Same logical content → same hash even with different timestamps/IDs."""
        from core.spec_ir import ApplicationSpec, Vertical
        from datetime import datetime, timezone

        t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2024, 6, 15, tzinfo=timezone.utc)

        spec_a = ApplicationSpec(
            tenant_id=uuid4(), project_id=uuid4(),
            vertical=Vertical.ECOMMERCE, created_at=t1, updated_at=t1,
        )
        spec_b = ApplicationSpec(
            tenant_id=uuid4(), project_id=uuid4(),
            vertical=Vertical.ECOMMERCE, created_at=t2, updated_at=t2,
        )
        assert spec_a.compute_hash() == spec_b.compute_hash()


# ── Generation Graph tests ────────────────────────────────────────────────────

class TestGenerationGraph:

    def test_graph_builds_without_error(self):
        from agents.graphs.generation_graph import build_generation_graph
        graph = build_generation_graph()
        assert graph is not None

    def test_full_pipeline_runs_end_to_end(self):
        """Run the complete graph with stub nodes and assert all nodes fired."""
        from agents.graphs.generation_graph import run_generation_job, JobPhase
        from core.spec_ir import make_empty_spec, Entity, EntityField, FieldType

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        spec = spec.model_copy(update={
            "domain_model": spec.domain_model.model_copy(update={
                "entities": [
                    Entity(
                        name="Order",
                        fields=[
                            EntityField(name="total", field_type=FieldType.MONEY),
                            EntityField(name="status", field_type=FieldType.STRING),
                        ],
                    ),
                    Entity(
                        name="Customer",
                        fields=[
                            EntityField(name="email", field_type=FieldType.STRING),
                        ],
                    ),
                ]
            })
        })

        final_state = asyncio.get_event_loop().run_until_complete(
            run_generation_job(
                job_id=str(uuid4()),
                tenant_id=str(uuid4()),
                spec=spec,
                postgres_dsn=None,  # in-memory for Phase 0
            )
        )

        # Verify delivery
        assert final_state.current_phase == JobPhase.DONE
        assert final_state.preview_url != ""
        assert final_state.gitea_repo_url != ""

        # Verify scaffold was rendered
        assert len(final_state.scaffold_files) > 0
        assert "Dockerfile" in final_state.scaffold_files

        # Verify modules were synthesized (2 entities × 3 modules + 1 migration = 7)
        assert len(final_state.synthesized_modules) == 7

        # Verify assembly
        assert len(final_state.assembled_files) > 0

        # Verify SSE events were emitted
        assert len(final_state.events) > 0
        node_names = {e.node for e in final_state.events}
        assert "plan_node" in node_names
        assert "scaffold_node" in node_names
        assert "synthesize_node" in node_names
        assert "gate_node" in node_names
        assert "deliver_node" in node_names

    def test_fix_loop_bounded_at_3(self):
        """When the gate always fails, fix_count must not exceed MAX_FIX_ITERATIONS."""
        from agents.graphs.generation_graph import (
            run_generation_job, GenerationState, MAX_FIX_ITERATIONS
        )
        from core.spec_ir import make_empty_spec

        # Empty spec → no entities → no assembled files → gate will fail in stub
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())

        final_state = asyncio.get_event_loop().run_until_complete(
            run_generation_job(
                job_id=str(uuid4()),
                tenant_id=str(uuid4()),
                spec=spec,
                postgres_dsn=None,
            )
        )

        # Fix count must never exceed the cap
        assert final_state.fix_count <= MAX_FIX_ITERATIONS

    def test_events_written_per_node(self):
        """Every node transition emits at least one job_events entry."""
        from agents.graphs.generation_graph import run_generation_job
        from core.spec_ir import make_empty_spec, Entity, EntityField, FieldType

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        spec = spec.model_copy(update={
            "domain_model": spec.domain_model.model_copy(update={
                "entities": [Entity(name="Product", fields=[EntityField(name="price", field_type=FieldType.MONEY)])]
            })
        })

        final_state = asyncio.get_event_loop().run_until_complete(
            run_generation_job(job_id=str(uuid4()), tenant_id=str(uuid4()), spec=spec)
        )

        phases_seen = {e.phase.value for e in final_state.events}
        # All major phases should appear in the event log
        for expected_phase in ["planning", "scaffolding", "synthesizing", "assembling", "gating"]:
            assert expected_phase in phases_seen, f"Phase '{expected_phase}' not found in events"

    def test_state_is_serializable(self):
        """GenerationState must be fully JSON-serializable (needed for Postgres checkpointer)."""
        from agents.graphs.generation_graph import run_generation_job, GenerationState
        from core.spec_ir import make_empty_spec, Entity, EntityField, FieldType

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        spec = spec.model_copy(update={
            "domain_model": spec.domain_model.model_copy(update={
                "entities": [Entity(name="Cart", fields=[EntityField(name="total", field_type=FieldType.MONEY)])]
            })
        })

        final_state = asyncio.get_event_loop().run_until_complete(
            run_generation_job(job_id=str(uuid4()), tenant_id=str(uuid4()), spec=spec)
        )

        # Must round-trip through JSON without error
        serialized = final_state.model_dump_json()
        restored = GenerationState.model_validate_json(serialized)
        assert restored.job_id == final_state.job_id


# ── Option Graph Engine tests ─────────────────────────────────────────────────

class TestOptionGraphEngine:

    @pytest.fixture
    def pack_dir(self, tmp_path):
        """Create a minimal pack directory for testing."""
        og_dir = tmp_path / "option_graphs"
        og_dir.mkdir()

        yaml_content = """
section_id: test_section
title: Test section
prerequisites: []
options:
  - option_id: add_reviews
    label: Product reviews
    description: Allow customers to leave reviews
    eligibility_rules:
      - rule_type: none
    spec_bindings:
      - json_path: domain_model.entities
        operation: append_if_missing
        value:
          name: Review
          fields:
            - {name: rating, field_type: integer, required: true}
            - {name: comment, field_type: string, required: false}
          relationships:
            - {target_entity: Product, relationship_type: many_to_one, foreign_key: product_id}
          audit_fields: true
          soft_delete: false
      - json_path: api_model.endpoints
        operation: append_if_missing
        value:
          method: POST
          path: /api/v1/products/{id}/reviews
          summary: Submit a product review
          auth_required: true
          roles: [CUSTOMER]
          tags: [reviews]

  - option_id: featured_reviews
    label: Featured reviews on homepage
    description: Requires reviews to be enabled first
    eligibility_rules:
      - rule_type: requires_option
        option_id: add_reviews
    spec_bindings:
      - json_path: api_model.endpoints
        operation: append_if_missing
        value:
          method: GET
          path: /api/v1/reviews/featured
          summary: Get featured reviews
          auth_required: false
          tags: [reviews]
"""
        (og_dir / "01_test.yaml").write_text(yaml_content)
        return tmp_path

    @pytest.fixture
    def engine(self, pack_dir):
        from agents.option_graph.engine import OptionGraphEngine
        return OptionGraphEngine.from_pack_dir(pack_dir)

    def test_load_option_graph(self, engine):
        assert len(engine.graph.sections) == 1
        assert engine.graph.sections[0].section_id == "test_section"
        assert len(engine.graph.sections[0].options) == 2

    def test_eligible_option_produces_delta(self, engine):
        from agents.option_graph.engine import EligibilityContext
        from core.spec_ir import make_empty_spec

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        context = EligibilityContext(selected_options=set())

        delta = engine.evaluate_selection("add_reviews", context, spec)

        assert delta is not None
        assert "domain_model" in delta.patch
        assert delta.new_entity_count == 1  # Review entity
        assert delta.new_endpoint_count == 1  # POST /reviews
        assert len(delta.provenance_entries) > 0

    def test_ineligible_option_raises(self, engine):
        from agents.option_graph.engine import EligibilityContext
        from core.spec_ir import make_empty_spec

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        context = EligibilityContext(selected_options=set())  # add_reviews NOT selected

        with pytest.raises(ValueError, match="not eligible"):
            engine.evaluate_selection("featured_reviews", context, spec)

    def test_eligible_after_prerequisite_selected(self, engine):
        from agents.option_graph.engine import EligibilityContext
        from core.spec_ir import make_empty_spec

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        context = EligibilityContext(selected_options={"add_reviews"})

        # Should not raise now
        delta = engine.evaluate_selection("featured_reviews", context, spec)
        assert delta is not None

    def test_delta_applied_to_spec(self, engine):
        from agents.option_graph.engine import EligibilityContext
        from core.spec_ir import make_empty_spec

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        context = EligibilityContext(selected_options=set())

        delta = engine.evaluate_selection("add_reviews", context, spec)
        new_spec = spec.apply_delta(delta)

        entity_names = [e.name for e in new_spec.domain_model.entities]
        assert "Review" in entity_names
        assert new_spec.spec_version == 2  # incremented

    def test_append_if_missing_is_idempotent(self, engine):
        """Applying the same option twice should not duplicate entities."""
        from agents.option_graph.engine import EligibilityContext
        from core.spec_ir import make_empty_spec

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        context = EligibilityContext(selected_options=set())

        delta1 = engine.evaluate_selection("add_reviews", context, spec)
        spec_v2 = spec.apply_delta(delta1)

        context2 = EligibilityContext(selected_options={"add_reviews"})
        delta2 = engine.evaluate_selection("add_reviews", context2, spec_v2)
        spec_v3 = spec_v2.apply_delta(delta2)

        entity_names = [e.name for e in spec_v3.domain_model.entities]
        assert entity_names.count("Review") == 1  # not duplicated

    def test_live_impact_updates(self, engine):
        from agents.option_graph.engine import EligibilityContext
        from core.spec_ir import make_empty_spec

        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        context = EligibilityContext(selected_options=set())

        impact_before = engine.get_live_impact(context, spec)
        assert impact_before["entity_count"] == 0

        delta = engine.evaluate_selection("add_reviews", context, spec)
        new_spec = spec.apply_delta(delta)

        impact_after = engine.get_live_impact(context, new_spec)
        assert impact_after["entity_count"] == 1

    def test_get_section_status(self, engine):
        from agents.option_graph.engine import EligibilityContext

        context = EligibilityContext(selected_options=set())
        status = engine.get_section_status("test_section", context)

        assert status["section_id"] == "test_section"
        assert "add_reviews" in status["options"]
        assert status["options"]["add_reviews"]["eligible"] is True
        assert status["options"]["featured_reviews"]["eligible"] is False


# ── Structured Output Harness tests ───────────────────────────────────────────

class TestStructuredOutputHarness:

    class GreetingModel(BaseModel := __import__('pydantic').BaseModel):
        name: str
        message: str
        confidence: float

    @pytest.fixture
    def perfect_mock(self):
        """Mock LLM that always returns valid JSON."""
        async def _call(model: str, system: str, user: str) -> str:
            return json.dumps({"name": "Alice", "message": "Hello!", "confidence": 0.95})
        return _call

    @pytest.fixture
    def fenced_mock(self):
        """Mock LLM that wraps JSON in markdown fences (common model behavior)."""
        async def _call(model: str, system: str, user: str) -> str:
            return "Sure! Here's the result:\n```json\n{\"name\": \"Bob\", \"message\": \"Hi\", \"confidence\": 0.8}\n```"
        return _call

    @pytest.fixture
    def fail_then_succeed_mock(self):
        """Mock that fails once then returns valid JSON."""
        call_count = {"n": 0}
        async def _call(model: str, system: str, user: str) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "Sorry I cannot answer that right now."
            return json.dumps({"name": "Charlie", "message": "Fixed!", "confidence": 0.7})
        return _call

    @pytest.fixture
    def always_fail_mock(self):
        """Mock that always returns garbage."""
        async def _call(model: str, system: str, user: str) -> str:
            return "I'm just a language model and cannot produce JSON."
        return _call

    def test_valid_json_response(self, perfect_mock):
        from agents.harness.structured_output import StructuredOutputHarness

        class GreetingModel(__import__('pydantic').BaseModel):
            name: str
            message: str
            confidence: float

        harness = StructuredOutputHarness(perfect_mock)
        result, meta = asyncio.get_event_loop().run_until_complete(
            harness.call(
                output_schema=GreetingModel,
                user_message="Generate a greeting for Alice",
                context_tag="test_greeting",
            )
        )
        assert result.name == "Alice"
        assert result.confidence == 0.95
        assert meta.attempts == 1
        assert meta.repaired is False

    def test_markdown_fenced_json_extracted(self, fenced_mock):
        from agents.harness.structured_output import StructuredOutputHarness

        class GreetingModel(__import__('pydantic').BaseModel):
            name: str
            message: str
            confidence: float

        harness = StructuredOutputHarness(fenced_mock)
        result, meta = asyncio.get_event_loop().run_until_complete(
            harness.call(GreetingModel, "greeting please", context_tag="fence_test")
        )
        assert result.name == "Bob"

    def test_repair_on_first_fail(self, fail_then_succeed_mock):
        from agents.harness.structured_output import StructuredOutputHarness

        class GreetingModel(__import__('pydantic').BaseModel):
            name: str
            message: str
            confidence: float

        harness = StructuredOutputHarness(fail_then_succeed_mock)
        result, meta = asyncio.get_event_loop().run_until_complete(
            harness.call(GreetingModel, "test", context_tag="repair_test")
        )
        assert result.name == "Charlie"
        assert meta.attempts == 2
        assert meta.repaired is True

    def test_raises_after_max_attempts(self, always_fail_mock):
        from agents.harness.structured_output import StructuredOutputHarness, HarnessError

        class GreetingModel(__import__('pydantic').BaseModel):
            name: str
            message: str
            confidence: float

        harness = StructuredOutputHarness(always_fail_mock)
        with pytest.raises(HarnessError):
            asyncio.get_event_loop().run_until_complete(
                harness.call(GreetingModel, "test", max_attempts=3, context_tag="fail_test")
            )

    def test_json_extraction_bare(self):
        from agents.harness.structured_output import extract_json
        raw = '{"key": "value", "num": 42}'
        assert extract_json(raw) == raw

    def test_json_extraction_fenced(self):
        from agents.harness.structured_output import extract_json
        raw = "Here is the result:\n```json\n{\"key\": \"value\"}\n```\nHope that helps!"
        extracted = extract_json(raw)
        assert extracted == '{"key": "value"}'

    def test_json_extraction_embedded(self):
        from agents.harness.structured_output import extract_json
        raw = 'The answer is {"name": "test", "value": 1} as shown above.'
        extracted = extract_json(raw)
        parsed = json.loads(extracted)
        assert parsed["name"] == "test"

    def test_json_extraction_fails_on_no_json(self):
        from agents.harness.structured_output import extract_json, SchemaExtractionError
        with pytest.raises(SchemaExtractionError):
            extract_json("This response has no JSON at all.")


# ── Real domain pack integration test ─────────────────────────────────────────

class TestEcommercePack:
    """Tests against the real e-commerce pack YAML (not a temp fixture)."""

    @pytest.fixture
    def pack_dir(self):
        return Path(__file__).parent.parent / "packs" / "ecommerce"

    def test_ecommerce_pack_loads(self, pack_dir):
        if not pack_dir.exists():
            pytest.skip("E-commerce pack not present")
        from agents.option_graph.engine import OptionGraphEngine
        engine = OptionGraphEngine.from_pack_dir(pack_dir)
        assert len(engine.graph.sections) >= 1

    def test_product_variants_option(self, pack_dir):
        if not pack_dir.exists():
            pytest.skip("E-commerce pack not present")
        from agents.option_graph.engine import OptionGraphEngine, EligibilityContext
        from core.spec_ir import make_empty_spec

        engine = OptionGraphEngine.from_pack_dir(pack_dir)
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        context = EligibilityContext()

        delta = engine.evaluate_selection("product_variants", context, spec)
        assert delta.new_entity_count >= 1
        new_spec = spec.apply_delta(delta)
        entity_names = [e.name for e in new_spec.domain_model.entities]
        assert "ProductVariant" in entity_names


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
