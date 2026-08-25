"""
Phase 3 — Custom Domain Pack Tests

Validates that a user-supplied custom vertical pack can be loaded
and converted into a complete ApplicationSpec without hard-coded
vertical-specific logic.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from agents.conversation.completeness_validator import (
    CompletenessValidator,
)
from agents.option_graph.engine import (
    EligibilityContext,
    OptionGraphEngine,
)
from core.spec_ir import make_empty_spec


ROOT = Path(__file__).resolve().parent.parent

CUSTOM_PACK = ROOT / "packs" / "custom"


def build_custom_spec():
    """Create an empty ApplicationSpec for the custom vertical."""
    return make_empty_spec(
        tenant_id=uuid4(),
        project_id=uuid4(),
        vertical="custom",
    )


def test_custom_pack_exists():
    """The custom domain pack must exist."""
    assert CUSTOM_PACK.exists(), (
        f"Missing custom pack: {CUSTOM_PACK}"
    )


def test_custom_pack_loads():
    """The custom pack must contain a valid option graph."""

    engine = OptionGraphEngine.from_pack_dir(
        CUSTOM_PACK
    )

    assert len(engine.graph.sections) >= 1
    assert len(engine._options) >= 1


def test_custom_pack_has_expected_files():
    """The custom pack must contain its Phase-3 artifacts."""

    assert (
        CUSTOM_PACK / "pack.yaml"
    ).exists()

    assert (
        CUSTOM_PACK
        / "option_graphs"
        / "custom.yaml"
    ).exists()

    assert (
        CUSTOM_PACK
        / "schema_fragments"
        / "events.yaml"
    ).exists()

    assert (
        CUSTOM_PACK
        / "compliance_rules"
        / "custom.yaml"
    ).exists()

    assert (
        CUSTOM_PACK
        / "acceptance_seeds"
        / "custom.feature"
    ).exists()


def test_custom_options_are_loaded():
    """The engine must load options from the custom YAML."""

    engine = OptionGraphEngine.from_pack_dir(
        CUSTOM_PACK
    )

    assert engine._options

    for option_id, option in engine._options.items():
        assert option_id
        assert option.label


def test_custom_option_can_produce_delta():
    """
    At least one custom option must be selectable and produce
    a deterministic OptionDelta.
    """

    engine = OptionGraphEngine.from_pack_dir(
        CUSTOM_PACK
    )

    spec = build_custom_spec()

    selected = set()

    for option_id in engine._options:

        context = EligibilityContext(
            selected_options=selected,
            vertical="custom",
        )

        try:
            delta = engine.evaluate_selection(
                option_id,
                context,
                spec,
            )
        except (ValueError, TypeError):
            continue

        assert delta.option_id == option_id
        assert isinstance(delta.patch, dict)

        return

    pytest.fail(
        "No custom-pack option could be selected "
        "from an empty custom context."
    )


def test_custom_pack_can_reach_complete_spec():
    """
    Deterministically search the custom option graph until a
    complete ApplicationSpec is produced.
    """

    engine = OptionGraphEngine.from_pack_dir(
        CUSTOM_PACK
    )

    validator = CompletenessValidator()
    spec = build_custom_spec()

    option_ids = list(engine._options.keys())

    max_states = 5000
    states_explored = 0

    def search(
        current_spec,
        selected,
    ):
        nonlocal states_explored

        states_explored += 1

        if states_explored > max_states:
            return None

        validation = validator.validate_sync(
            current_spec.model_dump(mode="json")
        )

        if validation.is_complete:
            return current_spec, selected

        for option_id in option_ids:

            if option_id in selected:
                continue

            context = EligibilityContext(
                selected_options=set(selected),
                vertical="custom",
            )

            try:
                delta = engine.evaluate_selection(
                    option_id,
                    context,
                    current_spec,
                )

                candidate_spec = current_spec.apply_delta(
                    delta.to_spec_delta()
                )

            except (
                ValueError,
                TypeError,
            ):
                continue

            candidate_selected = set(selected)
            candidate_selected.add(option_id)

            result = search(
                candidate_spec,
                candidate_selected,
            )

            if result is not None:
                return result

        return None

    result = search(
        spec,
        set(),
    )

    assert result is not None, (
        "Custom pack could not produce a complete "
        "ApplicationSpec.\n"
        f"Available options: {option_ids}\n"
        f"States explored: {states_explored}"
    )

    complete_spec, selected = result

    validation = validator.validate_sync(
        complete_spec.model_dump(mode="json")
    )

    assert validation.is_complete
    assert validation.completeness_percent == 100.0
    assert selected


def test_custom_pack_creates_real_spec_content():
    """
    Completion must come from real Spec IR content rather than
    merely changing metadata.
    """

    engine = OptionGraphEngine.from_pack_dir(
        CUSTOM_PACK
    )

    validator = CompletenessValidator()
    spec = build_custom_spec()

    option_ids = list(engine._options.keys())

    max_states = 5000
    states_explored = 0

    def search(current_spec, selected):

        nonlocal states_explored

        states_explored += 1

        if states_explored > max_states:
            return None

        result = validator.validate_sync(
            current_spec.model_dump(mode="json")
        )

        if result.is_complete:
            return current_spec, selected

        for option_id in option_ids:

            if option_id in selected:
                continue

            context = EligibilityContext(
                selected_options=set(selected),
                vertical="custom",
            )

            try:
                delta = engine.evaluate_selection(
                    option_id,
                    context,
                    current_spec,
                )

                next_spec = current_spec.apply_delta(
                    delta.to_spec_delta()
                )

            except (
                ValueError,
                TypeError,
            ):
                continue

            next_selected = set(selected)
            next_selected.add(option_id)

            found = search(
                next_spec,
                next_selected,
            )

            if found is not None:
                return found

        return None

    result = search(
        spec,
        set(),
    )

    assert result is not None

    complete_spec, selected = result

    assert selected

    assert len(
        complete_spec.domain_model.entities
    ) >= 1

    assert len(
        complete_spec.api_model.endpoints
    ) >= 1

    assert len(
        complete_spec.security_model.roles
    ) >= 1

    assert len(
        complete_spec.acceptance_criteria
    ) >= 1


def test_custom_pack_does_not_mutate_original_spec():
    """
    Applying custom options must preserve immutability of the
    original ApplicationSpec.
    """

    engine = OptionGraphEngine.from_pack_dir(
        CUSTOM_PACK
    )

    spec = build_custom_spec()

    original_version = spec.spec_version
    original_hash = spec.canonical_hash

    for option_id in engine._options:

        context = EligibilityContext(
            selected_options=set(),
            vertical="custom",
        )

        try:
            delta = engine.evaluate_selection(
                option_id,
                context,
                spec,
            )
        except (ValueError, TypeError):
            continue

        new_spec = spec.apply_delta(
            delta.to_spec_delta()
        )

        assert new_spec is not spec

        assert spec.spec_version == original_version
        assert spec.canonical_hash == original_hash

        return

    pytest.fail(
        "No selectable custom option was available."
    )