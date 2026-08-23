"""Property tests for C2 Message Module.

**Property 14: C2 Message Delivery Timing**
**Property 15: C2 Message Cross-Domain Latency Composition**
**Property 16: C2 Message Failure Handling**
**Validates: Requirements 6.2, 6.3, 6.4, 6.5, 6.6**

Property 14: For any C2 message routed to a reachable destination without crossing
a gateway, the delivery_time SHALL equal generation_time + transmission_latency,
and all three timestamps (generation, transmission_start, delivery) SHALL satisfy
generation ≤ transmission_start ≤ delivery.

Property 15: For any C2 message traversing a Cross_Domain_Gateway with a permitted
classification, the total delivery time SHALL equal transmission_latency +
sanitization_latency.

Property 16: For any C2 message directed to a destination not present in the
configuration, the module SHALL publish a message-delivery-failure event containing
the message type, source, intended destination, and generation timestamp, and SHALL
retain the generation and transmission-start timestamps on the message record.
"""

from __future__ import annotations

import simpy
from hypothesis import given, settings
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.events import MessageDeliveryFailureEvent
from enterprise_sim.models.messaging import C2MessageType
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.models.security import (
    CrossDomainGatewayConfig,
    RoutingRule,
    SecurityEnclave,
)
from enterprise_sim.modules.c2_message import C2MessageModule


# === Hypothesis Strategies ===

# Transmission latency: non-negative floats in a reasonable simulation range
transmission_latency_st = st.floats(
    min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False
)

# Sanitization latency: non-negative floats
sanitization_latency_st = st.floats(
    min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False
)

# Node name strategy - simple alphanumeric names
node_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L",)),
    min_size=2,
    max_size=10,
)

# Message name strategy
message_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=15,
)

# Classification strategy
classification_st = st.sampled_from([
    "UNCLASSIFIED",
    "CONFIDENTIAL",
    "SECRET",
    "TOP_SECRET",
])


def _make_minimal_route_graph(node_names: list[str]) -> RouteGraphConfig:
    """Build a minimal route graph with the given node names as a chain."""
    # Ensure at least 2 nodes
    if len(node_names) < 2:
        node_names = ["Origin", "Dest"]

    nodes = [
        RouteNode(entity_id=f"n_{name}", name=name, node_type="waypoint")
        for name in node_names
    ]

    # Create edges forming a chain: n0 -> n1 -> ... -> nN
    edges = [
        RouteEdge(source=node_names[i], destination=node_names[i + 1], distance_nm=100.0)
        for i in range(len(node_names) - 1)
    ]

    return RouteGraphConfig(
        nodes=nodes,
        edges=edges,
        origin=node_names[0],
        destination=node_names[-1],
    )


def _make_aircraft_config() -> AircraftConfig:
    """Build a minimal valid aircraft config."""
    return AircraftConfig(
        entity_id="ac1",
        aircraft_type="C-17",
        lift_to_drag_ratio=15.0,
        specific_fuel_consumption=0.6,
        initial_fuel_weight=50000.0,
        max_fuel_capacity=60000.0,
    )


@st.composite
def reachable_message_scenario_st(draw):
    """Generate a C2 message scenario where destination IS reachable (no gateway).

    Returns:
        - transmission_latency (float)
        - message_name (str)
        - source_node (str)
        - destination_node (str)
    """
    transmission_latency = draw(transmission_latency_st)
    message_name = draw(message_name_st)

    # Use fixed node names for route graph to ensure reachability
    source_node = "SourceBase"
    destination_node = "DestBase"

    return transmission_latency, message_name, source_node, destination_node


@st.composite
def cross_domain_message_scenario_st(draw):
    """Generate a C2 message scenario with cross-domain gateway traversal.

    Returns:
        - transmission_latency (float)
        - sanitization_latency (float)
        - message_name (str)
        - classification (str)
        - source_node (str)
        - destination_node (str)
    """
    transmission_latency = draw(transmission_latency_st)
    sanitization_latency = draw(sanitization_latency_st)
    message_name = draw(message_name_st)
    classification = draw(classification_st)

    source_node = "SourceBase"
    destination_node = "DestBase"

    return (
        transmission_latency,
        sanitization_latency,
        message_name,
        classification,
        source_node,
        destination_node,
    )


