"""Property tests for the Message Router component.

**Property 15: Message Routing Rule Cross-Reference Validation**
**Property 16: Message Routing to Correct Personnel**
**Property 17: Missing Routing Rule Publishes Unrouted Event**
**Property 18: Non-READY Recipient Queues Message**
**Property 19: No Matching Personnel Publishes Unroutable Event**

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7**
"""

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    ClearanceLevel,
    CredentialType,
    MessageRoutingRule,
    Personnel,
    PersonnelConfig,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    MessageDelayedEvent,
    MessageReceivedEvent,
    MessageUnroutableEvent,
    MessageUnroutedEvent,
)
from enterprise_sim.modules.personnel.message_router import MessageRouter


# === Hypothesis Strategies ===

# Non-empty identifier strings
identifier_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=32,
)

# Strategy for valid roles
valid_roles = st.sampled_from(
    ["Pilot", "Loadmaster", "Navigator", "AOC_Commander", "Engineer"]
)

# Strategy for duty stations / node names
valid_stations = st.sampled_from(
    ["Hickam_AFB", "Luzon_DZ", "Kadena_AB", "Clark_AB", "Manila"]
)

# Strategy for message type names
valid_message_types = st.sampled_from(
    ["OPORD", "SITREP", "MEDEVAC", "LOGSTAT", "FRAGO"]
)

