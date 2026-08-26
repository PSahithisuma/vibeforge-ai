from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StepResult:
    keyword: str  # Given, When, Then, And
    text: str
    passed: bool
    reason: str = ""


@dataclass
class ScenarioResult:
    scenario_name: str
    steps: list[StepResult] = field(default_factory=list)
    passed: bool = True
    failure_reason: str = ""


@dataclass
class AcceptanceReport:
    feature_name: str
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    compliance_score: float
    scenarios: list[ScenarioResult] = field(default_factory=list)


class GherkinAcceptanceRunner:
    def parse_feature(self, feature_text: str) -> tuple[str, list[dict[str, Any]]]:
        feature_name = "Feature"
        scenarios = []
        current_scenario: Optional[dict[str, Any]] = None

        for line in feature_text.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue

            if line_str.lower().startswith("feature:"):
                feature_name = line_str.split(":", 1)[1].strip()
            elif line_str.lower().startswith("scenario:"):
                if current_scenario:
                    scenarios.append(current_scenario)
                current_scenario = {
                    "name": line_str.split(":", 1)[1].strip(),
                    "steps": [],
                }
            elif current_scenario and any(
                line_str.startswith(kw) for kw in ["Given ", "When ", "Then ", "And ", "But "]
            ):
                parts = line_str.split(" ", 1)
                current_scenario["steps"].append({
                    "keyword": parts[0],
                    "text": parts[1] if len(parts) > 1 else "",
                })

        if current_scenario:
            scenarios.append(current_scenario)

        return feature_name, scenarios

    def evaluate(
        self,
        feature_text: str,
        assembled_files: dict[str, str],
        vertical: str = "custom",
    ) -> AcceptanceReport:
        feature_name, parsed_scenarios = self.parse_feature(feature_text)
        all_code = "\n".join(assembled_files.values())
        scenario_results: list[ScenarioResult] = []

        for sc in parsed_scenarios:
            s_name = sc["name"]
            step_results: list[StepResult] = []
            scenario_passed = True
            fail_reason = ""

            for st in sc["steps"]:
                kw = st["keyword"]
                txt = st["text"].lower()

                passed = True
                reason = ""

                if "transaction" in txt or "atomic" in txt:
                    if "@transactional" not in all_code.lower() and "session.begin" not in all_code.lower():
                        passed = False
                        reason = "Missing transaction demarcation (@Transactional)"

                elif "kyc" in txt or "aadhaar" in txt or "pan" in txt:
                    if not any(k in all_code.lower() for k in ["kyc", "aadhaar", "pan", "identity"]):
                        passed = False
                        reason = "Missing KYC verification handling"

                elif "audit" in txt or "logged" in txt:
                    if not any(k in all_code.lower() for k in ["audit", "logger", "logging", "event"]):
                        passed = False
                        reason = "Missing audit logging trail"

                elif "validation" in txt or "positive balance" in txt or "amount > 0" in txt:
                    if not any(k in all_code.lower() for k in ["@min", "@positive", "@valid", "if (amount", "amount <="]):
                        passed = False
                        reason = "Missing amount or input validation constraint"

                elif "waybill" in txt or "tracking" in txt:
                    if not any(k in all_code.lower() for k in ["waybill", "tracking", "shipment"]):
                        passed = False
                        reason = "Missing logistics waybill/tracking entity"

                elif "cart" in txt or "checkout" in txt:
                    if not any(k in all_code.lower() for k in ["cart", "checkout", "order"]):
                        passed = False
                        reason = "Missing cart/checkout entity or service"

                step_results.append(StepResult(keyword=kw, text=st["text"], passed=passed, reason=reason))
                if not passed:
                    scenario_passed = False
                    if not fail_reason:
                        fail_reason = reason

            scenario_results.append(
                ScenarioResult(
                    scenario_name=s_name,
                    steps=step_results,
                    passed=scenario_passed,
                    failure_reason=fail_reason,
                )
            )

        passed_count = sum(1 for s in scenario_results if s.passed)
        total_count = len(scenario_results)
        score = round(passed_count / max(1, total_count), 4)

        return AcceptanceReport(
            feature_name=feature_name,
            total_scenarios=total_count,
            passed_scenarios=passed_count,
            failed_scenarios=total_count - passed_count,
            compliance_score=score,
            scenarios=scenario_results,
        )
