from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4


@dataclass
class DPOPreferencePair:
    prompt: str
    chosen: str
    rejected: str
    vertical: str
    error_class: str
    cost_saved_usd: float = 0.50


class HDPODatasetGenerator:
    """
    Generates training pairs for Hierarchical Direct Preference Optimization (HDPO).
    Pairs contrast optimal local routing decisions against wasteful commercial escalations.
    """

    SAMPLE_ERRORS = [
        {
            "error": "NullPointerException: Cannot invoke 'Order.getItems()' because 'order' is null",
            "file": "src/OrderService.java",
            "error_class": "NULL_DEREF",
            "vertical": "ecommerce",
            "optimal_action": "ROUTE_LOCAL: Add null check guard if (order == null) return;",
            "suboptimal_action": "ROUTE_ESCALATE: Commercial model call for simple null check",
        },
        {
            "error": "SecurityViolation: SQL injection in AccountRepository.findByAccountNumber",
            "file": "src/AccountRepository.java",
            "error_class": "SQL_INJECTION",
            "vertical": "banking",
            "optimal_action": "ROUTE_LOCAL: Use parameterized JPA query @Query(SELECT a FROM Account a WHERE a.number = :num)",
            "suboptimal_action": "ROUTE_ESCALATE: Commercial model call for standard JPA query binding",
        },
        {
            "error": "CompileError: Cannot find symbol 'WaybillStatus.DELIVERED'",
            "file": "src/ShipmentService.java",
            "error_class": "MISSING_SYMBOL",
            "vertical": "logistics",
            "optimal_action": "ROUTE_LOCAL: Import enum and add DELIVERED status to enum definition",
            "suboptimal_action": "ROUTE_ESCALATE: Full pipeline rewrite via external commercial model",
        },
        {
            "error": "ComplianceViolation: RBI Mandate requires 24h cooldown for new beneficiaries",
            "file": "src/BeneficiaryService.java",
            "error_class": "COMPLIANCE_RBI",
            "vertical": "banking",
            "optimal_action": "ROUTE_LOCAL: Add cooldown timestamp check if (createdAt.plusHours(24).isAfter(now))",
            "suboptimal_action": "ROUTE_ESCALATE: Unnecessary commercial escalation for boolean logic",
        },
    ]

    def generate_pairs(self, count_per_sample: int = 5) -> list[DPOPreferencePair]:
        pairs = []
        for sample in self.SAMPLE_ERRORS:
            prompt = (
                f"Analyze compilation/lint error in vertical '{sample['vertical']}':\n"
                f"File: {sample['file']}\n"
                f"Error: {sample['error']}\n"
                f"Determine optimal routing: ROUTE_LOCAL or ROUTE_ESCALATE with action plan."
            )
            for _ in range(count_per_sample):
                pairs.append(
                    DPOPreferencePair(
                        prompt=prompt,
                        chosen=sample["optimal_action"],
                        rejected=sample["suboptimal_action"],
                        vertical=sample["vertical"],
                        error_class=sample["error_class"],
                        cost_saved_usd=0.50,
                    )
                )
        return pairs

    def export_jsonl(self, filepath: str, pairs: list[DPOPreferencePair]) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            for p in pairs:
                row = {
                    "prompt": p.prompt,
                    "chosen": p.chosen,
                    "rejected": p.rejected,
                    "metadata": {
                        "vertical": p.vertical,
                        "error_class": p.error_class,
                        "cost_saved_usd": p.cost_saved_usd,
                    },
                }
                f.write(json.dumps(row) + "\n")
