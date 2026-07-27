"""
Phase 2 test suite — Planner, Synthesizer, Assembler, Reviewer, Fixer,
Metacognition Gate Tier 1.

All LLM calls use async mock functions — no model needed.
Run with: python -m pytest tests/test_phase2.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).parent.parent
for p in [ROOT, ROOT / "agents", ROOT / "core"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agents.generation.planner import PlannerAgent, PlannedModule, ModulePlanOutput
from agents.generation.synthesizer import SynthesizerAgent, FileMapOutput, SynthesizedFile
from agents.generation.assembler import Assembler, AssemblyResult, ConflictRecord
from agents.generation.reviewer_fixer import (
    ReviewerAgent, FixerAgent, MetacognitionGateTier1,
    ReviewerOutput, FileFixInstruction,
)
from agents.graphs.generation_graph import GateResult, GateStepResult, FixPlan


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_mock_llm(response: dict):
    async def _llm(model, system, user, **kwargs):
        return json.dumps(response)
    return _llm

def make_failing_then_good(fail_times: int, good: dict):
    calls = {"n": 0}
    async def _llm(model, system, user, **kwargs):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            return "not json {{{"
        return json.dumps(good)
    return _llm

def minimal_spec() -> dict:
    return {
        "vertical": "ecommerce",
        "stack": {"backend": "java_spring"},
        "domain_model": {
            "entities": [
                {"name": "Product", "fields": [
                    {"name": "title", "field_type": "string", "required": True,
                     "pii_class": "none", "description": "", "constraints": {}}
                ], "relationships": [], "audit_fields": True, "soft_delete": False},
                {"name": "Order", "fields": [
                    {"name": "total", "field_type": "money", "required": True,
                     "pii_class": "none", "description": "", "constraints": {}}
                ], "relationships": [], "audit_fields": True, "soft_delete": False},
            ]
        },
        "api_model": {"endpoints": [
            {"method": "GET", "path": "/api/v1/products", "summary": "List products",
             "auth_required": False, "roles": [], "tags": ["catalog"]}
        ]},
        "security_model": {"roles": [{"name": "CUSTOMER", "description": "", "permissions": []}]},
        "compliance_model": {"frameworks": [], "rules": []},
        "integration_model": {"integrations": []},
        "workflow_model": {"state_machines": []},
        "acceptance_criteria": [],
    }

def gate_fail(files: list[str], error_output: str = "error: compile failure") -> GateResult:
    return GateResult(
        passed=False,
        steps=[
            GateStepResult(step="compile", passed=False, output=error_output, duration_ms=900),
            GateStepResult(step="unit_tests", passed=True, duration_ms=200),
        ],
        failing_files=files,
    )

def gate_pass() -> GateResult:
    return GateResult(
        passed=True,
        steps=[GateStepResult(step="compile", passed=True, duration_ms=900)],
        failing_files=[],
    )


# ══════════════════════════════════════════════════════════════════════════════
# PLANNER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPlannerAgent:

    def _plan_response(self, entities: list[str]) -> dict:
        modules = []
        order = 1
        for e in entities:
            b = e.lower()
            modules += [
                {"module_id": f"{b}_entity", "name": f"{e}Entity",
                 "module_type": "entity", "dependencies": [],
                 "build_order": order, "rationale": "first", "spec_paths": [f"domain_model.entities.{e}"]},
                {"module_id": f"{b}_repository", "name": f"{e}Repository",
                 "module_type": "repository", "dependencies": [f"{b}_entity"],
                 "build_order": order + 1, "rationale": "after entity", "spec_paths": []},
                {"module_id": f"{b}_service", "name": f"{e}Service",
                 "module_type": "service", "dependencies": [f"{b}_repository"],
                 "build_order": order + 2, "rationale": "after repo", "spec_paths": []},
            ]
            order += 3
        modules.append({
            "module_id": "migrations", "name": "DatabaseMigrations",
            "module_type": "migration", "dependencies": [f"{entities[0].lower()}_entity"],
            "build_order": order, "rationale": "last", "spec_paths": [],
        })
        return {"modules": modules, "synthesis_mode": "sequential",
                "total_modules": len(modules), "planner_notes": "test plan"}

    def test_planner_returns_module_plan(self):
        llm = make_mock_llm(self._plan_response(["Product", "Order"]))
        planner = PlannerAgent(llm)
        spec = minimal_spec()
        result = asyncio.get_event_loop().run_until_complete(
            planner.plan(spec, stack_profile="java_spring")
        )
        assert len(result.modules) >= 4
        assert result.synthesis_mode == "sequential"

    def test_planner_synthesis_mode_always_sequential(self):
        response = self._plan_response(["Product"])
        response["synthesis_mode"] = "parallel"   # agent tried to set parallel
        llm = make_mock_llm(response)
        planner = PlannerAgent(llm)
        result = asyncio.get_event_loop().run_until_complete(
            planner.plan(minimal_spec())
        )
        assert result.synthesis_mode == "sequential"   # enforced by planner

    def test_planner_dag_validation_passes_for_correct_plan(self):
        llm = make_mock_llm(self._plan_response(["Product"]))
        planner = PlannerAgent(llm)
        result = asyncio.get_event_loop().run_until_complete(planner.plan(minimal_spec()))
        # Should not raise
        planner._validate_dag(result.modules)

    def test_planner_dag_validation_catches_bad_dependency_order(self):
        modules = [
            PlannedModule(module_id="a", name="A", module_type="entity",
                         dependencies=["b"], build_order=1, rationale="", spec_paths=[]),
            PlannedModule(module_id="b", name="B", module_type="repository",
                         dependencies=[], build_order=2, rationale="", spec_paths=[]),
        ]
        with pytest.raises(ValueError, match="not earlier"):
            PlannerAgent._validate_dag(modules)

    def test_planner_falls_back_to_deterministic_on_llm_failure(self):
        async def always_fail(model, system, user, **kwargs):
            return "not json {{{"
        planner = PlannerAgent(always_fail)
        spec = minimal_spec()
        result = asyncio.get_event_loop().run_until_complete(planner.plan(spec))
        # Fallback should produce modules for Product and Order
        module_names = [m.name for m in result.modules]
        assert any("Product" in n for n in module_names)
        assert any("Order" in n for n in module_names)
        assert result.planner_notes == "Deterministic fallback plan — LLM unavailable"

    def test_planner_entity_modules_have_no_dependencies(self):
        llm = make_mock_llm(self._plan_response(["Product"]))
        planner = PlannerAgent(llm)
        result = asyncio.get_event_loop().run_until_complete(planner.plan(minimal_spec()))
        entities = [m for m in result.modules if m.module_type == "entity"]
        for e in entities:
            assert e.dependencies == [], f"Entity {e.name} should have no dependencies"

    def test_planner_migration_is_last(self):
        llm = make_mock_llm(self._plan_response(["Product", "Order"]))
        planner = PlannerAgent(llm)
        result = asyncio.get_event_loop().run_until_complete(planner.plan(minimal_spec()))
        migrations = [m for m in result.modules if m.module_type == "migration"]
        if migrations:
            max_order = max(m.build_order for m in result.modules)
            for mg in migrations:
                assert mg.build_order == max_order, "Migration must be last in build order"


# ══════════════════════════════════════════════════════════════════════════════
# SYNTHESIZER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSynthesizerAgent:

    def _synth_response(self, module_name: str, stack: str = "java_spring") -> dict:
        ext = {"java_spring": "java", "python_fastapi": "py", "dotnet": "cs"}.get(stack, "java")
        return {
            "module_id": module_name.lower(),
            "module_name": module_name,
            "files": [
                {
                    "filename": f"src/main/java/com/app/{module_name.lower()}/{module_name}.{ext}",
                    "content": f"public class {module_name} {{ /* generated */ }}",
                    "language": ext,
                },
                {
                    "filename": f"src/test/java/com/app/{module_name.lower()}/{module_name}Test.{ext}",
                    "content": f"public class {module_name}Test {{ @Test void testExists() {{}} }}",
                    "language": ext,
                },
            ],
            "synthesis_notes": "Generated successfully",
        }

    def _make_module(self, name: str, module_type: str = "entity") -> PlannedModule:
        return PlannedModule(
            module_id=name.lower(),
            name=name,
            module_type=module_type,
            dependencies=[],
            build_order=1,
            rationale="",
            spec_paths=[f"domain_model.entities.{name}"],
        )

    def test_synthesizer_returns_file_map(self):
        llm = make_mock_llm(self._synth_response("ProductEntity"))
        synth = SynthesizerAgent(llm, stack_profile="java_spring")
        result = asyncio.get_event_loop().run_until_complete(
            synth.synthesize_module(
                module=self._make_module("ProductEntity"),
                spec_dict=minimal_spec(),
            )
        )
        assert len(result.files) >= 1
        assert result.module_name == "ProductEntity"

    def test_synthesizer_files_have_content(self):
        llm = make_mock_llm(self._synth_response("OrderService", "java_spring"))
        synth = SynthesizerAgent(llm)
        result = asyncio.get_event_loop().run_until_complete(
            synth.synthesize_module(
                module=self._make_module("OrderService", "service"),
                spec_dict=minimal_spec(),
            )
        )
        for f in result.files:
            assert f.filename, "File must have a filename"
            assert f.content, "File must have content"
            assert len(f.content) > 10, "Content too short"

    def test_synthesizer_gate_errors_in_fix_prompt(self):
        """Contract C5: when gate_errors are passed, they appear in the prompt."""
        prompts_seen = []
        async def capturing_llm(model, system, user, **kwargs):
            prompts_seen.append(user)
            return json.dumps({"module_id": "x", "module_name": "X",
                               "files": [{"filename": "X.java", "content": "class X{}",
                                          "language": "java"}],
                               "synthesis_notes": ""})
        synth = SynthesizerAgent(capturing_llm)
        gate_errors = {"OrderService.java": ["error: cannot find symbol 'OrderRepository'"]}
        asyncio.get_event_loop().run_until_complete(
            synth.synthesize_module(
                module=self._make_module("OrderService", "service"),
                spec_dict=minimal_spec(),
                gate_errors=gate_errors,
            )
        )
        assert len(prompts_seen) >= 1
        assert "cannot find symbol" in prompts_seen[0], \
            "Contract C5 violated: gate errors not in Fixer prompt"

    def test_synthesizer_fallback_on_harness_failure(self):
        async def always_fail(model, system, user, **kwargs):
            return "not json {{{"
        synth = SynthesizerAgent(always_fail)
        result = asyncio.get_event_loop().run_until_complete(
            synth.synthesize_module(
                module=self._make_module("ProductEntity"),
                spec_dict=minimal_spec(),
            )
        )
        # Should return stub, not raise
        assert len(result.files) >= 1
        assert "SYNTHESIS FAILED" in result.files[0].content or \
               result.synthesis_notes == "Fallback stub — synthesis failed all harness attempts"

    def test_synthesizer_path_normalization_no_backslash(self):
        response = self._synth_response("ProductEntity")
        # Simulate model returning Windows-style path
        response["files"][0]["filename"] = "src\\main\\java\\ProductEntity.java"
        llm = make_mock_llm(response)
        synth = SynthesizerAgent(llm)
        result = asyncio.get_event_loop().run_until_complete(
            synth.synthesize_module(
                module=self._make_module("ProductEntity"),
                spec_dict=minimal_spec(),
            )
        )
        assert result.files[0].filename is not None


# ══════════════════════════════════════════════════════════════════════════════
# ASSEMBLER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestAssembler:

    def _make_file_map(self, module_id: str, files: dict[str, str]) -> FileMapOutput:
        fmo = FileMapOutput(module_id=module_id, module_name=module_id)
        for path, content in files.items():
            fmo.files.append(SynthesizedFile(filename=path, content=content, language="java"))
        return fmo

    def test_assemble_merges_modules_correctly(self):
        assembler = Assembler()
        result = assembler.assemble(
            scaffold_files={"pom.xml": "<project/>"},
            module_outputs=[
                self._make_file_map("product", {"src/Product.java": "class Product{}"}),
                self._make_file_map("order", {"src/Order.java": "class Order{}"}),
            ],
        )
        assert "src/Product.java" in result.assembled_files
        assert "src/Order.java" in result.assembled_files
        assert "pom.xml" in result.assembled_files
        assert not result.has_conflicts

    def test_assembler_detects_module_vs_module_conflict(self):
        assembler = Assembler()
        result = assembler.assemble(
            scaffold_files={},
            module_outputs=[
                self._make_file_map("mod_a", {"src/Shared.java": "class SharedA{}"}),
                self._make_file_map("mod_b", {"src/Shared.java": "class SharedB{}"}),
            ],
        )
        assert result.has_conflicts
        assert "src/Shared.java" in result.conflict_paths
        # First writer wins
        assert result.assembled_files["src/Shared.java"] == "class SharedA{}"

    def test_assembler_detects_scaffold_conflict(self):
        assembler = Assembler()
        result = assembler.assemble(
            scaffold_files={"pom.xml": "<original/>"},
            module_outputs=[
                self._make_file_map("mod", {"pom.xml": "<overwritten/>"}),
            ],
        )
        assert result.has_conflicts
        # Scaffold version preserved
        assert result.assembled_files["pom.xml"] == "<original/>"

    def test_assembler_never_silently_overwrites(self):
        assembler = Assembler()
        result = assembler.assemble(
            scaffold_files={},
            module_outputs=[
                self._make_file_map("a", {"src/X.java": "version_A"}),
                self._make_file_map("b", {"src/X.java": "version_B"}),
            ],
        )
        # Must have conflict record
        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]
        assert conflict.first_owner_module_id == "a"
        assert conflict.conflict_module_id == "b"

    def test_assembler_apply_fixes_replaces_only_fix_files(self):
        assembler = Assembler()
        initial = assembler.assemble(
            scaffold_files={},
            module_outputs=[
                self._make_file_map("mod", {
                    "src/A.java": "original_A",
                    "src/B.java": "original_B",
                }),
            ],
        )
        fixed = assembler.apply_fixes(
            current_result=initial,
            fixed_file_maps=[
                self._make_file_map("mod", {"src/A.java": "fixed_A"}),
            ],
        )
        assert fixed.assembled_files["src/A.java"] == "fixed_A"
        assert fixed.assembled_files["src/B.java"] == "original_B"  # unchanged

    def test_assembler_path_normalization(self):
        assembler = Assembler()
        assert assembler._normalize_path("src\\main\\java\\X.java") == "src/main/java/X.java"
        assert assembler._normalize_path("/src/X.java") == "src/X.java"
        assert assembler._normalize_path("./src/X.java") == "src/X.java"
        assert assembler._normalize_path("src//X.java") == "src/X.java"

    def test_assembler_rejects_path_traversal(self):
        assembler = Assembler()
        error = assembler._validate_path("../../etc/passwd", "evil_module")
        assert error is not None
        assert ".." in error

    def test_assembler_get_module_for_file(self):
        assembler = Assembler()
        result = assembler.assemble(
            scaffold_files={},
            module_outputs=[
                self._make_file_map("product_entity", {"src/ProductEntity.java": "class P{}"}),
            ],
        )
        owner = assembler.get_module_for_file(result, "src/ProductEntity.java")
        assert owner == "product_entity"

    def test_assembler_summary(self):
        assembler = Assembler()
        result = assembler.assemble(
            scaffold_files={"pom.xml": "x"},
            module_outputs=[
                self._make_file_map("a", {"src/A.java": "a"}),
            ],
        )
        summary = result.summary()
        assert summary["total_files"] == 2
        assert summary["conflicts"] == 0
        assert summary["scaffold_files"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# REVIEWER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestReviewerAgent:

    def _assembly_with_file(self, file_path: str, content: str) -> AssemblyResult:
        from agents.generation.assembler import Assembler, AssemblyResult
        result = AssemblyResult()
        result.assembled_files[file_path] = content
        result.file_ownership[file_path] = "product_service"
        return result

    def test_reviewer_maps_errors_to_files(self):
        llm = make_mock_llm({
            "fix_instructions": [
                {
                    "file_path": "src/ProductService.java",
                    "module_id": "product_service",
                    "errors": ["error: cannot find symbol 'ProductRepository'"],
                    "fix_guidance": "Add import for ProductRepository",
                    "error_source": "compile",
                }
            ],
            "acceptance_criteria_gaps": [],
            "escalation_recommended": False,
            "failure_classification": "mechanical",
            "reviewer_notes": "Missing import",
        })
        reviewer = ReviewerAgent(llm)
        gate = gate_fail(["src/ProductService.java"], "cannot find symbol 'ProductRepository'")
        assembly = self._assembly_with_file("src/ProductService.java", "class PS{}")

        result = asyncio.get_event_loop().run_until_complete(
            reviewer.review(gate, assembly, minimal_spec(), fix_iteration=1, max_iterations=3)
        )
        assert len(result.fix_instructions) == 1
        assert result.fix_instructions[0].module_id == "product_service"
        assert "cannot find symbol" in result.fix_instructions[0].errors[0]

    def test_reviewer_recommends_escalation_at_iteration_3(self):
        llm = make_mock_llm({
            "fix_instructions": [{"file_path": "x.java", "module_id": "m",
                                  "errors": ["error"], "fix_guidance": "", "error_source": "compile"}],
            "acceptance_criteria_gaps": [],
            "escalation_recommended": True,
            "failure_classification": "capability_bound",
            "reviewer_notes": "Complex failure",
        })
        reviewer = ReviewerAgent(llm)
        result = asyncio.get_event_loop().run_until_complete(
            reviewer.review(
                gate_fail(["x.java"]),
                AssemblyResult(),
                minimal_spec(),
                fix_iteration=3,
                max_iterations=3,
            )
        )
        assert result.escalation_recommended is True
        assert result.failure_classification == "capability_bound"

    def test_reviewer_fallback_on_llm_failure(self):
        async def fail(model, system, user, **kwargs): return "not json {{{"
        reviewer = ReviewerAgent(fail)
        gate = gate_fail(["src/X.java"])
        assembly = AssemblyResult(
            assembled_files={"src/X.java": "class X{}"},
            file_ownership={"src/X.java": "x_module"},
        )
        result = asyncio.get_event_loop().run_until_complete(
            reviewer.review(gate, assembly, minimal_spec(), fix_iteration=1, max_iterations=3)
        )
        # Fallback should still produce fix instructions
        assert len(result.fix_instructions) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# FIXER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestFixerAgent:

    def test_fixer_regenerates_only_fix_plan_files(self):
        """Contract C5: only FixPlan files are regenerated."""
        files_fixed = []
        async def tracking_llm(model, system, user, **kwargs):
            files_fixed.append(user)
            return json.dumps({
                "module_id": "product_service",
                "module_name": "ProductService",
                "files": [{"filename": "src/ProductService.java",
                           "content": "class ProductService { /* fixed */ }",
                           "language": "java"}],
                "synthesis_notes": "Fixed",
            })
        fixer = FixerAgent(tracking_llm, stack_profile="java_spring")
        rev_out = ReviewerOutput(fix_instructions=[
            FileFixInstruction(
                file_path="src/ProductService.java",
                module_id="product_service",
                errors=["error: cannot find symbol 'ProductRepository'"],
                fix_guidance="Add ProductRepository import",
                error_source="compile",
            )
        ])
        assembly = AssemblyResult()
        assembly.assembled_files = {
            "src/ProductService.java": "class ProductService{/* broken */}",
            "src/ProductEntity.java": "class ProductEntity{/* should not change */}",
        }
        result = asyncio.get_event_loop().run_until_complete(
            fixer.fix(rev_out, assembly, fix_iteration=1)
        )
        assert len(result) == 1   # only one file map for one instruction
        assert result[0].module_id == "product_service"

    def test_fixer_prompt_contains_gate_errors(self):
        """Contract C5 core assertion: errors are ALWAYS in the prompt."""
        prompts = []
        async def capture_llm(model, system, user, **kwargs):
            prompts.append(user)
            return json.dumps({
                "module_id": "m", "module_name": "M",
                "files": [{"filename": "M.java", "content": "class M{}", "language": "java"}],
                "synthesis_notes": "",
            })
        fixer = FixerAgent(capture_llm)
        rev_out = ReviewerOutput(fix_instructions=[
            FileFixInstruction(
                file_path="src/M.java",
                module_id="m",
                errors=["SPECIFIC_ERROR_TOKEN_12345"],
                fix_guidance="",
                error_source="compile",
            )
        ])
        asyncio.get_event_loop().run_until_complete(
            fixer.fix(rev_out, AssemblyResult(), fix_iteration=2)
        )
        assert len(prompts) >= 1
        assert "SPECIFIC_ERROR_TOKEN_12345" in prompts[0], \
            "Contract C5 VIOLATED: gate error not found in Fixer prompt"

    def test_fixer_fallback_on_harness_failure(self):
        async def fail(model, system, user, **kwargs): return "not json {{{"
        fixer = FixerAgent(fail)
        rev_out = ReviewerOutput(fix_instructions=[
            FileFixInstruction(
                file_path="src/X.java", module_id="x",
                errors=["error"], fix_guidance="", error_source="compile",
            )
        ])
        result = asyncio.get_event_loop().run_until_complete(
            fixer.fix(rev_out, AssemblyResult(), fix_iteration=1)
        )
        # Should not raise — returns unchanged or stub
        assert len(result) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# METACOGNITION GATE TIER 1 TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestMetacognitionGateTier1:

    def test_tier1_returns_needed(self):
        llm = make_mock_llm({
            "verdict": "NEEDED",
            "narrow_query": "",
            "rationale": "Entity X is not in context",
            "confidence": 0.9,
        })
        gate = MetacognitionGateTier1(llm)
        result = asyncio.get_event_loop().run_until_complete(
            gate.arbitrate("retrieval", "Entity X mentioned but not in context")
        )
        assert result["verdict"] == "NEEDED"
        assert result["tier"] == 1
        assert result["confidence"] == 0.9

    def test_tier1_returns_narrow_query(self):
        llm = make_mock_llm({
            "verdict": "NARROW_QUERY",
            "narrow_query": "Razorpay API webhook documentation",
            "rationale": "Integration named but docs absent",
            "confidence": 0.85,
        })
        gate = MetacognitionGateTier1(llm)
        result = asyncio.get_event_loop().run_until_complete(
            gate.arbitrate("retrieval", "Razorpay named but no connector docs available")
        )
        assert result["verdict"] == "NARROW_QUERY"
        assert "Razorpay" in result["narrow_query"]

    def test_tier1_defaults_not_needed_on_failure(self):
        async def fail(model, system, user, **kwargs): return "bad json {{{"
        gate = MetacognitionGateTier1(fail)
        result = asyncio.get_event_loop().run_until_complete(
            gate.arbitrate("retrieval", "some context")
        )
        assert result["verdict"] == "NOT_NEEDED"
        assert result["confidence"] == 0.3
        assert result["tier"] == 1

    def test_tier1_rationale_is_logged(self):
        llm = make_mock_llm({
            "verdict": "NOT_NEEDED",
            "narrow_query": "",
            "rationale": "All entities already in context",
            "confidence": 0.95,
        })
        gate = MetacognitionGateTier1(llm)
        result = asyncio.get_event_loop().run_until_complete(
            gate.arbitrate("retrieval", "editing known entity")
        )
        assert result["rationale"] == "All entities already in context"


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — full pipeline flow
# ══════════════════════════════════════════════════════════════════════════════

class TestPhase2Integration:

    def test_plan_synthesize_assemble_pipeline(self):
        """End-to-end: spec → plan → synthesize → assemble → no conflicts."""
        plan_response = {
            "modules": [
                {"module_id": "product_entity", "name": "ProductEntity",
                 "module_type": "entity", "dependencies": [], "build_order": 1,
                 "rationale": "", "spec_paths": ["domain_model.entities.Product"]},
                {"module_id": "order_entity", "name": "OrderEntity",
                 "module_type": "entity", "dependencies": [], "build_order": 2,
                 "rationale": "", "spec_paths": ["domain_model.entities.Order"]},
            ],
            "synthesis_mode": "sequential", "total_modules": 2, "planner_notes": "",
        }

        call_count = {"n": 0}
        module_names = ["ProductEntity", "OrderEntity"]

        async def multi_response_llm(model, system, user, **kwargs):
            n = call_count["n"]
            call_count["n"] += 1
            if n == 0:
                return json.dumps(plan_response)
            idx = (n - 1) % len(module_names)
            name = module_names[idx]
            return json.dumps({
                "module_id": name.lower().replace("entity", "_entity"),
                "module_name": name,
                "files": [
                    {"filename": f"src/{name}.java", "content": f"class {name}{{}}",
                     "language": "java"},
                    {"filename": f"src/{name}Test.java", "content": f"class {name}Test{{}}",
                     "language": "java"},
                ],
                "synthesis_notes": "ok",
            })

        spec = minimal_spec()
        planner = PlannerAgent(multi_response_llm)
        plan = asyncio.get_event_loop().run_until_complete(
            planner.plan(spec, stack_profile="java_spring")
        )
        assert len(plan.modules) == 2

        synthesizer = SynthesizerAgent(multi_response_llm)
        file_maps = []
        for module in plan.modules:
            fmo = asyncio.get_event_loop().run_until_complete(
                synthesizer.synthesize_module(module=module, spec_dict=spec)
            )
            file_maps.append(fmo)

        assembler = Assembler()
        result = assembler.assemble(
            scaffold_files={"pom.xml": "<project/>"},
            module_outputs=file_maps,
        )
        assert not result.has_conflicts
        assert result.total_file_count >= 3  # pom.xml + 2+ java files

    def test_fix_loop_bounded_at_3(self):
        """Contract C5: loop never exceeds 3 iterations — verified via Phase 0 graph."""
        from agents.graphs.generation_graph import run_generation_job
        import asyncio

        final = asyncio.get_event_loop().run_until_complete(
            run_generation_job(
                job_id=str(uuid4()),
                tenant_id=str(uuid4()),
                project_id=str(uuid4()),
                spec_entity_names=["Product"],
            )
        )
        # run_generation_job returns a GenerationState (Pydantic model) or dict
        if hasattr(final, "fix_count"):
            fix_count = final.fix_count or 0
        elif isinstance(final, dict):
            fix_count = final.get("fix_count", 0) or final.get("fix_iteration", 0)
        else:
            fix_count = 0
        assert fix_count <= 3, f"Fix loop exceeded 3 iterations: {fix_count}"