@st.composite
def unreachable_message_scenario_st(draw):
    """Generate a C2 message scenario where destination is NOT reachable.

    Returns:
        - transmission_latency (float)
        - message_name (str)
        - source_node (str)
        - unreachable_destination (str)
    """
    transmission_latency = draw(transmission_latency_st)
    message_name = draw(message_name_st)

    source_node = "SourceBase"
    # Generate a destination name that won't be in the route graph
    unreachable_destination = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L",)),
            min_size=3,
            max_size=12,
        ).filter(lambda x: x not in ("SourceBase", "DestBase"))
    )

    return transmission_latency, message_name, source_node, unreachable_destination


class TestC2MessageDeliveryTiming:
    """Property 14: C2 Message Delivery Timing.

    **Validates: Requirements 6.2, 6.3**

    For any C2 message routed to a reachable destination without crossing a gateway,
    the delivery_time SHALL equal generation_time + transmission_latency, and all
    three timestamps (generation, transmission_start, delivery) SHALL satisfy
    generation ≤ transmission_start ≤ delivery.
    """

    @given(data=reachable_message_scenario_st())
    @settings(max_examples=200)
    def test_delivery_time_equals_generation_plus_latency(self, data):
        """delivery_time = generation_time + transmission_latency for reachable
        destinations without gateway traversal."""
        transmission_latency, message_name, source_node, destination_node = data

        # Build config with route graph containing source and destination nodes
        route_graph = _make_minimal_route_graph([source_node, destination_node])

        msg_type = C2MessageType(
            entity_id=f"msg_type_{message_name}",
            name=message_name,
            source_node=source_node,
            destination_node=destination_node,
            priority=1,
            transmission_latency=transmission_latency,
        )

        config = MissionConfiguration(
            mission_id="test-mission",
            mission_name="Test Mission",
            route_graph=route_graph,
            aircraft=_make_aircraft_config(),
            c2_messages=[msg_type],
            active_modules=["c2_message"],
        )

        # Set up module
        env = simpy.Environment()
        event_bus = EventBus()
        module = C2MessageModule()
        module.initialize(env, event_bus, config)

        # Run the simulation
        module.create_processes(env)
        env.run()

        # Verify message was delivered
        assert len(module.message_log) == 1
        instance = module.message_log[0]
        assert instance.status == "delivered"

        # Property: delivery_time = generation_time + transmission_latency
        expected_delivery = instance.generation_time + transmission_latency
        assert instance.delivery_time == expected_delivery, (
            f"delivery_time {instance.delivery_time} != "
            f"generation_time {instance.generation_time} + "
            f"transmission_latency {transmission_latency} = {expected_delivery}"
        )

    @given(data=reachable_message_scenario_st())
    @settings(max_examples=200)
    def test_timestamp_ordering(self, data):
        """generation ≤ transmission_start ≤ delivery for all delivered messages."""
        transmission_latency, message_name, source_node, destination_node = data

        route_graph = _make_minimal_route_graph([source_node, destination_node])

        msg_type = C2MessageType(
            entity_id=f"msg_type_{message_name}",
            name=message_name,
            source_node=source_node,
            destination_node=destination_node,
            priority=1,
            transmission_latency=transmission_latency,
        )

        config = MissionConfiguration(
            mission_id="test-mission",
            mission_name="Test Mission",
            route_graph=route_graph,
            aircraft=_make_aircraft_config(),
            c2_messages=[msg_type],
            active_modules=["c2_message"],
        )

        env = simpy.Environment()
        event_bus = EventBus()
        module = C2MessageModule()
        module.initialize(env, event_bus, config)

        module.create_processes(env)
        env.run()

        assert len(module.message_log) == 1
        instance = module.message_log[0]
        assert instance.status == "delivered"

        # Property: generation ≤ transmission_start ≤ delivery
        assert instance.generation_time <= instance.transmission_start_time, (
            f"generation_time {instance.generation_time} > "
            f"transmission_start_time {instance.transmission_start_time}"
        )
        assert instance.transmission_start_time <= instance.delivery_time, (
            f"transmission_start_time {instance.transmission_start_time} > "
            f"delivery_time {instance.delivery_time}"
        )


