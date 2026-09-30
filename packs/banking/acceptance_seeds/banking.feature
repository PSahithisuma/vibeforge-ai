Feature: Enterprise Banking Application — RBI Tier 1 Compliance

  # -- ACCOUNT MANAGEMENT ----------------------------------

  Scenario: Create savings account with full KYC
    Given a customer with FULL KYC status and valid PAN
    When they request a new SAVINGS account at branch IFSC HDFC0001234
    Then account is created with a unique 16-digit account number
    And opening balance is set to zero
    And dormancy trigger is set to 2 years from today per RBI norms
    And AuditLog records CREATE action with customer_id and branch_id

  Scenario: Prevent account creation without KYC
    Given a customer with KYC status PENDING
    When they attempt to open any bank account
    Then the request is rejected with error KYC_VERIFICATION_REQUIRED
    And no account record is created
    And AuditLog records the rejected attempt

  Scenario: Account dormancy after 2 years of inactivity
    Given an ACTIVE savings account with no transactions for 730 days
    When the nightly dormancy batch job runs
    Then account status changes to DORMANT
    And customer is notified via registered mobile
    And AuditLog records the status change with batch_id

  # -- PAYMENT RAILS ----------------------------------------

  Scenario: UPI transfer with successful penny drop verification
    Given an active account with available_balance of 50000 INR
    And a verified beneficiary with cooldown_until in the past
    When a UPI transfer of 10000 INR is initiated via MOBILE_APP
    Then balance_before is recorded as 50000
    And balance_after is recorded as 40000
    And transaction status moves to SUCCESS
    And RRN is captured from NPCI
    And AuditLog records the transaction

  Scenario: RTGS transfer below minimum amount rejected
    Given an active account attempting an RTGS transfer
    When the transfer amount is 150000 INR
    Then the request is rejected with error RTGS_MINIMUM_2_LAKHS
    And no transaction record is created
    And AuditLog records the validation failure

  Scenario: NEFT transfer batched correctly
    Given an active account initiating a NEFT transfer at 2:30 PM
    When the transfer of 25000 INR is submitted
    Then transaction status is set to PENDING
    And transaction is assigned to the next NEFT batch window
    And batch_id is populated when batch runs
    And customer receives SMS with transaction_ref

  # -- BENEFICIARY COOLDOWN ---------------------------------

  Scenario: Block transfer within 24h of adding new beneficiary
    Given a customer adds beneficiary account number 1234567890 at 10:00 AM
    When they attempt a transfer to that beneficiary at 10:01 AM same day
    Then transfer is rejected with error BENEFICIARY_COOLDOWN_ACTIVE
    And cooldown_until timestamp is returned in the error response
    And no transaction is created

  Scenario: Allow transfer after 24h cooldown expires
    Given a beneficiary was added 25 hours ago
    And cooldown_until is in the past
    When a transfer of 5000 INR is initiated to that beneficiary
    Then transfer proceeds normally
    And transaction status reaches SUCCESS

  # -- FRAUD DETECTION --------------------------------------

  Scenario: Flag high velocity transactions
    Given a customer account with 5 successful transactions in the last 10 minutes
    When a 6th transaction is initiated
    Then a FraudAlert is created with type VELOCITY_BREACH and severity HIGH
    And the 6th transaction is held in PENDING status
    And risk_score is calculated and stored
    And compliance team is notified

  Scenario: Block transaction to blacklisted account
    Given a destination account number exists in the fraud blacklist
    When any transfer is initiated to that account
    Then FraudAlert is created with type BLACKLIST_MATCH and severity CRITICAL
    And transaction status is set to FAILED
    And RBI STR report is automatically triggered

  # -- HIGH VALUE TRANSACTIONS ------------------------------

  Scenario: Cash transaction above 10 lakhs triggers RBI CTR
    Given a cash deposit of 1100000 INR to any account
    When the transaction is processed
    Then a RBIReport of type CASH_TRANSACTION_ABOVE_10L is auto-generated
    And report status is set to PENDING_REVIEW
    And compliance officer is notified within 15 minutes per RBI mandate

  Scenario: RTGS transfer above 50 lakhs requires dual approval
    Given a RTGS transfer request of 6000000 INR
    When submitted by a CUSTOMER via NET_BANKING
    Then transaction status is set to PENDING_APPROVAL
    And a senior officer approval request is created
    And transaction cannot be processed until approved_by is populated

  # -- KYC DOCUMENT MANAGEMENT ------------------------------

  Scenario: Periodic KYC renewal reminder
    Given a customer whose KYC documents expire within 60 days
    When the nightly KYC expiry check batch runs
    Then customer receives renewal notification
    And KYC status is set to PENDING if renewal not completed within 30 days
    And high-value transactions are blocked for PENDING KYC customers

  # -- AUDIT TRAIL ------------------------------------------

  Scenario: Every field change is captured in AuditLog
    Given a staff member updates a customer address
    When the update is committed to the database
    Then AuditLog records entity_type Customer
    And old_value contains previous address JSON
    And new_value contains updated address JSON
    And changed_fields lists address_line1 and address_line2
    And timestamp is recorded to millisecond precision

  # -- ACCOUNT CLOSURE --------------------------------------

  Scenario: Account closure with zero balance
    Given an ACTIVE account with balance of 0 and no pending transactions
    When a closure request is submitted with a valid reason
    Then account status changes to CLOSED
    And closed_at timestamp is recorded
    And closure_reason is stored
    And all associated standing instructions are cancelled
    And AuditLog records the closure with performed_by staff_id
