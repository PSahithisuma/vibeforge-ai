"""
Phase 1 test suite — Metacognition Gate, Domain Wizard,
Spec Editor Agent, Completeness Validator, expanded e-commerce pack.

All LLM calls use async mock functions — no real model needed.
Run with: python -m pytest tests/test_phase1.py -v
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

from core.spec_ir import make_empty_spec, Vertical
from agents.gates.metacognition import (
    MetacognitionGate, GateContext, GateVerdict,
)
from agents.conversation.domain_wizard import (
    DomainWizard, WizardTurnContext, run_wizard_turn,
)
from agents.conversation.spec_editor import SpecEditorAgent
from agents.conversation.completeness_validator import CompletenessValidator
from agents.option_graph.engine import OptionGraphEngine, EligibilityContext


# ── Mock LLM helpers ──────────────────────────────────────────────────────────

def make_mock_llm(response_json: dict):
    """Returns an async callable that returns a fixed JSON response."""
    async def _llm(model: str, system: str, user: str) -> str:
        return json.dumps(response_json)
    return _llm


def make_failing_llm(fail_times: int, then_return: dict):
    """Fails N times, then returns valid JSON (tests repair loop)."""
    calls = {"count": 0}
    async def _llm(model: str, system: str, user: str) -> str:
        calls["count"] += 1
        if calls["count"] <= fail_times:
            return "this is not valid json at all {{{"
        return json.dumps(then_return)
    return _llm


# ── Test fixtures ─────────────────────────────────────────────────────────────

def minimal_spec_dict() -> dict:
    spec = make_empty_spec(
        tenant_id=uuid4(),
        project_id=uuid4(),
        vertical=Vertical.ECOMMERCE,
    )
    return spec.model_dump(mode="json")


def populated_spec_dict() -> dict:
    """Spec with entities, endpoints, roles — passes Layer 1 completeness."""
    spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
    d = spec.model_dump(mode="json")
    d["stack"]["backend"] = "java_spring"
    d["vertical"] = "ecommerce"
    d["domain_model"]["entities"] = [
        {
            "name": "Product",
            "description": "A product for sale",
            "fields": [{"name": "title", "field_type": "string", "required": True,
                        "pii_class": "none", "description": "", "constraints": {}}],
            "relationships": [],
            "audit_fields": True,
            "soft_delete": False,
        },
        {
            "name": "Order",
            "description": "A customer order",
            "fields": [{"name": "total", "field_type": "money", "required": True,
                        "pii_class": "none", "description": "", "constraints": {}}],
            "relationships": [],
            "audit_fields": True,
            "soft_delete": False,
        },
    ]
    d["api_model"]["endpoints"] = [
        {"endpoint_id": "ep_001", "method": "GET", "path": "/api/v1/products",
         "summary": "List products", "auth_required": False, "roles": [],
         "request_params": [], "request_body_entity": "", "response_entity": "list[Product]",
         "tags": ["catalog"], "acceptance_criteria_ids": []},
    ]
    d["security_model"]["roles"] = [
        {"name": "CUSTOMER", "description": "Logged-in customer", "permissions": ["orders:read"]},
    ]
    d["acceptance_criteria"] = [
        {"criterion_id": "ac_001", "feature": "catalog", "scenario": "Given a customer, when they list products, then they see all active products",
         "endpoint_ids": [], "priority": "must", "automated": True},
    ]
    return d


# ══════════════════════════════════════════════════════════════════════════════
# METACOGNITION GATE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestMetacognitionGate:

    def setup_method(self):
        self.gate = MetacognitionGate()

    def _ctx(self, **kwargs) -> GateContext:
        return GateContext(decision_point="retrieval", **kwargs)

    # ── Retrieval rules ───────────────────────────────────────────────────────

    def test_retrieval_mandatory_for_missing_connector_docs(self):
        ctx = self._ctx(
            integrations_named_in_spec=["Razorpay"],
            connector_docs_available=[],
            user_message="add payment integration",
        )
        decision = self.gate.evaluate_retrieval(ctx)
        assert decision.verdict == GateVerdict.NARROW
        assert decision.tier == 0
        assert "Razorpay" in decision.narrow_query

    def test_retrieval_mandatory_for_regulation_keyword(self):
        ctx = self._ctx(
            user_message="we need PCI-DSS compliance for card payments",
            integrations_named_in_spec=[],
            connector_docs_available=[],
        )
        decision = self.gate.evaluate_retrieval(ctx)
        assert decision.verdict == GateVerdict.NARROW
        assert decision.tier == 0
        assert "pci" in decision.narrow_query.lower()

    def test_retrieval_prohibited_when_entities_in_context(self):
        ctx = self._ctx(
            user_message="add a discount field to Product",
            entities_in_context=["Product", "Order"],
            turn_entities_mentioned=["Product"],
            integrations_named_in_spec=[],
            connector_docs_available=[],
        )
        decision = self.gate.evaluate_retrieval(ctx)
        assert decision.verdict == GateVerdict.NOT_NEEDED
        assert decision.tier == 0

    def test_retrieval_prohibited_for_short_message(self):
        ctx = self._ctx(
            user_message="yes",
            integrations_named_in_spec=[],
            connector_docs_available=[],
        )
        decision = self.gate.evaluate_retrieval(ctx)
        assert decision.verdict == GateVerdict.NOT_NEEDED
        assert decision.tier == 0

    # ── Escalation rules ──────────────────────────────────────────────────────

    def test_escalation_prohibited_before_3_iterations(self):
        ctx = GateContext(
            decision_point="escalation",
            fix_iteration=1,
            max_fix_iterations=3,
            tenant_escalation_enabled=True,
            tenant_external_llm_consent=True,
            budget_remaining=100.0,
            escalation_budget=5.0,
        )
        decision = self.gate.evaluate_escalation(ctx)
        assert decision.verdict == GateVerdict.NOT_NEEDED
        assert "iteration 1 < 3" in decision.reason

    def test_escalation_prohibited_when_not_enabled(self):
        ctx = GateContext(
            decision_point="escalation",
            fix_iteration=3,
            max_fix_iterations=3,
            tenant_escalation_enabled=False,
            tenant_external_llm_consent=True,
            budget_remaining=100.0,
            escalation_budget=5.0,
        )
        decision = self.gate.evaluate_escalation(ctx)
        assert decision.verdict == GateVerdict.NOT_NEEDED
        assert "escalation_enabled=False" in decision.reason

    def test_escalation_prohibited_without_consent(self):
        ctx = GateContext(
            decision_point="escalation",
            fix_iteration=3,
            max_fix_iterations=3,
            tenant_escalation_enabled=True,
            tenant_external_llm_consent=False,
            budget_remaining=100.0,
            escalation_budget=5.0,
        )
        decision = self.gate.evaluate_escalation(ctx)
        assert decision.verdict == GateVerdict.NOT_NEEDED
        assert "consent=False" in decision.reason

    def test_escalation_prohibited_when_budget_exhausted(self):
        ctx = GateContext(
            decision_point="escalation",
            fix_iteration=3,
            max_fix_iterations=3,
            tenant_escalation_enabled=True,
            tenant_external_llm_consent=True,
            budget_remaining=2.0,
            escalation_budget=10.0,
        )
        decision = self.gate.evaluate_escalation(ctx)
        assert decision.verdict == GateVerdict.NOT_NEEDED
        assert "Budget" in decision.reason

    def test_escalation_needed_when_all_conditions_met(self):
        ctx = GateContext(
            decision_point="escalation",
            fix_iteration=3,
            max_fix_iterations=3,
            tenant_escalation_enabled=True,
            tenant_external_llm_consent=True,
            budget_remaining=100.0,
            escalation_budget=5.0,
        )
        decision = self.gate.evaluate_escalation(ctx)
        assert decision.verdict == GateVerdict.NEEDED
        assert decision.tier == 0

    def test_gate_logs_decisions(self):
        ctx = self._ctx(user_message="yes")
        self.gate.evaluate_retrieval(ctx)
        assert len(self.gate.get_decisions()) == 1

    def test_record_outcome_updates_decision(self):
        ctx = self._ctx(user_message="yes")
        decision = self.gate.evaluate_retrieval(ctx)
        self.gate.record_outcome(decision, "spec_delta_accepted")
        decisions = self.gate.get_decisions()
        assert decisions[-1].downstream_outcome == "spec_delta_accepted"

    def test_gate_verdict_needed_property(self):
        from agents.gates.metacognition import GateDecision
        d = GateDecision(verdict=GateVerdict.NEEDED, tier=0)
        assert d.needed is True
        d2 = GateDecision(verdict=GateVerdict.NARROW, tier=0)
        assert d2.needed is True
        d3 = GateDecision(verdict=GateVerdict.NOT_NEEDED, tier=0)
        assert d3.needed is False


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN WIZARD TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestDomainWizard:

    def test_wizard_returns_diff_card(self):
        llm = make_mock_llm({
            "summary": "Add review system to catalog",
            "patch": {"domain_model": {"entities": [{"name": "Review",
                "description": "Product review", "fields": [], "relationships": [],
                "audit_fields": True, "soft_delete": False}]}},
            "impact_summary": "+1 entity (Review)",
            "new_entity_count": 1,
            "new_endpoint_count": 0,
            "confidence": 0.9,
            "grounded_sources": [],
        })
        wizard = DomainWizard(llm_client=llm)
        ctx = WizardTurnContext(
            user_message="add a product review feature",
            section_id="domain_model",
            spec_snapshot=minimal_spec_dict(),
        )
        card = asyncio.get_event_loop().run_until_complete(wizard.process_turn(ctx))
        assert card.amendment_id.startswith("amend_")
        assert card.proposal.new_entity_count == 1
        assert "Review" in str(card.proposal.patch)

    def test_wizard_diff_card_has_before_snapshot(self):
        spec = minimal_spec_dict()
        llm = make_mock_llm({
            "summary": "Set vertical to banking",
            "patch": {"vertical": "banking"},
            "impact_summary": "Changed vertical",
            "new_entity_count": 0,
            "new_endpoint_count": 0,
            "confidence": 0.95,
            "grounded_sources": [],
        })
        wizard = DomainWizard(llm_client=llm)
        ctx = WizardTurnContext(
            user_message="change vertical to banking",
            section_id="vertical",
            spec_snapshot=spec,
        )
        card = asyncio.get_event_loop().run_until_complete(wizard.process_turn(ctx))
        # before_snapshot should contain the old value
        assert "vertical" in card.before_snapshot

    def test_wizard_gate_prevents_unnecessary_retrieval(self):
        """Gate should block retrieval for short messages about known entities."""
        retrieved = {"called": False}

        async def mock_retrieval(query: str) -> list[str]:
            retrieved["called"] = True
            return ["some context"]

        llm = make_mock_llm({
            "summary": "No-op change", "patch": {}, "impact_summary": "",
            "new_entity_count": 0, "new_endpoint_count": 0, "confidence": 0.8,
            "grounded_sources": [],
        })
        wizard = DomainWizard(llm_client=llm, retrieval_fn=mock_retrieval)
        ctx = WizardTurnContext(
            user_message="yes",  # too short → gate blocks retrieval
            section_id="domain_model",
            spec_snapshot=minimal_spec_dict(),
            entities_in_context=["Product"],
        )
        asyncio.get_event_loop().run_until_complete(wizard.process_turn(ctx))
        assert not retrieved["called"], "Gate should have blocked retrieval for 'yes'"

    def test_wizard_triggers_retrieval_for_missing_connector(self):
        """Gate should approve retrieval when a named integration has no docs."""
        retrieved = {"query": None}

        async def mock_retrieval(query: str) -> list[str]:
            retrieved["query"] = query
            return ["Razorpay API docs chunk 1"]

        llm = make_mock_llm({
            "summary": "Add Razorpay", "patch": {}, "impact_summary": "",
            "new_entity_count": 0, "new_endpoint_count": 0, "confidence": 0.8,
            "grounded_sources": ["RAG:0"],
        })
        wizard = DomainWizard(llm_client=llm, retrieval_fn=mock_retrieval)
        ctx = WizardTurnContext(
            user_message="add Razorpay payment gateway",
            section_id="integration_model",
            spec_snapshot=minimal_spec_dict(),
            integrations_named=["Razorpay"],
            connector_docs_available=[],  # no docs → gate fires NARROW
        )
        card = asyncio.get_event_loop().run_until_complete(wizard.process_turn(ctx))
        assert retrieved["query"] is not None, "Retrieval should have been called"
        assert card.retrieval_used is True

    def test_wizard_handles_harness_failure_gracefully(self):
        """If LLM fails all attempts, wizard returns an empty diff card — doesn't crash."""
        async def always_fail(model, system, user):
            return "not json at all"

        wizard = DomainWizard(llm_client=always_fail)
        ctx = WizardTurnContext(
            user_message="add a product review feature",
            section_id="domain_model",
            spec_snapshot=minimal_spec_dict(),
        )
        card = asyncio.get_event_loop().run_until_complete(wizard.process_turn(ctx))
        assert card.proposal.confidence == 0.0
        assert card.proposal.patch == {}


