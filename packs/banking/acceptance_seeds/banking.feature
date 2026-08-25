Feature: Banking application

  Scenario: Create a bank account
    Given a verified customer
    When the customer creates a savings account
    Then an account should be created
    And the account should be associated with the customer

  Scenario: UPI payment
    Given an active customer account
    When the customer initiates a UPI payment
    Then the transaction should be recorded
    And the payment status should be returned

  Scenario: KYC verification
    Given a new customer
    When the customer completes the required KYC level
    Then the customer should be eligible for the configured banking services