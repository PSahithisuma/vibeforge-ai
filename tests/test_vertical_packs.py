"""
Three-vertical domain-pack integration tests.

Exit criterion:
    E-commerce, Banking, and Logistics must each be able to produce
    a complete ApplicationSpec using only their real YAML option graph.

No LLM is used here.

The test exercises:

    real YAML pack
        ↓
    OptionGraphEngine
        ↓
    option selections
        ↓
    SpecDelta
        ↓
    ApplicationSpec.apply_delta()
        ↓
    CompletenessValidator
        ↓
    complete Spec IR
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from agents.conversation.completeness_validator import CompletenessValidator
from agents.option_graph.engine import (
    EligibilityContext,
    OptionGraphEngine,
)
from core.spec_ir import make_empty_spec


ROOT = Path(__file__).resolve().parent.parent


VERTICALS = (
    "ecommerce",
    "banking",
    "logistics",
    "custom",
)

def _build_spec(vertical: str):
    """Create an empty ApplicationSpec for a vertical."""
    return make_empty_spec(
        tenant_id=uuid4(),
        project_id=uuid4(),
        vertical=vertical,
    )


def _apply_option(
    engine,
    option_id,
    context,
    spec,
):
    """
    Evaluate one option and immutably apply its SpecDelta.

    Returns:
        (new_spec, option_delta)
    """
    delta = engine.evaluate_selection(
        option_id,
        context,
        spec,
    )

    new_spec = spec.apply_delta(
        delta.to_spec_delta()
    )

    return new_spec, delta


def _find_complete_selection(engine, spec, vertical):
    """
    Find a deterministic combination of options that produces
    a complete ApplicationSpec.

    Uses depth-first backtracking.

    An option does NOT have to improve completeness immediately.
    Some options provide capabilities required by later options.
    """

    validator = CompletenessValidator()

    option_ids = list(engine._options.keys())

    # Safety limit so a malformed pack cannot create an infinite search.
    max_states = 5000
    states_explored = 0

    def search(
        current_spec,
        selected,
        selected_deltas,
    ):
        nonlocal states_explored

        states_explored += 1

        if states_explored > max_states:
            return None

        validation = validator.validate_sync(
            current_spec.model_dump(mode="json")
        )

        if validation.is_complete:
            return (
                current_spec,
                selected,
                selected_deltas,
            )

        for option_id in option_ids:

            if option_id in selected:
                continue

            context = EligibilityContext(
                selected_options=set(selected),
                vertical=vertical,
            )

            try:
                candidate_spec, delta = _apply_option(
                    engine,
                    option_id,
                    context,
                    current_spec,
                )

            except (
                ValueError,
                TypeError,
            ):
                # Option is not currently eligible or generated
                # an invalid delta.
                continue

            candidate_selected = set(selected)
            candidate_selected.add(option_id)

            result = search(
                candidate_spec,
                candidate_selected,
                selected_deltas + [delta],
            )

            if result is not None:
                return result

        return None

    result = search(
        spec,
        set(),
        [],
    )

    if result is not None:
        return result

    validation = validator.validate_sync(
        spec.model_dump(mode="json")
    )

    raise AssertionError(
        f"{vertical} pack could not produce a complete spec.\n"
        f"Available options: {option_ids}\n"
        f"Initial completeness: "
        f"{validation.completeness_percent}%\n"
        f"Initial missing requirements: "
        f"{validation.missing_required}\n"
        f"States explored: {states_explored}"
    )


@pytest.mark.parametrize(
    "vertical",
    VERTICALS,
)
def test_vertical_pack_loads(vertical):
    """
    Every required vertical must have a real option graph
    containing at least one section and one option.
    """

    pack_dir = ROOT / "packs" / vertical

    assert pack_dir.exists(), (
        f"Missing domain pack: {pack_dir}"
    )

    engine = OptionGraphEngine.from_pack_dir(
        pack_dir
    )

    assert len(engine.graph.sections) >= 1

    assert len(engine._options) >= 1


@pytest.mark.parametrize(
    "vertical",
    VERTICALS,
)
def test_vertical_pack_produces_complete_spec(vertical):
    """
    Main three-vertical exit criterion.

    A business-user option-selection flow must be able to
    produce a complete ApplicationSpec without validation errors.
    """

    pack_dir = ROOT / "packs" / vertical

    engine = OptionGraphEngine.from_pack_dir(
        pack_dir
    )

    spec = _build_spec(vertical)

    complete_spec, selected, deltas = _find_complete_selection(
        engine,
        spec,
        vertical,
    )

    validator = CompletenessValidator()

    result = validator.validate_sync(
        complete_spec.model_dump(mode="json")
    )

    assert result.is_complete, (
        f"{vertical} spec is incomplete: "
        f"{result.missing_required}"
    )

    assert result.completeness_percent == 100.0

    # At least one business option must have been selected.
    assert selected, (
        f"{vertical} completed without selecting any options"
    )

    # Every selected option should have produced a delta.
    assert len(deltas) == len(selected)


@pytest.mark.parametrize(
    "vertical",
    VERTICALS,
)
def test_vertical_pack_spec_is_immutably_updated(vertical):
    """
    Verify that option selection does not mutate the original
    ApplicationSpec and creates a new spec version.
    """

    pack_dir = ROOT / "packs" / vertical

    engine = OptionGraphEngine.from_pack_dir(
        pack_dir
    )

    spec = _build_spec(vertical)

    original_version = spec.spec_version
    original_hash = spec.canonical_hash

    complete_spec, selected, deltas = _find_complete_selection(
        engine,
        spec,
        vertical,
    )

    assert complete_spec is not spec

    assert complete_spec.spec_version > original_version

    # Original spec remains untouched.
    assert spec.spec_version == original_version
    assert spec.canonical_hash == original_hash

    # Provenance must exist because every SpecDelta application
    # is expected to record the change.
    assert len(complete_spec.provenance) >= len(deltas)


@pytest.mark.parametrize(
    "vertical",
    VERTICALS,
)
def test_vertical_pack_has_real_spec_content(vertical):
    """
    Make sure completion isn't being achieved by merely setting
    metadata. The resulting Spec IR must contain actual generated
    domain/API/security/acceptance content.
    """

    pack_dir = ROOT / "packs" / vertical

    engine = OptionGraphEngine.from_pack_dir(
        pack_dir
    )

    spec = _build_spec(vertical)

    complete_spec, selected, _ = _find_complete_selection(
        engine,
        spec,
        vertical,
    )

    assert selected

    # C1
    assert len(
        complete_spec.domain_model.entities
    ) >= 1

    # C2
    assert len(
        complete_spec.api_model.endpoints
    ) >= 1

    # C5
    assert len(
        complete_spec.security_model.roles
    ) >= 1

    # C6
    assert len(
        complete_spec.acceptance_criteria
    ) >= 1


if __name__ == "__main__":
    pytest.main(
        [
            __file__,
            "-v",
            "--tb=short",
        ]
    )