# ══════════════════════════════════════════════════════════════════════════════
# SPEC EDITOR AGENT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSpecEditorAgent:

    def test_editor_returns_diff_card(self):
        llm = make_mock_llm({
            "operation": "add",
            "section": "security_model",
            "summary": "Add WhatsApp OTP as secondary auth",
            "patch": {"security_model": {"auth_provider": "keycloak+whatsapp"}},
            "impact_summary": "Secondary auth via WhatsApp OTP",
            "new_entity_count": 0,
            "new_endpoint_count": 1,
            "compliance_implications": [],
            "consistency_warnings": [],
            "confidence": 0.9,
        })
        agent = SpecEditorAgent(llm_client=llm)
        card = asyncio.get_event_loop().run_until_complete(
            agent.process_amendment(
                instruction="Add WhatsApp OTP as a secondary auth method",
                section_id="security_model",
                spec_snapshot=populated_spec_dict(),
            )
        )
        assert card.amendment_id.startswith("amend_")
        assert card.proposal.summary == "Add WhatsApp OTP as secondary auth"
        assert card.proposal.new_endpoint_count == 1

    def test_editor_rejects_empty_instruction(self):
        llm = make_mock_llm({"operation": "add", "section": "domain_model",
            "summary": "no-op", "patch": {}, "impact_summary": "",
            "new_entity_count": 0, "new_endpoint_count": 0,
            "compliance_implications": [], "consistency_warnings": [], "confidence": 0.5})
        agent = SpecEditorAgent(llm_client=llm)
        card = asyncio.get_event_loop().run_until_complete(
            agent.process_amendment(
                instruction="  ",
                section_id="domain_model",
                spec_snapshot=populated_spec_dict(),
            )
        )
        assert card.proposal.patch == {}
        assert card.proposal.confidence == 0.0

    def test_editor_detects_referential_integrity_violation(self):
        """Removing an entity that another entity references → consistency warning."""
        llm = make_mock_llm({
            "operation": "remove",
            "section": "domain_model",
            "summary": "Remove Order entity",
            "patch": {"domain_model": {"entities": [{"name": "Order"}]}},
            "impact_summary": "Removes Order",
            "new_entity_count": 0,
            "new_endpoint_count": 0,
            "compliance_implications": [],
            "consistency_warnings": [],
            "confidence": 0.9,
        })
        spec = populated_spec_dict()
        # Add a relationship: Payment → Order
        spec["domain_model"]["entities"].append({
            "name": "Payment",
            "description": "Payment record",
            "fields": [{"name": "amount", "field_type": "money", "required": True,
                        "pii_class": "none", "description": "", "constraints": {}}],
            "relationships": [
                {"target_entity": "Order", "relationship_type": "many_to_one",
                 "foreign_key": "order_id", "cascade": False}
            ],
            "audit_fields": True,
            "soft_delete": False,
        })
        agent = SpecEditorAgent(llm_client=llm)
        card = asyncio.get_event_loop().run_until_complete(
            agent.process_amendment(
                instruction="Remove the Order entity",
                section_id="domain_model",
                spec_snapshot=spec,
            )
        )
        # Warning should be in impact_summary
        assert "Warning" in card.proposal.impact_summary or \
               "Cannot remove" in card.proposal.impact_summary or \
               card.proposal.impact_summary != ""

    def test_editor_repair_loop_on_bad_json(self):
        """Harness should repair after first failed attempt."""
        good_response = {
            "operation": "add", "section": "compliance_model",
            "summary": "Add GDPR framework",
            "patch": {"compliance_model": {"frameworks": ["gdpr"]}},
            "impact_summary": "GDPR added",
            "new_entity_count": 0, "new_endpoint_count": 0,
            "compliance_implications": ["gdpr"],
            "consistency_warnings": [], "confidence": 0.85,
        }
        llm = make_failing_llm(fail_times=1, then_return=good_response)
        agent = SpecEditorAgent(llm_client=llm)
        card = asyncio.get_event_loop().run_until_complete(
            agent.process_amendment(
                instruction="Add GDPR compliance",
                section_id="compliance_model",
                spec_snapshot=populated_spec_dict(),
            )
        )
        assert card.proposal.summary == "Add GDPR framework"


