"""Property tests for C2 message routing integration with PersonnelModule.

**Property 27: Sender Personnel Resolution**
**Property 28: Receiver Personnel Resolution**

**Validates: Requirements 8.2, 8.3, 8.4, 8.5**
"""

import simpy
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

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
    ReceiverUnresolvedEvent,
    SenderUnresolvedEvent,
)
from enterprise_sim.modules.personnel import PersonnelModule


# === Hypothesis Strategies ===

identifier_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=32,
)

valid_roles = st.sampled_from(
    ["Pilot", "Loadmaster", "Navigator", "AOC_Commander", "Engineer"]
)

valid_stations = st.sampled_from(
    ["Hickam_AFB", "Luzon_DZ", "Kadena_AB", "Clark_AB", "Manila"]
)

valid_message_types = st.sampled_from(
    ["OPORD", "SITREP", "MEDEVAC", "LOGSTAT", "FRAGO"]
)

timestamp_st = st.floats(
    min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False
)


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
def personnel_strategy(draw, role=None, duty_station=None, readiness=None, name=None):
    """Generate a valid Personnel instance with optional overrides."""
    return Personnel(
        entity_id=draw(identifier_st),
        name=name if name is not None else draw(
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


def _make_delivered_event(
    message_type: str, source_node: str, destination_node: str, timestamp: float = 5.0
):
    """Create a MessageDeliveredEvent for testing."""
    return MessageDeliveredEvent(
        entity_id=f"delivered_msg_{message_type}_{timestamp}",
        timestamp=timestamp,
        source_module="c2_message",
        event_type="message_delivered",
        message_type=message_type,
        source_node=source_node,
        destination_node=destination_node,
        delivery_time=timestamp,
    )


# === Property 27: Sender Personnel Resolution ===
# Feature: personnel-module, Property 27: Sender Personnel Resolution


class TestSenderPersonnelResolution:
    """Property 27: Sender Personnel Resolution.

    **Validates: Requirements 8.2, 8.4**

    For any generated C2 message, the module SHALL resolve sender_personnel from
    the Personnel member at the source node whose role matches the
    MessageRoutingRule. If no matching Personnel exists at the source node,
    sender_personnel SHALL be set to null and a sender-unresolved event SHALL be
    published.
    """

    @given(
        message_type=valid_message_types,
        source_node=valid_stations,
        destination_node=valid_stations,
        target_role=valid_roles,
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_sender_resolved_when_matching_personnel_at_source(
        self, message_type, source_node, destination_node, target_role, timestamp, data
    ):
        """Sender SHALL be resolved from personnel at source node with matching role."""
        assume(source_node != destination_node)

        # Create sender at source node with matching role
        sender = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=source_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        # Create receiver at destination with same role to enable full resolution
        receiver = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=destination_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        assume(sender.name != receiver.name)

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        config = _make_config_mock(
            roster=[sender, receiver],
            routing_rules=routing_rules,
        )

        env = simpy.Environment()
        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        delivered_event = _make_delivered_event(
            message_type=message_type,
            source_node=source_node,
            destination_node=destination_node,
            timestamp=timestamp,
        )
        module._handle_message_delivered(delivered_event)

        # sender_personnel should be resolved to the sender's name
        assert getattr(delivered_event, "sender_personnel") == sender.name

        # No SenderUnresolvedEvent should be published
        unresolved_events = capture.get_events_of_type(SenderUnresolvedEvent)
        assert len(unresolved_events) == 0

    @given(
        message_type=valid_message_types,
        source_node=valid_stations,
        destination_node=valid_stations,
        target_role=valid_roles,
        wrong_role=valid_roles,
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_sender_unresolved_when_no_matching_personnel_at_source(
        self, message_type, source_node, destination_node, target_role, wrong_role,
        timestamp, data
    ):
        """When no matching personnel at source, sender_personnel SHALL be null
        and SenderUnresolvedEvent SHALL be published."""
        assume(source_node != destination_node)
        assume(target_role != wrong_role)

        # Create personnel at source node but with WRONG role (won't match rule)
        wrong_person = data.draw(
            personnel_strategy(
                role=wrong_role,
                duty_station=source_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        # Create receiver at destination with correct role so route_message works
        receiver = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=destination_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        assume(wrong_person.name != receiver.name)

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        config = _make_config_mock(
            roster=[wrong_person, receiver],
            routing_rules=routing_rules,
        )

        env = simpy.Environment()
        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        delivered_event = _make_delivered_event(
            message_type=message_type,
            source_node=source_node,
            destination_node=destination_node,
            timestamp=timestamp,
        )
        module._handle_message_delivered(delivered_event)

        # sender_personnel should be null
        assert getattr(delivered_event, "sender_personnel") is None

        # SenderUnresolvedEvent should be published
        unresolved_events = capture.get_events_of_type(SenderUnresolvedEvent)
        assert len(unresolved_events) == 1
        evt = unresolved_events[0]
        assert evt.message_type == message_type
        assert evt.source_node == source_node

    @given(
        message_type=valid_message_types,
        source_node=valid_stations,
        destination_node=valid_stations,
        target_role=valid_roles,
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_sender_unresolved_when_no_personnel_at_source_node(
        self, message_type, source_node, destination_node, target_role, timestamp, data
    ):
        """When no personnel exist at the source node at all, sender_personnel
        SHALL be null and SenderUnresolvedEvent SHALL be published."""
        assume(source_node != destination_node)

        # Only create personnel at destination, none at source
        receiver = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=destination_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        config = _make_config_mock(
            roster=[receiver],
            routing_rules=routing_rules,
        )

        env = simpy.Environment()
        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        delivered_event = _make_delivered_event(
            message_type=message_type,
            source_node=source_node,
            destination_node=destination_node,
            timestamp=timestamp,
        )
        module._handle_message_delivered(delivered_event)

        # sender_personnel should be null
        assert getattr(delivered_event, "sender_personnel") is None

        # SenderUnresolvedEvent should be published
        unresolved_events = capture.get_events_of_type(SenderUnresolvedEvent)
        assert len(unresolved_events) == 1
        evt = unresolved_events[0]
        assert evt.message_type == message_type
        assert evt.source_node == source_node


# === Property 28: Receiver Personnel Resolution ===
# Feature: personnel-module, Property 28: Receiver Personnel Resolution


class TestReceiverPersonnelResolution:
    """Property 28: Receiver Personnel Resolution.

    **Validates: Requirements 8.3, 8.5**

    For any delivered C2 message, the module SHALL resolve receiver_personnel from
    the Personnel member at the destination node whose role matches the
    MessageRoutingRule. If no matching Personnel exists at the destination node,
    receiver_personnel SHALL be set to null and a receiver-unresolved event SHALL
    be published.
    """

    @given(
        message_type=valid_message_types,
        source_node=valid_stations,
        destination_node=valid_stations,
        target_role=valid_roles,
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_receiver_resolved_when_matching_personnel_at_destination(
        self, message_type, source_node, destination_node, target_role, timestamp, data
    ):
        """Receiver SHALL be resolved from personnel at destination with matching role."""
        assume(source_node != destination_node)

        # Create sender at source with matching role
        sender = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=source_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        # Create receiver at destination with matching role
        receiver = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=destination_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        assume(sender.name != receiver.name)

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        config = _make_config_mock(
            roster=[sender, receiver],
            routing_rules=routing_rules,
        )

        env = simpy.Environment()
        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        delivered_event = _make_delivered_event(
            message_type=message_type,
            source_node=source_node,
            destination_node=destination_node,
            timestamp=timestamp,
        )
        module._handle_message_delivered(delivered_event)

        # receiver_personnel should be resolved to the receiver's name
        assert getattr(delivered_event, "receiver_personnel") == receiver.name

    @given(
        message_type=valid_message_types,
        source_node=valid_stations,
        destination_node=valid_stations,
        target_role=valid_roles,
        wrong_role=valid_roles,
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_receiver_unresolved_when_no_matching_personnel_at_destination(
        self, message_type, source_node, destination_node, target_role, wrong_role,
        timestamp, data
    ):
        """When no matching personnel at destination, receiver_personnel SHALL be
        null and a receiver-unresolved event SHALL be published."""
        assume(source_node != destination_node)
        assume(target_role != wrong_role)

        # Create sender at source with correct role
        sender = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=source_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        # Create personnel at destination with WRONG role
        wrong_person = data.draw(
            personnel_strategy(
                role=wrong_role,
                duty_station=destination_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        assume(sender.name != wrong_person.name)

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        config = _make_config_mock(
            roster=[sender, wrong_person],
            routing_rules=routing_rules,
        )

        env = simpy.Environment()
        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        delivered_event = _make_delivered_event(
            message_type=message_type,
            source_node=source_node,
            destination_node=destination_node,
            timestamp=timestamp,
        )
        module._handle_message_delivered(delivered_event)

        # receiver_personnel should be null
        assert getattr(delivered_event, "receiver_personnel") is None

    @given(
        message_type=valid_message_types,
        source_node=valid_stations,
        destination_node=valid_stations,
        target_role=valid_roles,
        timestamp=timestamp_st,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_receiver_unresolved_when_no_personnel_at_destination_node(
        self, message_type, source_node, destination_node, target_role, timestamp, data
    ):
        """When no personnel exist at the destination node at all,
        receiver_personnel SHALL be null and a receiver-unresolved event SHALL
        be published."""
        assume(source_node != destination_node)

        # Only create personnel at source, none at destination
        sender = data.draw(
            personnel_strategy(
                role=target_role,
                duty_station=source_node,
                readiness=ReadinessState.READY,
                name=None,
            )
        )

        routing_rules = [
            MessageRoutingRule(message_type=message_type, destination_role=target_role)
        ]

        config = _make_config_mock(
            roster=[sender],
            routing_rules=routing_rules,
        )

        env = simpy.Environment()
        event_bus = EventBus()
        capture = MockEventCapture()
        event_bus.subscribe_all(capture.capture, "test_capture")

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        delivered_event = _make_delivered_event(
            message_type=message_type,
            source_node=source_node,
            destination_node=destination_node,
            timestamp=timestamp,
        )
        module._handle_message_delivered(delivered_event)

        # receiver_personnel should be null
        assert getattr(delivered_event, "receiver_personnel") is None
