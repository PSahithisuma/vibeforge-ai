Feature: Enterprise Logistics Management — ISO 27001 Compliant

  Scenario: Book shipment and generate AWB
    Given a registered shipper with sufficient credit limit
    When a forward shipment is booked with declared value 5000 INR
    Then a unique AWB number is generated
    And shipment status is set to BOOKED
    And estimated_delivery_at is calculated based on service type and route
    And shipper receives booking confirmation with AWB

  Scenario: Real-time GPS tracking update
    Given an active shipment with status IN_TRANSIT
    When driver app sends location update with latitude and longitude
    Then TrackingEvent is created with event_type ARRIVED_AT_HUB
    And vehicle current_latitude and current_longitude are updated
    And last_location_updated_at is set to current timestamp

  Scenario: Failed delivery attempt with rescheduling
    Given a shipment with status OUT_FOR_DELIVERY
    When driver marks delivery as failed with reason CUSTOMER_NOT_AVAILABLE
    Then shipment status changes to FAILED_DELIVERY
    And attempt_count increments by 1
    And next_attempt_at is scheduled for next business day
    And consignee receives SMS notification with rescheduling link

  Scenario: COD collection and reconciliation
    Given a shipment with is_cod true and cod_amount 1500 INR
    When driver marks shipment as DELIVERED and collects cash
    Then delivery_otp is verified before status update
    And CODCollection record is created with amount 1500
    And driver cash balance increases accordingly
    And remittance to shipper is scheduled

  Scenario: Shipment weight discrepancy handling
    Given a shipment booked with declared weight 2000 grams
    When hub scan reveals actual weight of 2800 grams
    Then WeightDiscrepancy record is created
    And additional freight charge is calculated
    And shipper is notified of revised charge
    And shipment proceeds with updated weight

  Scenario: Route optimisation for delivery hub
    Given a delivery hub with 50 shipments for the same pin code zone
    When morning route planning batch runs at 6 AM
    Then shipments are grouped by locality and assigned to vehicles
    And estimated_delivery_at is updated based on optimised sequence
    And driver receives sorted delivery list in mobile app