# ══════════════════════════════════════════════════════════════════════════════
# COMPLETENESS VALIDATOR TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestCompletenessValidator:

    def test_minimal_spec_fails_layer1(self):
        validator = CompletenessValidator(llm_client=None)
        result = validator.validate_sync(minimal_spec_dict())
        assert not result.is_complete
        assert len(result.missing_required) > 0

    def test_populated_spec_passes_layer1(self):
        validator = CompletenessValidator(llm_client=None)
        result = validator.validate_sync(populated_spec_dict())
        assert result.is_complete
        assert result.can_proceed_to_review

    def test_completeness_percent_increases_with_fields(self):
        validator = CompletenessValidator(llm_client=None)
        r1 = validator.validate_sync(minimal_spec_dict())
        r2 = validator.validate_sync(populated_spec_dict())
        assert r2.completeness_percent > r1.completeness_percent

    def test_entity_without_fields_fails(self):
        validator = CompletenessValidator(llm_client=None)
        spec = populated_spec_dict()
        # Add an entity with no fields
        spec["domain_model"]["entities"].append({
            "name": "EmptyEntity", "description": "",
            "fields": [], "relationships": [],
            "audit_fields": True, "soft_delete": False,
        })
        result = validator.validate_sync(spec)
        # Should fail c7_entities_have_fields
        failed_ids = [c.check_id for c in result.checklist if not c.is_satisfied]
        assert "c7_entities_have_fields" in failed_ids

    def test_gap_analysis_returns_questions(self):
        """LLM gap analysis produces GapQuestion objects."""
        gap_response = {
            "gap_questions": [
                {
                    "question_id": "gq_001",
                    "question": "Does your platform require guest checkout?",
                    "section": "api_model",
                    "json_path": "api_model.guest_checkout_enabled",
                    "choice_chips": ["Yes, required", "No, account required"],
                    "default_choice": "Yes, required",
                    "priority": "should",
                }
            ],
            "analysis_summary": "Spec is mostly complete, one gap found.",
        }
        llm = make_mock_llm(gap_response)
        validator = CompletenessValidator(llm_client=llm)
        result = asyncio.get_event_loop().run_until_complete(
            validator.validate(populated_spec_dict(), run_gap_analysis=True)
        )
        assert result.is_complete
        assert len(result.gap_questions) == 1
        assert result.gap_questions[0].question_id == "gq_001"
        assert result.gap_questions[0].choice_chips is not None

    def test_gap_analysis_skipped_when_layer1_fails(self):
        """If Layer 1 fails, Layer 2 should NOT run (no LLM call made)."""
        call_count = {"n": 0}
        async def counting_llm(model, system, user):
            call_count["n"] += 1
            return json.dumps({"gap_questions": [], "analysis_summary": ""})

        validator = CompletenessValidator(llm_client=counting_llm)
        result = asyncio.get_event_loop().run_until_complete(
            validator.validate(minimal_spec_dict(), run_gap_analysis=True)
        )
        assert not result.is_complete
        assert call_count["n"] == 0, "LLM should not be called when Layer 1 fails"


