Feature: Logistics application

  Scenario: Create shipment
    Given a valid shipment request
    When the shipment is created
    Then the shipment should have a unique identifier
    And the shipment status should be CREATED

  Scenario: Assign carrier
    Given an existing shipment
    When a carrier is assigned
    Then the shipment should have the carrier assigned

  Scenario: Track shipment
    Given a shipment in transit
    When the shipment location is updated
    Then the shipment tracking information should be updated

  Scenario: Proof of delivery
    Given a shipment is delivered
    When proof of delivery is submitted
    Then the shipment status should become DELIVERED