class TestC2MessageCrossDomainLatencyComposition:
    """Property 15: C2 Message Cross-Domain Latency Composition.

    **Validates: Requirements 6.5, 6.6**

    For any C2 message traversing a Cross_Domain_Gateway with a permitted
    classification, the total delivery time SHALL equal transmission_latency +
    sanitization_latency.
    """

    @given(data=cross_domain_message_scenario_st())
    @settings(max_examples=200)
    def test_delivery_time_includes_sanitization_latency(self, data):
        """Total delivery time = transmission_latency + sanitization_latency
        when traversing a cross-domain gateway."""
        (
            transmission_latency,
            sanitization_latency,
            message_name,
            classification,
            source_node,
            destination_node,
        ) = data

        route_graph = _make_minimal_route_graph([source_node, destination_node])

        msg_type = C2MessageType(
            entity_id=f"msg_type_{message_name}",
            name=message_name,
            source_node=source_node,
            destination_node=destination_node,
            priority=1,
            transmission_latency=transmission_latency,
            classification=classification,
        )

        # Set up cross-domain gateway config with a permissive rule
        gateway_config = CrossDomainGatewayConfig(
            sanitization_latency=sanitization_latency,
            enclaves=[
                SecurityEnclave(
                    entity_id="enc_source",
                    name="SourceEnclave",
                    classification_level="SECRET",
                ),
                SecurityEnclave(
                    entity_id="enc_dest",
                    name="DestEnclave",
                    classification_level="SECRET",
                ),
            ],
            routing_rules=[
                RoutingRule(
                    source_enclave="SourceEnclave",
                    destination_enclave="DestEnclave",
                    permitted_classifications=[
                        "UNCLASSIFIED",
                        "CONFIDENTIAL",
                        "SECRET",
                        "TOP_SECRET",
                    ],
                )
            ],
        )

        config = MissionConfiguration(
            mission_id="test-mission",
            mission_name="Test Mission",
            route_graph=route_graph,
            aircraft=_make_aircraft_config(),
            c2_messages=[msg_type],
            cross_domain_gateway=gateway_config,
            active_modules=["c2_message", "cross_domain"],
        )

        env = simpy.Environment()
        event_bus = EventBus()
        module = C2MessageModule()
        module.initialize(env, event_bus, config)

        module.create_processes(env)
        env.run()

        # Verify message was delivered
        assert len(module.message_log) == 1
        instance = module.message_log[0]
        assert instance.status == "delivered"

        # Property: delivery_time = generation_time + transmission_latency + sanitization_latency
        expected_delivery = instance.generation_time + transmission_latency + sanitization_latency
        assert instance.delivery_time == expected_delivery, (
            f"delivery_time {instance.delivery_time} != "
            f"generation_time {instance.generation_time} + "
            f"transmission_latency {transmission_latency} + "
            f"sanitization_latency {sanitization_latency} = {expected_delivery}"
        )