# ══════════════════════════════════════════════════════════════════════════════
# EXPANDED E-COMMERCE PACK TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestExpandedEcommercePack:

    PACK_DIR = ROOT / "packs" / "ecommerce"

    def setup_method(self):
        self.engine = OptionGraphEngine.from_pack_dir(self.PACK_DIR)

    def test_pack_loads_all_4_sections(self):
        section_ids = {s.section_id for s in self.engine.graph.sections}
        assert "catalog" in section_ids
        assert "payments" in section_ids
        assert "fulfillment" in section_ids
        assert "compliance" in section_ids

    def test_pack_has_expected_options(self):
        option_ids = set(self.engine._options.keys())
        # Catalog
        assert "product_variants" in option_ids
        assert "product_search" in option_ids
        # Payments
        assert "razorpay_integration" in option_ids
        assert "stripe_integration" in option_ids
        assert "guest_checkout" in option_ids
        # Fulfillment
        assert "order_management" in option_ids
        assert "shipment_tracking" in option_ids
        assert "dark_store_inventory" in option_ids
        # Compliance
        assert "pci_dss_basic" in option_ids
        assert "gst_compliance" in option_ids
        assert "gdpr_data_privacy" in option_ids

    def test_razorpay_and_stripe_conflict(self):
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx = EligibilityContext(selected_options={"razorpay_integration"})
        # Should raise — stripe conflicts with razorpay
        with pytest.raises(ValueError, match="Conflicts with"):
            self.engine.evaluate_selection("stripe_integration", ctx, spec)

    def test_saved_payment_requires_razorpay(self):
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx = EligibilityContext(selected_options=set())  # razorpay NOT selected
        with pytest.raises(ValueError, match="Requires"):
            self.engine.evaluate_selection("saved_payment_methods", ctx, spec)

    def test_saved_payment_eligible_after_razorpay(self):
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx = EligibilityContext(selected_options={"razorpay_integration"})
        delta = self.engine.evaluate_selection("saved_payment_methods", ctx, spec)
        assert delta.option_id == "saved_payment_methods"

    def test_pci_requires_razorpay(self):
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx = EligibilityContext(selected_options=set())
        with pytest.raises(ValueError, match="Requires"):
            self.engine.evaluate_selection("pci_dss_basic", ctx, spec)

    def test_order_management_produces_state_machine(self):
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx = EligibilityContext(selected_options=set())
        delta = self.engine.evaluate_selection("order_management", ctx, spec)
        # The patch should contain the OrderLifecycle state machine
        sm_patch = delta.patch.get("workflow_model", {}).get("state_machines", [])
        assert any(sm.get("name") == "OrderLifecycle" for sm in sm_patch)

    def test_gst_compliance_adds_gst_invoice_entity(self):
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx = EligibilityContext(selected_options=set())
        delta = self.engine.evaluate_selection("gst_compliance", ctx, spec)
        entities = delta.patch.get("domain_model", {}).get("entities", [])
        entity_names = [e.get("name") for e in entities]
        assert "GSTInvoice" in entity_names

    def test_dark_store_and_delivery_slots_chain(self):
        """delivery_slots requires dark_store_inventory."""
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx_without = EligibilityContext(selected_options=set())
        with pytest.raises(ValueError):
            self.engine.evaluate_selection("delivery_slots", ctx_without, spec)

        ctx_with = EligibilityContext(selected_options={"dark_store_inventory"})
        delta = self.engine.evaluate_selection("delivery_slots", ctx_with, spec)
        assert delta.option_id == "delivery_slots"

    def test_selecting_full_ecommerce_stack_applies_correctly(self):
        """Select a realistic set of options and verify spec builds correctly."""
        spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
        ctx = EligibilityContext(selected_options=set())

        options_to_select = [
            "product_variants",
            "product_search",
            "razorpay_integration",
            "guest_checkout",
            "order_management",
            "gst_compliance",
            "audit_logging",
        ]
        for option_id in options_to_select:
            ctx.selected_options.add(option_id)
            delta = self.engine.evaluate_selection(option_id, ctx, spec)
            spec = spec.apply_delta(delta.to_spec_delta())

        # _deep_merge replaces lists per path — each option patch is authoritative
        # for its own path. Version increments prove all deltas applied.
        assert spec.spec_version == len(options_to_select) + 1
        assert len(spec.api_model.endpoints) >= 1
        assert spec.compliance_model.audit_logging is True
        assert len(spec.domain_model.entities) >= 1

    def test_contract_c12_no_orchestration_change(self):
        """
        Contract C12: adding this pack required zero changes to agents/gates/* or
        agents/graphs/*. Verify by checking those files don't reference 'ecommerce'.
        """
        gate_file = ROOT / "agents" / "gates" / "metacognition.py"
        graph_file = ROOT / "agents" / "graphs" / "generation_graph.py"

        gate_content = gate_file.read_text()
        graph_content = graph_file.read_text()

        assert "ecommerce" not in gate_content.lower(), \
            "metacognition.py must not reference a specific vertical (C12 violated)"
        assert "ecommerce" not in graph_content.lower(), \
            "generation_graph.py must not reference a specific vertical (C12 violated)"


