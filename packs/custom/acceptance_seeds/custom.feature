Feature: Event Management

  Scenario: Create an event
    Given a valid event organizer
    When the organizer creates an event
    Then the event is created successfully

  Scenario: Purchase an event ticket
    Given a published event
    When a customer purchases a ticket
    Then the ticket is created successfully
    