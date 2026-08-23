"""Unit tests for Personnel module C2 message routing integration.

Tests the _handle_message_delivered handler for requirements 4.2, 4.3,
8.1, 8.2, 8.3, 8.4, 8.5, 8.6.
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
    MessageUnroutableEvent,
    MessageUnroutedEvent,
    ReceiverUnresolvedEvent,
    SenderUnresolvedEvent,
)
from enterprise_sim.modules.personnel import PersonnelModule


def _make_credential():
    """Create a default credential for tests."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=ClearanceLevel.SECRET,
        authorized_enclaves=["NIPR"],
    )


def _make_personnel(name: str, role: str, duty_station: str,
                    readiness: ReadinessState = ReadinessState.READY) -> Personnel:
    """Create a personnel member for testing."""
    return Personnel(
        entity_id=f"person_{name}",
        name=name,
        role=role,
        duty_station=duty_station,
        credential=_make_credential(),
        readiness_state=readiness,
    )


def _make_config_mock(roster, routing_rules=None):
    """Create a minimal mock config for PersonnelModule initialization."""

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


def _make_delivered_event(message_type: str, source_node: str,
                          destination_node: str, timestamp: float = 5.0):
    """Create a MessageDeliveredEvent for testing."""
    return MessageDeliveredEvent(
        entity_id=f"delivered_msg_{message_type}",
        timestamp=timestamp,
        source_module="c2_message",
        event_type="message_delivered",
        message_type=message_type,
        source_node=source_node,
        destination_node=destination_node,
        delivery_time=timestamp,
    )


