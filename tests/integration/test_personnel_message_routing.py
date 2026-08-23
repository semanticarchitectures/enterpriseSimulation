"""Integration tests for personnel message routing end-to-end.

Exercises the full message delivery flow through the PersonnelModule:
1. Set up PersonnelModule with routing rules and roster
2. Publish a MessageDeliveredEvent via EventBus
3. Verify the correct recipient is identified and MessageReceivedEvent is published
4. Test non-READY recipient queuing and delivery on readiness change
5. Verify sender_personnel and receiver_personnel are recorded on the event

Requirements: 4.2, 4.3, 4.5, 4.6, 8.1, 8.2, 8.3, 8.6
"""

import simpy

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import MessageDeliveredEvent, SimulationEvent
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
    ReadinessChangedEvent,
)
from enterprise_sim.modules.personnel import PersonnelModule


def _make_credential():
    """Create a default credential for tests."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=ClearanceLevel.SECRET,
        authorized_enclaves=["NIPR"],
    )


def _make_personnel(
    name: str,
    role: str,
    duty_station: str,
    readiness: ReadinessState = ReadinessState.READY,
) -> Personnel:
    """Create a personnel member for testing."""
    return Personnel(
        entity_id=f"person_{name}",
        name=name,
        role=role,
        duty_station=duty_station,
        credential=_make_credential(),
        readiness_state=readiness,
    )


def _make_config(roster, routing_rules=None):
    """Create a minimal mock config for PersonnelModule initialization.

    Builds a mock MissionConfiguration with the necessary attributes
    for personnel module initialization.
    """

    class MockRouteGraph:
        nodes = [type("Node", (), {"name": p.duty_station})() for p in roster]

    class MockConfig:
        route_graph = MockRouteGraph()
        c2_messages = []
        authorization_chain = None
        cross_domain_gateway = None
        personnel_parameters = PersonnelConfig(
            roster=roster,
            message_routing_rules=routing_rules or [],
        )

    return MockConfig()


def _make_delivered_event(
    message_type: str,
    source_node: str,
    destination_node: str,
    timestamp: float = 5.0,
):
    """Create a MessageDeliveredEvent for testing."""
    return MessageDeliveredEvent(
        entity_id=f"delivered_{message_type}_{timestamp}",
        timestamp=timestamp,
        source_module="c2_message",
        event_type="message_delivered",
        message_type=message_type,
        source_node=source_node,
        destination_node=destination_node,
        delivery_time=timestamp,
    )


class TestMessageRoutingEndToEnd:
    """Integration tests for the full message routing flow through EventBus."""

    def test_message_routed_to_correct_personnel_by_role_and_duty_station(self):
        """Req 4.2, 4.3: Message delivered via EventBus is routed to correct
        personnel based on role matching and duty station location.

        Full flow:
        1. PersonnelModule subscribes to MessageDeliveredEvent during init
        2. A MessageDeliveredEvent is published on the EventBus
        3. Module resolves the recipient by role + duty_station
        4. MessageReceivedEvent is published with correct recipient info
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Set up a roster with personnel at different duty stations and roles
        pilot_sender = _make_personnel("MAJ_Torres", "Pilot", "Hickam_AFB")
        loadmaster = _make_personnel("SGT_Davis", "Loadmaster", "Hickam_AFB")
        aoc_receiver = _make_personnel("COL_Park", "AOC_Commander", "Luzon_DZ")
        medic_receiver = _make_personnel("CPT_Lee", "FlightSurgeon", "Luzon_DZ")

        routing_rules = [
            MessageRoutingRule(message_type="OPORD", destination_role="AOC_Commander"),
            MessageRoutingRule(message_type="MEDEVAC", destination_role="FlightSurgeon"),
        ]

        config = _make_config(
            roster=[pilot_sender, loadmaster, aoc_receiver, medic_receiver],
            routing_rules=routing_rules,
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect all published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Publish a MessageDeliveredEvent through the bus (simulates C2 module behavior)
        delivered_event = _make_delivered_event(
            message_type="OPORD",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
            timestamp=10.0,
        )
        event_bus.publish(delivered_event)

        # Verify MessageReceivedEvent was published for the correct recipient
        received_events = [
            e for e in published_events if isinstance(e, MessageReceivedEvent)
        ]
        assert len(received_events) == 1
        assert received_events[0].recipient_name == "COL_Park"
        assert received_events[0].recipient_role == "AOC_Commander"
        assert received_events[0].message_type == "OPORD"

        # Verify a different message type routes to a different personnel
        published_events.clear()
        medevac_event = _make_delivered_event(
            message_type="MEDEVAC",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
            timestamp=15.0,
        )
        event_bus.publish(medevac_event)

        received_events = [
            e for e in published_events if isinstance(e, MessageReceivedEvent)
        ]
        assert len(received_events) == 1
        assert received_events[0].recipient_name == "CPT_Lee"
        assert received_events[0].recipient_role == "FlightSurgeon"
        assert received_events[0].message_type == "MEDEVAC"

    def test_message_queued_when_recipient_non_ready_delivered_on_state_change(self):
        """Req 4.5, 4.6: Message is queued when recipient is non-READY,
        and delivered with MessageReceivedEvent when recipient transitions to READY.

        Full flow:
        1. Message arrives for UNAVAILABLE recipient → MessageDelayedEvent published
        2. Recipient transitions to READY via change_readiness()
        3. Queued message is delivered → MessageReceivedEvent published with actual timestamp
        """
        env = simpy.Environment()
        event_bus = EventBus()

        sender = _make_personnel("MAJ_Torres", "Pilot", "Hickam_AFB")
        receiver = _make_personnel(
            "COL_Park", "Pilot", "Luzon_DZ",
            readiness=ReadinessState.UNAVAILABLE,
        )

        routing_rules = [
            MessageRoutingRule(message_type="SITREP", destination_role="Pilot"),
        ]

        config = _make_config(
            roster=[sender, receiver],
            routing_rules=routing_rules,
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Step 1: Deliver a message to the UNAVAILABLE recipient
        delivered_event = _make_delivered_event(
            message_type="SITREP",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
            timestamp=5.0,
        )
        event_bus.publish(delivered_event)

        # Verify MessageDelayedEvent was published (not MessageReceivedEvent)
        delayed_events = [
            e for e in published_events if isinstance(e, MessageDelayedEvent)
        ]
        received_events = [
            e for e in published_events if isinstance(e, MessageReceivedEvent)
        ]
        assert len(delayed_events) == 1
        assert delayed_events[0].personnel_name == "COL_Park"
        assert delayed_events[0].readiness_state == "UNAVAILABLE"
        assert delayed_events[0].message_type == "SITREP"
        assert len(received_events) == 0

        # Verify message is queued in the router
        assert module.message_router.has_queued_messages("COL_Park")

        # Step 2: Transition recipient to READY state (simulates later in mission)
        published_events.clear()
        result = module.change_readiness("COL_Park", ReadinessState.READY, timestamp=20.0)
        assert result is None  # No error

        # Step 3: Verify MessageReceivedEvent was published on state change
        received_events = [
            e for e in published_events if isinstance(e, MessageReceivedEvent)
        ]
        assert len(received_events) == 1
        assert received_events[0].recipient_name == "COL_Park"
        assert received_events[0].recipient_role == "Pilot"
        assert received_events[0].message_type == "SITREP"
        # Delivery timestamp should reflect actual delivery time (when became READY)
        assert received_events[0].timestamp == 20.0

        # Verify readiness changed event was also published
        readiness_events = [
            e for e in published_events if isinstance(e, ReadinessChangedEvent)
        ]
        assert len(readiness_events) == 1
        assert readiness_events[0].personnel_name == "COL_Park"
        assert readiness_events[0].previous_state == "UNAVAILABLE"
        assert readiness_events[0].new_state == "READY"

        # Verify queue is now empty
        assert not module.message_router.has_queued_messages("COL_Park")

    def test_sender_and_receiver_resolution_recorded_on_delivery_record(self):
        """Req 8.1, 8.2, 8.3, 8.6: Sender and receiver personnel names
        are recorded on the message delivery event for Reporter output.

        Full flow:
        1. PersonnelModule subscribes to MessageDeliveredEvent during init (Req 8.1)
        2. On delivery, sender is resolved at source node (Req 8.2)
        3. On delivery, receiver is resolved at destination node (Req 8.3)
        4. Both are recorded on the event instance (Req 8.6)
        """
        env = simpy.Environment()
        event_bus = EventBus()

        sender = _make_personnel("COL_Jones", "AOC_Commander", "Hickam_AFB")
        receiver = _make_personnel("LT_Kim", "AOC_Commander", "Luzon_DZ")

        routing_rules = [
            MessageRoutingRule(message_type="FRAGORD", destination_role="AOC_Commander"),
        ]

        config = _make_config(
            roster=[sender, receiver],
            routing_rules=routing_rules,
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Publish MessageDeliveredEvent through the event bus
        delivered_event = _make_delivered_event(
            message_type="FRAGORD",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
            timestamp=12.0,
        )
        event_bus.publish(delivered_event)

        # Verify sender_personnel and receiver_personnel are recorded on the event
        assert delivered_event.sender_personnel == "COL_Jones"
        assert delivered_event.receiver_personnel == "LT_Kim"

    def test_multiple_messages_routed_independently(self):
        """Verify multiple messages in sequence each route to correct personnel."""
        env = simpy.Environment()
        event_bus = EventBus()

        pilot = _make_personnel("MAJ_Torres", "Pilot", "Hickam_AFB")
        loadmaster = _make_personnel("SGT_Davis", "Loadmaster", "Hickam_AFB")
        ground_pilot = _make_personnel("CPT_Reed", "Pilot", "Luzon_DZ")
        ground_lm = _make_personnel("SPC_Grant", "Loadmaster", "Luzon_DZ")

        routing_rules = [
            MessageRoutingRule(message_type="SITREP", destination_role="Pilot"),
            MessageRoutingRule(message_type="CARGO_MANIFEST", destination_role="Loadmaster"),
        ]

        config = _make_config(
            roster=[pilot, loadmaster, ground_pilot, ground_lm],
            routing_rules=routing_rules,
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # First message: SITREP → Pilot at Luzon_DZ
        sitrep_event = _make_delivered_event(
            message_type="SITREP",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
            timestamp=5.0,
        )
        event_bus.publish(sitrep_event)

        assert sitrep_event.sender_personnel == "MAJ_Torres"
        assert sitrep_event.receiver_personnel == "CPT_Reed"

        # Second message: CARGO_MANIFEST → Loadmaster at Luzon_DZ
        cargo_event = _make_delivered_event(
            message_type="CARGO_MANIFEST",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
            timestamp=6.0,
        )
        event_bus.publish(cargo_event)

        assert cargo_event.sender_personnel == "SGT_Davis"
        assert cargo_event.receiver_personnel == "SPC_Grant"

    def test_queued_messages_delivered_in_order_on_readiness_change(self):
        """Req 4.5, 4.6: Multiple queued messages are all delivered when
        personnel transitions to READY, preserving message type information."""
        env = simpy.Environment()
        event_bus = EventBus()

        sender = _make_personnel("MAJ_Torres", "Pilot", "Hickam_AFB")
        receiver = _make_personnel(
            "CPT_Reed", "Pilot", "Luzon_DZ",
            readiness=ReadinessState.INCAPACITATED,
        )

        routing_rules = [
            MessageRoutingRule(message_type="SITREP", destination_role="Pilot"),
        ]

        config = _make_config(
            roster=[sender, receiver],
            routing_rules=routing_rules,
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Queue multiple messages while recipient is INCAPACITATED
        for ts in [1.0, 2.0, 3.0]:
            event = _make_delivered_event(
                message_type="SITREP",
                source_node="Hickam_AFB",
                destination_node="Luzon_DZ",
                timestamp=ts,
            )
            event_bus.publish(event)

        # Verify all 3 messages are queued
        assert module.message_router.get_queue_size("CPT_Reed") == 3

        # Collect events after state change
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Transition to READY — should deliver all queued messages
        module.change_readiness("CPT_Reed", ReadinessState.READY, timestamp=50.0)

        received_events = [
            e for e in published_events if isinstance(e, MessageReceivedEvent)
        ]
        assert len(received_events) == 3
        # All delivered at the actual delivery timestamp
        for evt in received_events:
            assert evt.timestamp == 50.0
            assert evt.recipient_name == "CPT_Reed"
            assert evt.message_type == "SITREP"

        # Queue should be empty
        assert not module.message_router.has_queued_messages("CPT_Reed")