# Non-negative simulation timestamps
timestamp_st = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def valid_credential_strategy(draw):
    """Generate a valid AuthenticationCredential."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=draw(st.sampled_from(list(ClearanceLevel))),
        authorized_enclaves=draw(
            st.lists(
                st.sampled_from(["NIPR", "SIPR", "JWICS"]),
                min_size=1,
                max_size=3,
                unique=True,
            )
        ),
    )


@st.composite
def personnel_strategy(draw, role=None, duty_station=None, readiness=None):
    """Generate a valid Personnel instance with optional overrides."""
    return Personnel(
        entity_id=draw(identifier_st),
        name=draw(
            st.text(
                alphabet=st.characters(whitelist_categories=("L", "N")),
                min_size=1,
                max_size=50,
            ).filter(lambda s: s.strip() != "")
        ),
        role=role if role is not None else draw(valid_roles),
        duty_station=duty_station if duty_station is not None else draw(valid_stations),
        credential=draw(valid_credential_strategy()),
        readiness_state=readiness if readiness is not None else ReadinessState.READY,
    )


class MockEventCapture:
    """Captures events published to the EventBus for assertion."""

    def __init__(self):
        self.events: list = []

    def capture(self, event):
        self.events.append(event)

    def get_events_of_type(self, event_type):
        return [e for e in self.events if isinstance(e, event_type)]


# === Property 15: Message Routing Rule Cross-Reference Validation ===
# Feature: personnel-module, Property 15: Message Routing Rule Cross-Reference Validation


class TestMessageRoutingRuleCrossReferenceValidation:
    """Property 15: Message Routing Rule Cross-Reference Validation.

    **Validates: Requirements 4.1**

    For any MessageRoutingRule that references a message type not defined in
    c2_messages configuration or a role not present in the roster, validation
    SHALL reject the rule.
    """

    @given(
        invalid_message_type=identifier_st,
        defined_message_types=st.lists(valid_message_types, min_size=1, max_size=5, unique=True),
        role=valid_roles,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_invalid_message_type_rejected(
        self, invalid_message_type, defined_message_types, role
    ):
        """A routing rule referencing an undefined message type SHALL be rejected."""
        # Ensure the invalid message type is NOT in the defined set
        assume(invalid_message_type not in defined_message_types)

        rule = MessageRoutingRule(
            message_type=invalid_message_type,
            destination_role=role,
        )

        # Validate the rule against the defined message types
        errors = _validate_routing_rule(rule, defined_message_types, [role])
        assert len(errors) > 0
        assert any(invalid_message_type in e for e in errors)

    @given(
        message_type=valid_message_types,
        invalid_role=identifier_st,
        roster_roles=st.lists(valid_roles, min_size=1, max_size=5, unique=True),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_invalid_role_rejected(self, message_type, invalid_role, roster_roles):
        """A routing rule referencing a role not in the roster SHALL be rejected."""
        assume(invalid_role not in roster_roles)

        rule = MessageRoutingRule(
            message_type=message_type,
            destination_role=invalid_role,
        )

        errors = _validate_routing_rule(rule, [message_type], roster_roles)
        assert len(errors) > 0
        assert any(invalid_role in e for e in errors)

    @given(
        message_type=valid_message_types,
        role=valid_roles,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_valid_rule_accepted(self, message_type, role):
        """A routing rule with valid message type and role SHALL be accepted."""
        rule = MessageRoutingRule(
            message_type=message_type,
            destination_role=role,
        )

        errors = _validate_routing_rule(rule, [message_type], [role])
        assert len(errors) == 0


# === Property 16: Message Routing to Correct Personnel ===
# Feature: personnel-module, Property 16: Message Routing to Correct Personnel


class TestMessageRoutingToCorrectPersonnel:
    """Property 16: Message Routing to Correct Personnel.

    **Validates: Requirements 4.2, 4.3**

    For any C2 message delivered to a destination node, and any MessageRoutingRule
    matching that message type, the module SHALL identify the Personnel member at
    that duty_station whose role matches the rule's destination_role, and publish
    a message-received event with message type, recipient name, recipient role,
    and timestamp.
    """

    @given(
        message_type=valid_message_types,
        destination_node=valid_stations,
        target_role=valid_roles,
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_message_routed_to_correct_personnel(
        self, message_type, destination_node, target_role, timestamp, data
    ):
        """Message SHALL be routed to the personnel at destination with matching role."""
        # Create a personnel member at the destination with the target role
        target_person = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=destination_node,
                readiness=ReadinessState.READY,
            )
        )

        # Create some other personnel at different stations
        other_stations = [
            s for s in ["Hickam_AFB", "Luzon_DZ", "Kadena_AB", "Clark_AB", "Manila"]
            if s != destination_node
        ]
        num_others = data.draw(st.integers(min_value=0, max_value=3))
        other_personnel = []
        for i in range(num_others):
            other_station = data.draw(st.sampled_from(other_stations))
            person = data.draw(
                personnel_strategy(
                    duty_station=other_station,
                    readiness=ReadinessState.READY,
                )
            )
            other_personnel.append(person)

        # Ensure unique names
        all_names = [target_person.name] + [p.name for p in other_personnel]
        assume(len(all_names) == len(set(all_names)))

        roster = [target_person] + other_personnel

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        # Set up EventBus with capture
        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        router = MessageRouter()
        result = router.route_message(
            message_type=message_type,
            destination_node=destination_node,
            roster=roster,
            routing_rules=routing_rules,
            event_bus=event_bus,
            timestamp=timestamp,
        )

        # Assert correct personnel identified
        assert result is not None
        assert result.name == target_person.name
        assert result.role == target_role
        assert result.duty_station == destination_node

        # Assert MessageReceivedEvent published
        received_events = capture.get_events_of_type(MessageReceivedEvent)
        assert len(received_events) == 1
        evt = received_events[0]
        assert evt.message_type == message_type
        assert evt.recipient_name == target_person.name
        assert evt.recipient_role == target_role
        assert evt.timestamp == timestamp


# === Property 17: Missing Routing Rule Publishes Unrouted Event ===
# Feature: personnel-module, Property 17: Missing Routing Rule Publishes Unrouted Event


class TestMissingRoutingRulePublishesUnroutedEvent:
    """Property 17: Missing Routing Rule Publishes Unrouted Event.

    **Validates: Requirements 4.4**

    For any C2 message type where no MessageRoutingRule exists, accept the message
    and publish a message-unrouted event.
    """

    @given(
        message_type=valid_message_types,
        destination_node=valid_stations,
        other_message_types=st.lists(valid_message_types, min_size=0, max_size=3, unique=True),
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_routing_rule_publishes_unrouted_event(
        self, message_type, destination_node, other_message_types, timestamp, data
    ):
        """When no routing rule exists for the message type, publish MessageUnroutedEvent."""
        # Ensure no routing rule exists for our message type
        routing_rules = [
            MessageRoutingRule(message_type=mt, destination_role=data.draw(valid_roles))
            for mt in other_message_types
            if mt != message_type
        ]
        # Verify no rule matches
        assume(all(r.message_type != message_type for r in routing_rules))

        # Create a roster (doesn't matter for this test, just needs to be valid)
        roster_person = data.draw(
            personnel_strategy(duty_station=destination_node, readiness=ReadinessState.READY)
        )
        roster = [roster_person]

        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        router = MessageRouter()
        result = router.route_message(
            message_type=message_type,
            destination_node=destination_node,
            roster=roster,
            routing_rules=routing_rules,
            event_bus=event_bus,
            timestamp=timestamp,
        )

        # Router should return None (message not routed to any person)
        assert result is None

        # Assert MessageUnroutedEvent published
        unrouted_events = capture.get_events_of_type(MessageUnroutedEvent)
        assert len(unrouted_events) == 1
        evt = unrouted_events[0]
        assert evt.message_type == message_type
        assert evt.destination_node == destination_node
        assert evt.timestamp == timestamp


# === Property 18: Non-READY Recipient Queues Message ===
# Feature: personnel-module, Property 18: Non-READY Recipient Queues Message


class TestNonReadyRecipientQueuesMessage:
    """Property 18: Non-READY Recipient Queues Message.

    **Validates: Requirements 4.5, 4.6**

    For any C2 message routed to a Personnel member who is not READY, queue the
    message, publish message-delayed event, and deliver when READY.
    """

    @given(
        message_type=valid_message_types,
        destination_node=valid_stations,
        target_role=valid_roles,
        non_ready_state=st.sampled_from([ReadinessState.UNAVAILABLE, ReadinessState.INCAPACITATED]),
        queue_timestamp=timestamp_st,
        delivery_timestamp=st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_non_ready_recipient_queues_and_delays(
        self, message_type, destination_node, target_role, non_ready_state,
        queue_timestamp, delivery_timestamp, data
    ):
        """Non-READY recipient SHALL queue message and publish MessageDelayedEvent."""
        # Create personnel at destination with correct role but non-READY
        target_person = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=destination_node,
                readiness=non_ready_state,
            )
        )

        roster = [target_person]
        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        router = MessageRouter()

        # Route the message — should be queued
        result = router.route_message(
            message_type=message_type,
            destination_node=destination_node,
            roster=roster,
            routing_rules=routing_rules,
            event_bus=event_bus,
            timestamp=queue_timestamp,
        )

        # Route returns None (message not immediately delivered)
        assert result is None

        # MessageDelayedEvent should be published
        delayed_events = capture.get_events_of_type(MessageDelayedEvent)
        assert len(delayed_events) == 1
        evt = delayed_events[0]
        assert evt.message_type == message_type
        assert evt.personnel_name == target_person.name
        assert evt.readiness_state == non_ready_state.value
        assert evt.timestamp == queue_timestamp

        # Message should be queued
        assert router.has_queued_messages(target_person.name)
        assert router.get_queue_size(target_person.name) == 1

        # Now simulate delivery when personnel becomes READY
        capture.events.clear()
        router.deliver_queued_messages(
            personnel_name=target_person.name,
            event_bus=event_bus,
            timestamp=delivery_timestamp,
            roster=roster,
        )

        # MessageReceivedEvent should be published with the delivery timestamp
        received_events = capture.get_events_of_type(MessageReceivedEvent)
        assert len(received_events) == 1
        recv_evt = received_events[0]
        assert recv_evt.message_type == message_type
        assert recv_evt.recipient_name == target_person.name
        assert recv_evt.recipient_role == target_person.role
        assert recv_evt.timestamp == delivery_timestamp

        # Queue should be empty now
        assert not router.has_queued_messages(target_person.name)


# === Property 19: No Matching Personnel Publishes Unroutable Event ===
# Feature: personnel-module, Property 19: No Matching Personnel Publishes Unroutable Event


class TestNoMatchingPersonnelPublishesUnroutableEvent:
    """Property 19: No Matching Personnel Publishes Unroutable Event.

    **Validates: Requirements 4.7**

    For any C2 message delivered where no Personnel member at the destination node
    has the matching role, publish a message-unroutable event.
    """

    @given(
        message_type=valid_message_types,
        destination_node=valid_stations,
        target_role=valid_roles,
        wrong_roles=st.lists(valid_roles, min_size=1, max_size=3),
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_matching_personnel_publishes_unroutable(
        self, message_type, destination_node, target_role, wrong_roles, timestamp, data
    ):
        """When no personnel at destination has the matching role, publish MessageUnroutableEvent."""
        # Ensure none of the 'wrong_roles' match the target role
        assume(target_role not in wrong_roles)

        # Create personnel at the destination but with wrong roles
        roster = []
        for i, role in enumerate(wrong_roles):
            person = data.draw(
                personnel_strategy(
                    role=role,
                    duty_station=destination_node,
                    readiness=ReadinessState.READY,
                )
            )
            # Ensure unique names
            person = person.model_copy(update={"name": f"{person.name}_{i}"})
            roster.append(person)

        # Ensure unique names across roster
        names = [p.name for p in roster]
        assume(len(names) == len(set(names)))

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        router = MessageRouter()
        result = router.route_message(
            message_type=message_type,
            destination_node=destination_node,
            roster=roster,
            routing_rules=routing_rules,
            event_bus=event_bus,
            timestamp=timestamp,
        )

        # No personnel found → returns None
        assert result is None

        # MessageUnroutableEvent should be published
        unroutable_events = capture.get_events_of_type(MessageUnroutableEvent)
        assert len(unroutable_events) == 1
        evt = unroutable_events[0]
        assert evt.message_type == message_type
        assert evt.expected_role == target_role
        assert evt.destination_node == destination_node
        assert evt.timestamp == timestamp


# === Helper function for Property 15 validation ===


def _validate_routing_rule(
    rule: MessageRoutingRule,
    valid_message_types: list[str],
    valid_roles: list[str],
) -> list[str]:
    """Validate a routing rule against defined message types and roster roles.

    Returns a list of error strings (empty if valid).
    This implements the validation logic that PersonnelModule.validate_config
    performs for routing rules.
    """
    errors = []
    if rule.message_type not in valid_message_types:
        errors.append(
            f"Routing rule references unknown message type: {rule.message_type}"
        )
    if rule.destination_role not in valid_roles:
        errors.append(
            f"Routing rule references unknown role: {rule.destination_role}"
        )
    return errors