class TestC2MessageFailureHandling:
    """Property 16: C2 Message Failure Handling.

    **Validates: Requirements 6.4, 6.6**

    For any C2 message directed to a destination not present in the configuration,
    the module SHALL publish a message-delivery-failure event containing the message
    type, source, intended destination, and generation timestamp, and SHALL retain
    the generation and transmission-start timestamps on the message record.
    """

    @given(data=unreachable_message_scenario_st())
    @settings(max_examples=200)
    def test_failure_event_published_for_unreachable_destination(self, data):
        """A message-delivery-failure event is published when destination is unreachable."""
        transmission_latency, message_name, source_node, unreachable_destination = data

        # Route graph does NOT contain the unreachable_destination
        route_graph = _make_minimal_route_graph([source_node, "DestBase"])

        msg_type = C2MessageType(
            entity_id=f"msg_type_{message_name}",
            name=message_name,
            source_node=source_node,
            destination_node=unreachable_destination,
            priority=1,
            transmission_latency=transmission_latency,
        )

        config = MissionConfiguration(
            mission_id="test-mission",
            mission_name="Test Mission",
            route_graph=route_graph,
            aircraft=_make_aircraft_config(),
            c2_messages=[msg_type],
            active_modules=["c2_message"],
        )

        env = simpy.Environment()
        event_bus = EventBus()
        module = C2MessageModule()
        module.initialize(env, event_bus, config)

        # Collect failure events
        failure_events: list[MessageDeliveryFailureEvent] = []
        event_bus.subscribe(
            MessageDeliveryFailureEvent,
            lambda e: failure_events.append(e),
            "test_failure",
        )

        module.create_processes(env)
        env.run()

        # Property: failure event published with correct fields
        assert len(failure_events) == 1, (
            f"Expected 1 failure event, got {len(failure_events)}"
        )
        failure_event = failure_events[0]
        assert failure_event.message_type == message_name
        assert failure_event.source == source_node
        assert failure_event.intended_destination == unreachable_destination
        assert failure_event.generation_time >= 0

    @given(data=unreachable_message_scenario_st())
    @settings(max_examples=200)
    def test_failed_message_retains_timestamps(self, data):
        """Failed messages retain generation_time and transmission_start_time."""
        transmission_latency, message_name, source_node, unreachable_destination = data

        route_graph = _make_minimal_route_graph([source_node, "DestBase"])

        msg_type = C2MessageType(
            entity_id=f"msg_type_{message_name}",
            name=message_name,
            source_node=source_node,
            destination_node=unreachable_destination,
            priority=1,
            transmission_latency=transmission_latency,
        )

        config = MissionConfiguration(
            mission_id="test-mission",
            mission_name="Test Mission",
            route_graph=route_graph,
            aircraft=_make_aircraft_config(),
            c2_messages=[msg_type],
            active_modules=["c2_message"],
        )

        env = simpy.Environment()
        event_bus = EventBus()
        module = C2MessageModule()
        module.initialize(env, event_bus, config)

        module.create_processes(env)
        env.run()

        # Property: failed message retains timestamps
        assert len(module.message_log) == 1
        instance = module.message_log[0]
        assert instance.status == "failed"

        # generation_time should be set (>= 0)
        assert instance.generation_time >= 0
        # transmission_start_time should be retained (set to generation_time per implementation)
        assert instance.transmission_start_time is not None
        assert instance.transmission_start_time >= 0

    @given(data=unreachable_message_scenario_st())
    @settings(max_examples=200)
    def test_failure_event_generation_time_matches_message(self, data):
        """The generation_time in the failure event matches the message's generation_time."""
        transmission_latency, message_name, source_node, unreachable_destination = data

        route_graph = _make_minimal_route_graph([source_node, "DestBase"])

        msg_type = C2MessageType(
            entity_id=f"msg_type_{message_name}",
            name=message_name,
            source_node=source_node,
            destination_node=unreachable_destination,
            priority=1,
            transmission_latency=transmission_latency,
        )

        config = MissionConfiguration(
            mission_id="test-mission",
            mission_name="Test Mission",
            route_graph=route_graph,
            aircraft=_make_aircraft_config(),
            c2_messages=[msg_type],
            active_modules=["c2_message"],
        )

        env = simpy.Environment()
        event_bus = EventBus()
        module = C2MessageModule()
        module.initialize(env, event_bus, config)

        failure_events: list[MessageDeliveryFailureEvent] = []
        event_bus.subscribe(
            MessageDeliveryFailureEvent,
            lambda e: failure_events.append(e),
            "test_failure",
        )

        module.create_processes(env)
        env.run()

        # Property: failure event generation_time matches message record
        assert len(failure_events) == 1
        assert len(module.message_log) == 1

        failure_event = failure_events[0]
        instance = module.message_log[0]
        assert failure_event.generation_time == instance.generation_time