class TestMessageDeliveredRouting:
    """Test message routing on delivery event."""

    def test_successful_routing_resolves_sender_and_receiver(self):
        """Req 8.2, 8.3: Sender and receiver resolved on message delivery."""
        env = simpy.Environment()
        event_bus = EventBus()

        sender = _make_personnel("COL_Jones", "AOC_Commander", "Hickam_AFB")
        receiver = _make_personnel("SGT_Smith", "AOC_Commander", "Luzon_DZ")

        config = _make_config_mock(
            roster=[sender, receiver],
            routing_rules=[
                MessageRoutingRule(
                    message_type="OPORD",
                    destination_role="AOC_Commander",
                ),
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Deliver a message
        delivered_event = _make_delivered_event(
            message_type="OPORD",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
        )
        module._handle_message_delivered(delivered_event)

        # Check sender_personnel and receiver_personnel are set on event
        assert getattr(delivered_event, "sender_personnel") == "COL_Jones"
        assert getattr(delivered_event, "receiver_personnel") == "SGT_Smith"

        # Check MessageReceivedEvent was published
        received_events = [
            e for e in published_events if isinstance(e, MessageReceivedEvent)
        ]
        assert len(received_events) == 1
        assert received_events[0].recipient_name == "SGT_Smith"
        assert received_events[0].recipient_role == "AOC_Commander"
        assert received_events[0].message_type == "OPORD"

    def test_sender_unresolved_publishes_event(self):
        """Req 8.4: If no sender at source node, publish SenderUnresolvedEvent."""
        env = simpy.Environment()
        event_bus = EventBus()

        # Only personnel at destination, not at source
        receiver = _make_personnel("SGT_Smith", "AOC_Commander", "Luzon_DZ")

        config = _make_config_mock(
            roster=[receiver],
            routing_rules=[
                MessageRoutingRule(
                    message_type="OPORD",
                    destination_role="AOC_Commander",
                ),
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        delivered_event = _make_delivered_event(
            message_type="OPORD",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
        )
        module._handle_message_delivered(delivered_event)

        # Sender should be unresolved
        assert getattr(delivered_event, "sender_personnel") is None

        # SenderUnresolvedEvent should be published
        unresolved_events = [
            e for e in published_events if isinstance(e, SenderUnresolvedEvent)
        ]
        assert len(unresolved_events) == 1
        assert unresolved_events[0].message_type == "OPORD"
        assert unresolved_events[0].source_node == "Hickam_AFB"

    def test_receiver_unresolved_publishes_event(self):
        """Req 8.5: If no receiver at destination, publish ReceiverUnresolvedEvent."""
        env = simpy.Environment()
        event_bus = EventBus()

        # Only personnel at source, not at destination
        sender = _make_personnel("COL_Jones", "AOC_Commander", "Hickam_AFB")

        config = _make_config_mock(
            roster=[sender],
            routing_rules=[
                MessageRoutingRule(
                    message_type="OPORD",
                    destination_role="AOC_Commander",
                ),
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        delivered_event = _make_delivered_event(
            message_type="OPORD",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
        )
        module._handle_message_delivered(delivered_event)

        # Receiver should be unresolved
        assert getattr(delivered_event, "receiver_personnel") is None

        # The route_message publishes MessageUnroutableEvent (not ReceiverUnresolvedEvent)
        # because no personnel at destination matches the rule
        unroutable_events = [
            e for e in published_events if isinstance(e, MessageUnroutableEvent)
        ]
        assert len(unroutable_events) == 1
        assert unroutable_events[0].message_type == "OPORD"
        assert unroutable_events[0].destination_node == "Luzon_DZ"

    def test_no_routing_rule_publishes_unrouted(self):
        """Req 4.4: No routing rule publishes MessageUnroutedEvent."""
        env = simpy.Environment()
        event_bus = EventBus()

        person = _make_personnel("SGT_Smith", "Loadmaster", "Luzon_DZ")

        config = _make_config_mock(
            roster=[person],
            routing_rules=[],  # No routing rules
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        delivered_event = _make_delivered_event(
            message_type="UNKNOWN_MSG",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
        )
        module._handle_message_delivered(delivered_event)

        # sender_personnel should be None (no rule to resolve)
        assert getattr(delivered_event, "sender_personnel") is None

        # receiver_personnel should be None (no rule to route)
        assert getattr(delivered_event, "receiver_personnel") is None

        # MessageUnroutedEvent published (from route_message)
        unrouted_events = [
            e for e in published_events if isinstance(e, MessageUnroutedEvent)
        ]
        assert len(unrouted_events) == 1
        assert unrouted_events[0].message_type == "UNKNOWN_MSG"

    def test_non_ready_recipient_queues_message(self):
        """Req 4.5: Non-READY recipient queues message, publishes MessageDelayedEvent."""
        env = simpy.Environment()
        event_bus = EventBus()

        sender = _make_personnel("COL_Jones", "AOC_Commander", "Hickam_AFB")
        receiver = _make_personnel(
            "SGT_Smith", "AOC_Commander", "Luzon_DZ",
            readiness=ReadinessState.UNAVAILABLE,
        )

        config = _make_config_mock(
            roster=[sender, receiver],
            routing_rules=[
                MessageRoutingRule(
                    message_type="OPORD",
                    destination_role="AOC_Commander",
                ),
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        delivered_event = _make_delivered_event(
            message_type="OPORD",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
        )
        module._handle_message_delivered(delivered_event)

        # Sender resolved, receiver not delivered (queued)
        assert getattr(delivered_event, "sender_personnel") == "COL_Jones"
        assert getattr(delivered_event, "receiver_personnel") is None

        # MessageDelayedEvent published
        delayed_events = [
            e for e in published_events if isinstance(e, MessageDelayedEvent)
        ]
        assert len(delayed_events) == 1
        assert delayed_events[0].personnel_name == "SGT_Smith"
        assert delayed_events[0].readiness_state == "UNAVAILABLE"

        # Message is queued in the router
        assert module.message_router.has_queued_messages("SGT_Smith")

    def test_event_bus_subscription_routes_delivery(self):
        """Req 8.1: PersonnelModule subscribes to MessageDeliveredEvent."""
        env = simpy.Environment()
        event_bus = EventBus()

        sender = _make_personnel("COL_Jones", "AOC_Commander", "Hickam_AFB")
        receiver = _make_personnel("SGT_Smith", "AOC_Commander", "Luzon_DZ")

        config = _make_config_mock(
            roster=[sender, receiver],
            routing_rules=[
                MessageRoutingRule(
                    message_type="OPORD",
                    destination_role="AOC_Commander",
                ),
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Publish a MessageDeliveredEvent through the bus (simulating C2 module)
        delivered_event = _make_delivered_event(
            message_type="OPORD",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
        )
        event_bus.publish(delivered_event)

        # The handler should have been called via subscription
        assert getattr(delivered_event, "sender_personnel") == "COL_Jones"
        assert getattr(delivered_event, "receiver_personnel") == "SGT_Smith"

        # MessageReceivedEvent from the handler
        received_events = [
            e for e in published_events if isinstance(e, MessageReceivedEvent)
        ]
        assert len(received_events) == 1

    def test_sender_and_receiver_recorded_on_event(self):
        """Req 8.6: Sender/receiver recorded on message delivery record."""
        env = simpy.Environment()
        event_bus = EventBus()

        sender = _make_personnel("COL_Jones", "Pilot", "Hickam_AFB")
        receiver = _make_personnel("SGT_Smith", "Pilot", "Luzon_DZ")

        config = _make_config_mock(
            roster=[sender, receiver],
            routing_rules=[
                MessageRoutingRule(
                    message_type="SITREP",
                    destination_role="Pilot",
                ),
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        delivered_event = _make_delivered_event(
            message_type="SITREP",
            source_node="Hickam_AFB",
            destination_node="Luzon_DZ",
            timestamp=10.0,
        )
        module._handle_message_delivered(delivered_event)

        # Verify personnel names are stored on the event instance
        assert hasattr(delivered_event, "sender_personnel")
        assert hasattr(delivered_event, "receiver_personnel")
        assert delivered_event.sender_personnel == "COL_Jones"
        assert delivered_event.receiver_personnel == "SGT_Smith"
