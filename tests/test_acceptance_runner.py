from __future__ import annotations

import pytest
from agents.testing.acceptance_runner import GherkinAcceptanceRunner

BANKING_FEATURE = """
Feature: Banking Core Payment and Compliance
  Scenario: Transfer funds between accounts with ACID compliance
    Given customer has an active savings account
    When transfer of amount is requested
    Then transaction is executed atomically
    And audit trail is logged

  Scenario: Prevent negative balance transfers
    Given account with balance 100
    When transfer of 500 is requested
    Then validation fails with insufficient funds

  Scenario: High value transaction requires KYC
    Given customer initiates transaction above 50000
    When transaction is processed
    Then KYC verification is enforced
"""

LOGISTICS_FEATURE = """
Feature: Freight and Waybill Tracking
  Scenario: Create and track shipment waybill
    Given a new delivery request
    When waybill is generated
    Then tracking status is updated
"""


class TestGherkinAcceptanceRunner:
    def test_parses_feature_and_scenarios(self):
        runner = GherkinAcceptanceRunner()
        fname, scenarios = runner.parse_feature(BANKING_FEATURE)
        assert fname == "Banking Core Payment and Compliance"
        assert len(scenarios) == 3
        assert scenarios[0]["name"] == "Transfer funds between accounts with ACID compliance"
        assert len(scenarios[0]["steps"]) == 4

    def test_banking_code_passes_all_scenarios(self):
        runner = GherkinAcceptanceRunner()
        mock_code = {
            "AccountService.java": """
                @Service
                public class AccountService {
                    private static final Logger logger = LoggerFactory.getLogger(AccountService.class);

                    @Transactional
                    public void transfer(Account from, Account to, @Positive double amount) {
                        if (from.getBalance() < amount) throw new InsufficientFundsException();
                        from.debit(amount);
                        to.credit(amount);
                        logger.info("Audit: Transferred " + amount);
                    }

                    public void verifyKYC(Customer customer) {
                        if (!customer.isKycVerified()) throw new KycException("KYC needed");
                    }
                }
            """
        }
        report = runner.evaluate(BANKING_FEATURE, mock_code, vertical="banking")
        assert report.total_scenarios == 3
        assert report.passed_scenarios == 3
        assert report.compliance_score == 1.0

    def test_banking_code_detects_missing_transaction(self):
        runner = GherkinAcceptanceRunner()
        bad_code = {
            "AccountService.java": """
                public class AccountService {
                    public void transfer(Account from, Account to, double amount) {
                        // missing @Transactional
                    }
                }
            """
        }
        report = runner.evaluate(BANKING_FEATURE, bad_code, vertical="banking")
        assert report.compliance_score < 1.0
        assert report.failed_scenarios > 0

    def test_logistics_code_passes_tracking_scenario(self):
        runner = GherkinAcceptanceRunner()
        mock_logistics = {
            "ShipmentService.py": """
                class ShipmentService:
                    def create_waybill(self, order_id):
                        return {"waybill_id": "WB-123", "tracking_status": "IN_TRANSIT"}
            """
        }
        report = runner.evaluate(LOGISTICS_FEATURE, mock_logistics, vertical="logistics")
        assert report.passed_scenarios == 1
        assert report.compliance_score == 1.0
