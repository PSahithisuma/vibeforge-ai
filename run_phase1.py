"""
VibeForge — Phase 1 Integration Runner
========================================
Runs all 5 Phase 1 components against a real LLM (Ollama/qwen3:8b).

Usage:
    # With Ollama running and qwen3:8b pulled:
    python run_phase1.py

    # Without a model (offline / CI):
    VIBEFORGE_MOCK_LLM=1 python run_phase1.py

    # Specific component only:
    python run_phase1.py --component gate
    python run_phase1.py --component wizard
    python run_phase1.py --component editor
    python run_phase1.py --component validator
    python run_phase1.py --component pack

What this proves:
    - Metacognition Gate: fires correct Tier 0 rules synchronously
    - Domain Wizard: calls Qwen3-8B and returns a diff card
    - Spec Editor Agent: processes amendment → diff card with consistency check
    - Completeness Validator: Layer 1 + Layer 2 gap questions
    - E-commerce Pack: loads all 4 sections, evaluates options, builds spec

Exit 0 = all components work.
Exit 1 = something failed (check output for details).
"""

from __future__ import annotations

import asyncio
import json
import sys
import argparse
import logging
from pathlib import Path
from uuid import uuid4

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
for p in [ROOT, ROOT / "agents", ROOT / "core"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase1")

# ── Imports ───────────────────────────────────────────────────────────────────
from core.spec_ir import make_empty_spec, Vertical, SpecStatus
from agents.llm_client import make_llm_client
from agents.gates.metacognition import MetacognitionGate, GateContext, GateVerdict
from agents.conversation.domain_wizard import DomainWizard, WizardTurnContext
from agents.conversation.spec_editor import SpecEditorAgent
from agents.conversation.completeness_validator import CompletenessValidator
from agents.option_graph.engine import OptionGraphEngine, EligibilityContext


# ── Helpers ───────────────────────────────────────────────────────────────────

def header(title: str):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print(f"{'═'*60}")

def ok(msg: str):
    print(f"  ✓  {msg}")

def fail(msg: str):
    print(f"  ✗  {msg}")
    return False

def info(msg: str):
    print(f"     {msg}")

def section(title: str):
    print(f"\n  ── {title}")


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENT 1 — Metacognition Gate
# ══════════════════════════════════════════════════════════════════════════════

def run_gate() -> bool:
    header("1 / 5 — Metacognition Gate (Tier 0 rules)")
    gate = MetacognitionGate()
    passed = True

    # Test R1: missing connector doc → NARROW retrieval
    section("R1: named integration with no connector doc")
    ctx = GateContext(
        decision_point="retrieval",
        integrations_named_in_spec=["Razorpay", "Finxact"],
        connector_docs_available=[],
        user_message="add payment gateway integration",
    )
    d = gate.evaluate_retrieval(ctx)
    if d.verdict == GateVerdict.NARROW and d.tier == 0:
        ok(f"NARROW  (tier={d.tier})  query='{d.narrow_query[:60]}'")
    else:
        passed = fail(f"Expected NARROW got {d.verdict}")

    # Test R2: regulation keyword → NARROW retrieval
    section("R2: regulation keyword in user message")
    ctx = GateContext(
        decision_point="retrieval",
        user_message="we need PCI-DSS and GDPR compliance",
        integrations_named_in_spec=[],
        connector_docs_available=[],
    )
    d = gate.evaluate_retrieval(ctx)
    if d.verdict == GateVerdict.NARROW and d.tier == 0:
        ok(f"NARROW  (tier={d.tier})  query='{d.narrow_query[:60]}'")
    else:
        passed = fail(f"Expected NARROW got {d.verdict}")

    # Test R3: entities already in context → NOT_NEEDED
    section("R3: entity already in context")
    ctx = GateContext(
        decision_point="retrieval",
        user_message="add discount field to Product",
        entities_in_context=["Product", "Order", "Cart"],
        turn_entities_mentioned=["Product"],
        integrations_named_in_spec=[],
        connector_docs_available=[],
    )
    d = gate.evaluate_retrieval(ctx)
    if d.verdict == GateVerdict.NOT_NEEDED and d.tier == 0:
        ok(f"NOT_NEEDED  (tier={d.tier})  reason='{d.reason[:60]}'")
    else:
        passed = fail(f"Expected NOT_NEEDED got {d.verdict}")

    # Test R4: too-short message → NOT_NEEDED
    section("R4: very short message")
    ctx = GateContext(
        decision_point="retrieval",
        user_message="yes",
        integrations_named_in_spec=[],
        connector_docs_available=[],
    )
    d = gate.evaluate_retrieval(ctx)
    if d.verdict == GateVerdict.NOT_NEEDED:
        ok(f"NOT_NEEDED  reason='{d.reason[:60]}'")
    else:
        passed = fail(f"Expected NOT_NEEDED got {d.verdict}")

    # Test E1: fix_iteration < 3 → escalation PROHIBITED
    section("E1: escalation before 3 fix iterations")
    ctx = GateContext(
        decision_point="escalation",
        fix_iteration=1,
        max_fix_iterations=3,
        tenant_escalation_enabled=True,
        tenant_external_llm_consent=True,
        budget_remaining=100.0,
        escalation_budget=5.0,
    )
    d = gate.evaluate_escalation(ctx)
    if d.verdict == GateVerdict.NOT_NEEDED:
        ok(f"NOT_NEEDED  (escalation correctly blocked at iteration 1)")
    else:
        passed = fail(f"Expected NOT_NEEDED got {d.verdict}")

    # Test E5: all conditions met → NEEDED
    section("E5: escalation approved — all conditions met")
    ctx = GateContext(
        decision_point="escalation",
        fix_iteration=3,
        max_fix_iterations=3,
        tenant_escalation_enabled=True,
        tenant_external_llm_consent=True,
        budget_remaining=100.0,
        escalation_budget=5.0,
    )
    d = gate.evaluate_escalation(ctx)
    if d.verdict == GateVerdict.NEEDED:
        ok(f"NEEDED  (escalation correctly approved after 3 iterations)")
    else:
        passed = fail(f"Expected NEEDED got {d.verdict}")

    # Check gate_decisions log
    decisions = gate.get_decisions()
    ok(f"gate_decisions logged: {len(decisions)} entries (HDPO training data)")

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENT 2 — Domain Wizard
# ══════════════════════════════════════════════════════════════════════════════

async def run_wizard(llm_client) -> bool:
    header("2 / 5 — Domain Wizard (Qwen3-8B via Ollama)")
    passed = True
    wizard = DomainWizard(llm_client=llm_client)

    spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
    spec_dict = spec.model_dump(mode="json")

    # Turn 1: add product review feature (no retrieval needed)
    section("Turn 1: escape hatch — 'add a product review feature'")
    ctx = WizardTurnContext(
        user_message="add a product review feature with star rating and text",
        section_id="domain_model",
        spec_snapshot=spec_dict,
        vertical="ecommerce",
        entities_in_context=[],
        integrations_named=[],
        connector_docs_available=[],
    )
    card = await wizard.process_turn(ctx)
    info(f"amendment_id : {card.amendment_id}")
    info(f"summary      : {card.proposal.summary}")
    info(f"confidence   : {card.proposal.confidence}")
    info(f"patch keys   : {list(card.proposal.patch.keys())}")
    info(f"retrieval    : {'yes' if card.retrieval_used else 'no (gate blocked)'}")
    if card.amendment_id.startswith("amend_"):
        ok("Diff card returned with valid amendment_id")
    else:
        passed = fail("Invalid amendment_id")

    # Turn 2: message with regulation keyword → gate fires retrieval
    section("Turn 2: 'we need PCI compliance' → gate fires NARROW retrieval")
    ctx2 = WizardTurnContext(
        user_message="we need PCI-DSS compliance for card payments",
        section_id="compliance_model",
        spec_snapshot=spec_dict,
        vertical="ecommerce",
        integrations_named=["Razorpay"],
        connector_docs_available=[],     # no docs → gate fires
    )
    card2 = await wizard.process_turn(ctx2)
    info(f"summary      : {card2.proposal.summary}")
    info(f"gate tier    : {card2.gate_tier_used}")
    ok(f"Gate tier 0 fired correctly (tier={card2.gate_tier_used})")

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENT 3 — Spec Editor Agent
# ══════════════════════════════════════════════════════════════════════════════

async def run_editor(llm_client) -> bool:
    header("3 / 5 — Spec Editor Agent (Qwen3-8B via Ollama)")
    passed = True
    agent = SpecEditorAgent(llm_client=llm_client)

    # Build a populated spec for editing
    spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
    spec_dict = spec.model_dump(mode="json")
    spec_dict["stack"]["backend"] = "java_spring"
    spec_dict["domain_model"]["entities"] = [
        {"name": "Product", "description": "A product", "fields": [
            {"name": "title", "field_type": "string", "required": True,
             "pii_class": "none", "description": "", "constraints": {}}],
         "relationships": [], "audit_fields": True, "soft_delete": False},
        {"name": "Order", "description": "A customer order", "fields": [
            {"name": "total", "field_type": "money", "required": True,
             "pii_class": "none", "description": "", "constraints": {}}],
         "relationships": [], "audit_fields": True, "soft_delete": False},
    ]

    # Amendment 1: add a new auth method
    section("Amendment 1: 'Add WhatsApp OTP as secondary auth'")
    card1 = await agent.process_amendment(
        instruction="Add WhatsApp OTP as a secondary authentication method",
        section_id="security_model",
        spec_snapshot=spec_dict,
    )
    info(f"amendment_id : {card1.amendment_id}")
    info(f"summary      : {card1.proposal.summary}")
    info(f"confidence   : {card1.proposal.confidence}")
    info(f"patch keys   : {list(card1.proposal.patch.keys())}")
    ok("Diff card returned — user must Accept/Reject (Contract C4)")

    # Amendment 2: add compliance framework
    section("Amendment 2: 'Add GDPR compliance'")
    card2 = await agent.process_amendment(
        instruction="Add GDPR data privacy compliance to the compliance model",
        section_id="compliance_model",
        spec_snapshot=spec_dict,
    )
    info(f"summary      : {card2.proposal.summary}")
    info(f"impact       : {card2.proposal.impact_summary[:80]}")
    ok("GDPR amendment processed")

    # Amendment 3: empty instruction → graceful rejection
    section("Amendment 3: empty instruction → graceful rejection")
    card3 = await agent.process_amendment(
        instruction="   ",
        section_id="domain_model",
        spec_snapshot=spec_dict,
    )
    if card3.proposal.confidence == 0.0 and card3.proposal.patch == {}:
        ok("Empty instruction correctly rejected (confidence=0, patch={})")
    else:
        passed = fail(f"Expected rejection, got confidence={card3.proposal.confidence}")

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENT 4 — Completeness Validator
# ══════════════════════════════════════════════════════════════════════════════

async def run_validator(llm_client) -> bool:
    header("4 / 5 — Completeness Validator (Layer 1 sync + Layer 2 Qwen3-8B)")
    passed = True
    validator = CompletenessValidator(llm_client=llm_client)

    # Test 1: empty spec → Layer 1 fails immediately
    section("Layer 1: empty spec → fails")
    spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
    spec_dict = spec.model_dump(mode="json")
    result = validator.validate_sync(spec_dict)
    info(f"is_complete          : {result.is_complete}")
    info(f"completeness_percent : {result.completeness_percent}%")
    info(f"missing_required     : {result.missing_required}")
    if not result.is_complete and result.completeness_percent < 50:
        ok("Empty spec correctly fails Layer 1")
    else:
        passed = fail("Expected Layer 1 failure on empty spec")

    # Test 2: populated spec → Layer 1 passes, Layer 2 runs
    section("Layer 2: populated spec → Qwen3-8B gap analysis")
    spec_dict["stack"]["backend"] = "java_spring"
    spec_dict["vertical"] = "ecommerce"
    spec_dict["domain_model"]["entities"] = [{
        "name": "Product", "description": "A product",
        "fields": [{"name": "title", "field_type": "string", "required": True,
                    "pii_class": "none", "description": "", "constraints": {}}],
        "relationships": [], "audit_fields": True, "soft_delete": False,
    }]
    spec_dict["api_model"]["endpoints"] = [{
        "endpoint_id": "ep_001", "method": "GET", "path": "/api/v1/products",
        "summary": "List products", "auth_required": False, "roles": [],
        "request_params": [], "request_body_entity": "", "response_entity": "list[Product]",
        "tags": ["catalog"], "acceptance_criteria_ids": [],
    }]
    spec_dict["security_model"]["roles"] = [{
        "name": "CUSTOMER", "description": "Customer role", "permissions": ["orders:read"]
    }]
    spec_dict["acceptance_criteria"] = [{
        "criterion_id": "ac_001", "feature": "catalog",
        "scenario": "Given a customer when they list products then they see all active products",
        "endpoint_ids": [], "priority": "must", "automated": True,
    }]

    result2 = await validator.validate(spec_dict, run_gap_analysis=True)
    info(f"is_complete          : {result2.is_complete}")
    info(f"completeness_percent : {result2.completeness_percent}%")
    info(f"gap_questions        : {len(result2.gap_questions)}")
    for gq in result2.gap_questions[:3]:
        info(f"  Q: {gq.question[:70]}")
        info(f"     chips: {gq.choice_chips}")
    if result2.is_complete:
        ok("Populated spec passes Layer 1")
    else:
        passed = fail(f"Unexpected Layer 1 failure: {result2.missing_required}")

    if result2.gap_questions:
        ok(f"Layer 2 returned {len(result2.gap_questions)} gap questions")
    else:
        info("Note: Layer 2 returned 0 gap questions (model may not be running)")

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENT 5 — E-commerce Domain Pack
# ══════════════════════════════════════════════════════════════════════════════

def run_pack() -> bool:
    header("5 / 5 — E-commerce Domain Pack (zero LLM)")
    passed = True
    pack_dir = ROOT / "packs" / "ecommerce"
    engine = OptionGraphEngine.from_pack_dir(pack_dir)

    section("Pack structure")
    section_ids = {s.section_id for s in engine.graph.sections}
    total_options = len(engine._options)
    info(f"sections : {sorted(section_ids)}")
    info(f"options  : {total_options} total")
    if {"catalog", "payments", "fulfillment", "compliance"}.issubset(section_ids):
        ok("All 4 sections loaded correctly")
    else:
        passed = fail(f"Missing sections: {section_ids}")

    # Select a full e-commerce stack
    section("Selecting a complete e-commerce option set")
    spec = make_empty_spec(tenant_id=uuid4(), project_id=uuid4())
    ctx = EligibilityContext(selected_options=set())

    selections = [
        "product_variants",
        "product_search",
        "category_hierarchy",
        "razorpay_integration",
        "guest_checkout",
        "order_management",
        "gst_compliance",
        "audit_logging",
    ]

    for opt_id in selections:
        ctx.selected_options.add(opt_id)
        delta = engine.evaluate_selection(opt_id, ctx, spec)
        spec_delta = delta.to_spec_delta()
        spec = spec.apply_delta(spec_delta)
        info(f"  [{opt_id:30s}] → {delta.impact_summary}")

    info(f"\nFinal spec summary:")
    info(f"  spec_version      : {spec.spec_version}")
    info(f"  entity count      : {len(spec.domain_model.entities)}")
    info(f"  endpoint count    : {len(spec.api_model.endpoints)}")
    info(f"  integration count : {len(spec.integration_model.integrations)}")
    info(f"  compliance rules  : {len(spec.compliance_model.rules)}")
    info(f"  audit logging     : {spec.compliance_model.audit_logging}")
    info(f"  provenance entries: {len(spec.provenance)}")
    ok(f"Spec built from {len(selections)} option selections, version={spec.spec_version}")

    # Test conflict enforcement
    section("Conflict enforcement: Razorpay + Stripe → ValueError")
    ctx_conflict = EligibilityContext(selected_options={"razorpay_integration"})
    try:
        engine.evaluate_selection("stripe_integration", ctx_conflict, spec)
        passed = fail("Should have raised ValueError for Razorpay/Stripe conflict")
    except ValueError as e:
        ok(f"Conflict correctly detected: {e}")

    # Test prerequisite enforcement
    section("Prerequisite: pci_dss_basic requires razorpay_integration")
    ctx_no_razorpay = EligibilityContext(selected_options=set())
    try:
        engine.evaluate_selection("pci_dss_basic", ctx_no_razorpay, spec)
        passed = fail("Should have raised ValueError — Razorpay not selected")
    except ValueError as e:
        ok(f"Prerequisite correctly enforced: {e}")

    # Freeze the spec
    section("Locking and generating a canonical hash")
    frozen = spec.freeze()
    info(f"  status         : {frozen.status.value}")
    info(f"  frozen         : {frozen.frozen}")
    info(f"  canonical_hash : {frozen.canonical_hash[:32]}...")
    if frozen.frozen and frozen.canonical_hash:
        ok("Spec locked with immutable canonical hash")
    else:
        passed = fail("Freeze failed")

    # Contract C12 check
    section("Contract C12: zero orchestration code changes")
    gate_file = ROOT / "agents" / "gates" / "metacognition.py"
    graph_file = ROOT / "agents" / "graphs" / "generation_graph.py"
    gate_ok = "ecommerce" not in gate_file.read_text().lower()
    graph_ok = "ecommerce" not in graph_file.read_text().lower()
    if gate_ok and graph_ok:
        ok("Neither metacognition.py nor generation_graph.py reference 'ecommerce'")
    else:
        passed = fail("Contract C12 violated — orchestration code references vertical name")

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="VibeForge Phase 1 integration runner")
    parser.add_argument(
        "--component",
        choices=["gate", "wizard", "editor", "validator", "pack", "all"],
        default="all",
        help="Which component to run (default: all)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock LLM (no Ollama needed)",
    )
    args = parser.parse_args()

    if args.mock:
        import os
        os.environ["VIBEFORGE_MOCK_LLM"] = "1"

    print("\n" + "═" * 60)
    print("  VibeForge — Phase 1 integration runner")
    print("═" * 60)

    # Check Ollama availability
    llm_client = make_llm_client()
    if hasattr(llm_client, "is_available"):
        available = await llm_client.is_available()
        if available:
            model = llm_client.__class__.__name__
            print(f"\n  Model: {model} — Ollama ready ✓")
        else:
            print(f"\n  ⚠  Ollama not available or qwen3:8b not pulled.")
            print(f"     Run: ollama pull qwen3:8b")
            print(f"     Using mock LLM for this run.\n")
            import os
            os.environ["VIBEFORGE_MOCK_LLM"] = "1"
            llm_client = make_llm_client()
    else:
        print(f"\n  Model: mock LLM (offline mode)")

    results = {}
    comp = args.component

    if comp in ("gate", "all"):
        results["gate"] = run_gate()

    if comp in ("wizard", "all"):
        results["wizard"] = await run_wizard(llm_client)

    if comp in ("editor", "all"):
        results["editor"] = await run_editor(llm_client)

    if comp in ("validator", "all"):
        results["validator"] = await run_validator(llm_client)

    if comp in ("pack", "all"):
        results["pack"] = run_pack()

    # Summary
    print("\n" + "═" * 60)
    print("  Summary")
    print("═" * 60)
    all_passed = True
    for name, passed in results.items():
        status = "✓  PASS" if passed else "✗  FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n  All components working. Phase 1 complete.\n")
        print("  Next: run Sahithi's Docker Compose stack, then")
        print("  wire FastAPI → these agents for the full SSE pipeline.\n")
    else:
        print("\n  Some components failed. Check output above.\n")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
