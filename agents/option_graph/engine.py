"""
VibeForge — Option-Graph Engine
================================

YAML-driven deterministic spec patch evaluator.

Zero LLM.

Every checkbox selection produces an OptionDelta,
which can then be converted into a SpecDelta and applied
immutably to ApplicationSpec.

Public API:

    engine = OptionGraphEngine.from_pack_dir(pack_dir)

    context = EligibilityContext(
        selected_options={"bank_accounts"}
    )

    delta = engine.evaluate_selection(
        "savings_account",
        context,
        spec,
    )

    status = engine.get_section_status(
        "accounts",
        context,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# YAML SCHEMA
# ============================================================================


class EligibilityRule(BaseModel):
    rule_type: str = "none"
    option_id: Optional[str] = None
    vertical: Optional[str] = None


class SpecBinding(BaseModel):
    json_path: str
    operation: str = "append_if_missing"
    value: Any = None


class OptionDefinition(BaseModel):
    option_id: str
    label: str
    description: str = ""
    eligibility_rules: list[EligibilityRule] = Field(
        default_factory=list
    )
    spec_bindings: list[SpecBinding] = Field(
        default_factory=list
    )


class SectionYAML(BaseModel):
    section_id: str
    title: str = ""
    label: str = ""
    prerequisites: list[str] = Field(
        default_factory=list
    )
    options: list[OptionDefinition] = Field(
        default_factory=list
    )

    @property
    def display_title(self) -> str:
        return self.title or self.label


class OptionGraph(BaseModel):
    sections: list[SectionYAML] = Field(
        default_factory=list
    )


# ============================================================================
# ELIGIBILITY CONTEXT
# ============================================================================


@dataclass
class EligibilityContext:
    selected_options: set[str] = field(
        default_factory=set
    )
    vertical: Optional[str] = None
    business_models: list[str] = field(
        default_factory=list
    )


# ============================================================================
# OPTION DELTA
# ============================================================================


@dataclass
class OptionDelta:
    option_id: str
    patch: dict[str, Any]
    provenance_entries: list[Any]

    new_entity_count: int = 0
    new_endpoint_count: int = 0

    amendment_id: str = ""
    impact_summary: str = ""

    def to_spec_delta(self):
        """
        Convert this OptionDelta into the SpecDelta expected
        by ApplicationSpec.apply_delta().
        """

        from core.spec_ir import SpecDelta, ProvenanceEntry

        entries = []

        for provenance in self.provenance_entries:
            if isinstance(provenance, dict):
                entries.append(
                    ProvenanceEntry(
                        json_path=provenance.get(
                            "json_path",
                            "",
                        ),
                        source_type="option_selection",
                        source_id=self.option_id,
                        value_snapshot=str(
                            provenance.get(
                                "value",
                                "",
                            )
                        )[:200],
                    )
                )
            else:
                entries.append(provenance)

        return SpecDelta(
            amendment_id=(
                self.amendment_id
                or f"opt_{self.option_id}"
            ),
            patch=self.patch,
            provenance_entries=entries,
            impact_summary=self.impact_summary,
            new_entity_count=self.new_entity_count,
            new_endpoint_count=self.new_endpoint_count,
        )


# ============================================================================
# NESTED DICT HELPERS
# ============================================================================


def _get_nested(
    data: dict,
    dotpath: str,
) -> Any:
    """
    Navigate:

        domain_model.entities

    into:

        data["domain_model"]["entities"]
    """

    current = data

    for part in dotpath.split("."):
        if not isinstance(current, dict):
            return None

        current = current.get(part)

    return current


def _set_nested(
    data: dict,
    dotpath: str,
    value: Any,
) -> None:
    """
    Set a nested value, creating intermediate dictionaries.
    """

    parts = dotpath.split(".")
    current = data

    for part in parts[:-1]:
        if part not in current:
            current[part] = {}

        elif not isinstance(current[part], dict):
            current[part] = {}

        current = current[part]

    current[parts[-1]] = value


def _identity_key(value: Any) -> Optional[str]:
    """
    Determine how an object should be uniquely identified.

    Different Spec IR collections use different identity fields.
    """

    if not isinstance(value, dict):
        return None

    for key in (
        "name",
        "endpoint_id",
        "operation_id",
        "path",
        "criterion_id",
        "screen_id",
        "integration_id",
        "provider",
        "id",
    ):
        if key in value:
            return key

    return None


# ============================================================================
# APPLY ONE BINDING
# ============================================================================


def _apply_binding(
    patch: dict,
    binding: SpecBinding,
) -> tuple[int, int]:
    """
    Apply one binding to the patch.

    Supported operations:

        set
        append
        append_if_missing
        remove

    Returns:

        (new_entity_count, new_endpoint_count)
    """

    new_entities = 0
    new_endpoints = 0

    operation = binding.operation
    path = binding.json_path
    value = binding.value

    # ------------------------------------------------------------------
    # SET
    # ------------------------------------------------------------------

    if operation == "set":
        _set_nested(
            patch,
            path,
            value,
        )

        return (
            new_entities,
            new_endpoints,
        )

    # ------------------------------------------------------------------
    # APPEND / APPEND_IF_MISSING
    # ------------------------------------------------------------------

    if operation in {
        "append",
        "append_if_missing",
    }:
        existing = _get_nested(
            patch,
            path,
        )

        if existing is None:
            existing = []

            _set_nested(
                patch,
                path,
                existing,
            )

        if not isinstance(existing, list):
            raise ValueError(
                f"Cannot append to non-list path "
                f"'{path}'"
            )

        should_append = True

        if operation == "append_if_missing":
            identity_key = _identity_key(value)

            if identity_key is not None:
                should_append = not any(
                    isinstance(item, dict)
                    and item.get(identity_key)
                    == value.get(identity_key)
                    for item in existing
                )
            else:
                should_append = value not in existing

        if should_append:
            existing.append(value)

            if path == "domain_model.entities":
                new_entities += 1

            elif path == "api_model.endpoints":
                new_endpoints += 1

        return (
            new_entities,
            new_endpoints,
        )

    # ------------------------------------------------------------------
    # REMOVE
    # ------------------------------------------------------------------

    if operation == "remove":
        existing = _get_nested(
            patch,
            path,
        )

        if isinstance(existing, list):
            if value in existing:
                existing.remove(value)

            else:
                identity_key = _identity_key(value)

                if identity_key is not None:
                    existing[:] = [
                        item
                        for item in existing
                        if not (
                            isinstance(item, dict)
                            and item.get(identity_key)
                            == value.get(identity_key)
                        )
                    ]

        return (
            new_entities,
            new_endpoints,
        )

    raise ValueError(
        f"Unsupported binding operation: "
        f"{operation}"
    )


# ============================================================================
# ENGINE
# ============================================================================


class OptionGraphEngine:
    """
    Deterministic YAML-driven option graph engine.
    """

    def __init__(
        self,
        graph: OptionGraph,
    ):
        self.graph = graph

        # option_id -> OptionDefinition
        self._options = {
            option.option_id: option
            for section in graph.sections
            for option in section.options
        }

        # section_id -> SectionYAML
        self._sections = {
            section.section_id: section
            for section in graph.sections
        }

    # ========================================================================
    # LOAD PACK
    # ========================================================================

    @classmethod
    def from_pack_dir(
        cls,
        pack_dir: str | Path,
    ) -> "OptionGraphEngine":
        """
        Load YAML option graphs from:

            <pack_dir>/option_graphs/

        Supports:

        1. Multi-section YAML

            sections:
              - section_id: ...

        2. Single-section YAML

            section_id: ...
        """

        pack_path = Path(pack_dir)

        option_graph_dir = (
            pack_path / "option_graphs"
        )

        if not option_graph_dir.exists():
            option_graph_dir = pack_path

        yaml_files = (
            sorted(
                option_graph_dir.glob("*.yaml")
            )
            + sorted(
                option_graph_dir.glob("*.yml")
            )
        )

        if not yaml_files:
            raise FileNotFoundError(
                f"No YAML files found in "
                f"{option_graph_dir}"
            )

        all_sections: list[SectionYAML] = []

        for yaml_file in yaml_files:
            logger.info(
                "Loading option graph: %s",
                yaml_file,
            )

            with open(
                yaml_file,
                "r",
                encoding="utf-8-sig",
            ) as file:
                data = yaml.safe_load(file)

            if not isinstance(data, dict):
                raise ValueError(
                    f"Invalid YAML in "
                    f"{yaml_file}: expected mapping"
                )

            # Multi-section format
            if "sections" in data:
                sections = data["sections"]

                if not isinstance(
                    sections,
                    list,
                ):
                    raise ValueError(
                        f"'sections' must be a list "
                        f"in {yaml_file}"
                    )

                for section in sections:
                    if not isinstance(
                        section,
                        dict,
                    ):
                        raise ValueError(
                            f"Invalid section in "
                            f"{yaml_file}"
                        )

                    all_sections.append(
                        SectionYAML(
                            **section
                        )
                    )

            # Single-section format
            elif "section_id" in data:
                all_sections.append(
                    SectionYAML(
                        **data
                    )
                )

            else:
                raise ValueError(
                    f"Invalid option graph "
                    f"{yaml_file}: expected "
                    f"'sections' or 'section_id'"
                )

        if not all_sections:
            raise ValueError(
                f"No sections loaded from "
                f"{option_graph_dir}"
            )

        graph = OptionGraph(
            sections=all_sections
        )

        logger.info(
            "Loaded option graph: "
            "%d sections, %d options",
            len(graph.sections),
            sum(
                len(section.options)
                for section in graph.sections
            ),
        )

        return cls(graph)

    # ========================================================================
    # ELIGIBILITY
    # ========================================================================

    def _check_rule(
        self,
        rule: EligibilityRule,
        context: EligibilityContext,
    ) -> bool:
        """
        Return True when an eligibility rule is satisfied.
        """

        rule_type = rule.rule_type

        # No restriction
        if rule_type == "none":
            return True

        # Requires another option
        if rule_type == "requires_option":
            if not rule.option_id:
                return False

            return (
                rule.option_id
                in context.selected_options
            )

        # Conflicts with another option
        if rule_type == "conflicts_with":
            if not rule.option_id:
                return True

            return (
                rule.option_id
                not in context.selected_options
            )

        # Requires a vertical
        if rule_type == "requires_vertical":
            if not rule.vertical:
                return False

            return (
                context.vertical
                == rule.vertical
            )

        logger.warning(
            "Unknown eligibility rule: %s",
            rule_type,
        )

        return False

    def _is_eligible(
        self,
        option: OptionDefinition,
        context: EligibilityContext,
    ) -> bool:
        """
        An option is eligible only when every rule passes.
        """

        return all(
            self._check_rule(
                rule,
                context,
            )
            for rule in option.eligibility_rules
        )

    def _eligibility_error(
        self,
        option: OptionDefinition,
        context: EligibilityContext,
    ) -> str:
        """
        Return a deterministic, human-readable explanation
        for why an option is not eligible.

        The message always contains "not eligible" for
        backward compatibility while also exposing the
        specific eligibility reason.
        """
        for rule in option.eligibility_rules:

            if rule.rule_type == "requires_option":
                if (
                    rule.option_id
                    and rule.option_id
                    not in context.selected_options
                ):
                    return (
                        f"Option '{option.option_id}' "
                        f"is not eligible: "
                        f"Requires '{rule.option_id}' "
                        f"to be selected first"
                    )

            elif rule.rule_type == "conflicts_with":
                if (
                    rule.option_id
                    and rule.option_id
                    in context.selected_options
                ):
                    return (
                        f"Option '{option.option_id}' "
                        f"is not eligible: "
                        f"Conflicts with "
                        f"'{rule.option_id}'"
                    )

            elif rule.rule_type == "requires_vertical":
                if context.vertical != rule.vertical:
                    return (
                        f"Option '{option.option_id}' "
                        f"is not eligible: "
                        f"Requires vertical "
                        f"'{rule.vertical}'"
                    )

        return (
            f"Option '{option.option_id}' "
            f"is not eligible"
        )

    # ========================================================================
    # EVALUATE SELECTION
    # ========================================================================

    def evaluate_selection(
        self,
        option_id: str,
        context: EligibilityContext,
        spec: Any,
    ) -> OptionDelta:
        """Evaluate a checkbox selection without mutating ApplicationSpec."""

        if option_id not in self._options:
            raise ValueError(
                f"Unknown option_id: '{option_id}'"
            )

        option = self._options[option_id]

        if not self._is_eligible(option, context):
            raise ValueError(
                self._eligibility_error(
                    option,
                    context,
                )
            )

        patch: dict[str, Any] = {}
        provenance_entries = []
        new_entity_count = 0
        new_endpoint_count = 0

        for binding in option.spec_bindings:
            entities, endpoints = _apply_binding(
                patch,
                binding,
            )
            new_entity_count += entities
            new_endpoint_count += endpoints

            provenance_entries.append(
                {
                    "json_path": binding.json_path,
                    "operation": binding.operation,
                    "value": binding.value,
                }
            )

        impact_parts = []

        if new_entity_count:
            impact_parts.append(
                f"+{new_entity_count} "
                f"entity"
                f"{'ies' if new_entity_count != 1 else ''}"
            )

        if new_endpoint_count:
            impact_parts.append(
                f"+{new_endpoint_count} "
                f"endpoint"
                f"{'s' if new_endpoint_count != 1 else ''}"
            )

        impact_summary = (
            f"Added: {option.label} "
            f"({', '.join(impact_parts)})"
            if impact_parts
            else f"Added: {option.label}"
        )

        return OptionDelta(
            option_id=option_id,
            patch=patch,
            provenance_entries=provenance_entries,
            new_entity_count=new_entity_count,
            new_endpoint_count=new_endpoint_count,
            amendment_id=f"opt_{option_id}",
            impact_summary=impact_summary,
        )
    # ========================================================================
    # SECTION STATUS
    # ========================================================================

    def get_section_status(
        self,
        section_id: str,
        context: EligibilityContext,
    ) -> dict[str, Any]:
        """
        Return UI-friendly status information for one section.

        `options` is a dictionary keyed by option_id.

        Example:

            {
                "section_id": "test_section",
                "title": "Test Section",
                "options": {
                    "add_reviews": {
                        "option_id": "add_reviews",
                        "label": "Add Reviews",
                        "description": "...",
                        "selected": False,
                        "eligible": True
                    }
                }
            }

        This structure supports direct lookup:

            status["options"]["add_reviews"]["eligible"]
        """

        if section_id not in self._sections:
            raise ValueError(
                f"Unknown section_id: '{section_id}'"
            )

        section = self._sections[section_id]

        options: dict[str, dict[str, Any]] = {}

        for option in section.options:

            selected = (
                option.option_id
                in context.selected_options
            )

            eligible = self._is_eligible(
                option,
                context,
            )

            options[option.option_id] = {
                "option_id": option.option_id,
                "label": option.label,
                "description": option.description,
                "selected": selected,
                "eligible": eligible,
            }

        selected_count = sum(
            1
            for option_id in options
            if option_id in context.selected_options
        )

        return {
            "section_id": section.section_id,
            "title": section.display_title,
            "label": section.label,
            "options": options,
            "selected_count": selected_count,
            "total_count": len(options),
        }
    # ========================================================================
    # LIVE IMPACT
    # ========================================================================

    def get_live_impact(
        self,
        context: EligibilityContext,
        spec: Any,
    ) -> dict[str, Any]:
        """Calculate selected-option impact without mutating the supplied spec."""

        entity_count = len(
            getattr(
                spec.domain_model,
                "entities",
                [],
            )
        )
        endpoint_count = len(
            getattr(
                spec.api_model,
                "endpoints",
                [],
            )
        )

        selected_entity_delta = 0
        selected_endpoint_delta = 0
        summaries = []

        for option_id in sorted(
            context.selected_options
        ):
            option = self._options.get(option_id)

            if option is None:
                continue

            try:
                delta = self.evaluate_selection(
                    option_id,
                    context,
                    spec,
                )
            except ValueError:
                continue

            selected_entity_delta += (
                delta.new_entity_count
            )
            selected_endpoint_delta += (
                delta.new_endpoint_count
            )
            summaries.append(delta.impact_summary)

        return {
            "entity_count": (
                entity_count
                + selected_entity_delta
            ),
            "endpoint_count": (
                endpoint_count
                + selected_endpoint_delta
            ),
            "new_entity_count": selected_entity_delta,
            "new_endpoint_count": selected_endpoint_delta,
            "selected_options": sorted(
                context.selected_options
            ),
            "option_count": len(
                context.selected_options
            ),
            "summaries": summaries,
            "impact_summary": "; ".join(summaries),
        }