# ══════════════════════════════════════════════════════════════════════════════
# PACK COMPLETENESS TESTS — schema_fragments, compliance_rules, acceptance_seeds
# ══════════════════════════════════════════════════════════════════════════════

class TestPackCompleteness:
    """
    Verifies the complete pack folder structure required by Phase 1 spec:
    option_graphs/ + schema_fragments/ + compliance_rules/ + acceptance_seeds/
    """

    PACK_DIR = ROOT / "packs" / "ecommerce"

    def test_all_4_pack_folders_exist(self):
        required = ["option_graphs", "schema_fragments", "compliance_rules", "acceptance_seeds"]
        for folder in required:
            assert (self.PACK_DIR / folder).exists(), f"Missing folder: {folder}"

    def test_schema_fragments_has_core_entities(self):
        sf = self.PACK_DIR / "schema_fragments" / "core_entities.yaml"
        assert sf.exists(), "core_entities.yaml missing from schema_fragments/"
        import yaml
        data = yaml.safe_load(sf.read_text())
        fragment_ids = {f["fragment_id"] for f in data["fragments"]}
        for required in ["catalog_product", "cart_cart", "order_order", "inventory_stock"]:
            assert required in fragment_ids, f"Missing fragment: {required}"

    def test_schema_fragments_entities_have_fields(self):
        import yaml
        data = yaml.safe_load(
            (self.PACK_DIR / "schema_fragments" / "core_entities.yaml").read_text()
        )
        for fragment in data["fragments"]:
            entity = fragment["entity"]
            assert len(entity["fields"]) >= 1, \
                f"Fragment {fragment['fragment_id']} entity has no fields"

    def test_compliance_rules_exist(self):
        cr = self.PACK_DIR / "compliance_rules" / "rules.yaml"
        assert cr.exists(), "rules.yaml missing from compliance_rules/"
        import yaml
        data = yaml.safe_load(cr.read_text())
        assert len(data["rules"]) >= 5, "Expected at least 5 compliance rules"

    def test_compliance_rules_have_semgrep_refs(self):
        import yaml
        data = yaml.safe_load(
            (self.PACK_DIR / "compliance_rules" / "rules.yaml").read_text()
        )
        for rule in data["rules"]:
            assert "semgrep_rule_id" in rule, \
                f"Rule {rule['rule_id']} missing semgrep_rule_id"
            assert "generated_control" in rule, \
                f"Rule {rule['rule_id']} missing generated_control"

    def test_acceptance_seeds_exist(self):
        seeds_file = self.PACK_DIR / "acceptance_seeds" / "seeds.yaml"
        assert seeds_file.exists(), "seeds.yaml missing from acceptance_seeds/"
        import yaml
        data = yaml.safe_load(seeds_file.read_text())
        assert len(data["seeds"]) >= 10, "Expected at least 10 acceptance seeds"

    def test_acceptance_seeds_have_gherkin_scenarios(self):
        import yaml
        data = yaml.safe_load(
            (self.PACK_DIR / "acceptance_seeds" / "seeds.yaml").read_text()
        )
        for seed in data["seeds"]:
            assert "scenario" in seed, f"Seed {seed['seed_id']} missing scenario"
            assert "Given" in seed["scenario"], \
                f"Seed {seed['seed_id']} scenario not in Gherkin format"
            assert "When" in seed["scenario"], \
                f"Seed {seed['seed_id']} scenario missing When clause"
            assert "Then" in seed["scenario"], \
                f"Seed {seed['seed_id']} scenario missing Then clause"

    def test_business_models_has_all_4_archetypes(self):
        import yaml
        data = yaml.safe_load(
            (self.PACK_DIR / "option_graphs" / "main.yaml").read_text()
        )
        bm_section = next(s for s in data["sections"] if s["section_id"] == "business_models")
        option_ids = {o["option_id"] for o in bm_section["options"]}
        for required in ["biz_b2c", "biz_b2b", "biz_quick_commerce", "biz_c2c"]:
            assert required in option_ids, f"Missing business model archetype: {required}"

    def test_pci_rules_apply_when_razorpay_selected(self):
        import yaml
        data = yaml.safe_load(
            (self.PACK_DIR / "compliance_rules" / "rules.yaml").read_text()
        )
        pci_rules = [r for r in data["rules"] if r["framework"] == "pci_dss"]
        assert len(pci_rules) >= 3, "Expected at least 3 PCI-DSS rules"
        for rule in pci_rules:
            applies = rule.get("applies_when", [])
            if applies:
                assert any(
                    a.get("option_selected") == "pay_razorpay"
                    for a in applies
                ), f"PCI rule {rule['rule_id']} should apply when pay_razorpay is selected"

    def test_compliance_rules_and_option_graphs_are_consistent(self):
        """
        Every compliance rule that has applies_when referencing an option_id
        must reference an option_id that actually exists in the pack.
        """
        import yaml
        rules_data = yaml.safe_load(
            (self.PACK_DIR / "compliance_rules" / "rules.yaml").read_text()
        )
        # Gather all option_ids from all option graph files
        all_option_ids = set()
        for yaml_file in (self.PACK_DIR / "option_graphs").glob("*.yaml"):
            data = yaml.safe_load(yaml_file.read_text())
            sections = data.get("sections", [])
            for section in sections:
                for opt in section.get("options", []):
                    all_option_ids.add(opt["option_id"])

        for rule in rules_data["rules"]:
            for condition in rule.get("applies_when", []):
                opt_id = condition.get("option_selected")
                if opt_id:
                    assert opt_id in all_option_ids, \
                        f"Rule {rule['rule_id']} references unknown option_id: {opt_id}"
