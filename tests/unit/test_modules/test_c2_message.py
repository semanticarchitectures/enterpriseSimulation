"""Unit tests for the C2MessageModule.

Tests message flow lifecycle, reachability checks, cross-domain gateway
traversal, failure event publication, and timing.
"""

from __future__ import annotations

import simpy
import pytest

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


def _make_mission_config(
    c2_messages: list[C2MessageType] | None = None,
    cross_domain_gateway: CrossDomainGatewayConfig | None = None,
    extra_nodes: list[RouteNode] | None = None,
    extra_edges: list[RouteEdge] | None = None,
) -> MissionConfiguration:
    """Build a minimal MissionConfiguration for C2 message testing."""
    nodes = [
        RouteNode(entity_id="n1", name="HQ", node_type="base"),
        RouteNode(entity_id="n2", name="FOB", node_type="base"),
    ]
    if extra_nodes:
        nodes.extend(extra_nodes)

    edges = [RouteEdge(source="HQ", destination="FOB", distance_nm=100.0)]
    if extra_edges:
        edges.extend(extra_edges)

    return MissionConfiguration(
        mission_id="test-mission",
        mission_name="Test Mission",
        route_graph=RouteGraphConfig(
            nodes=nodes,
            edges=edges,
            origin="HQ",
            destination="FOB",
        ),
        aircraft=AircraftConfig(
            entity_id="ac1",
            aircraft_type="C-17",
            lift_to_drag_ratio=15.0,
            specific_fuel_consumption=0.6,
            initial_fuel_weight=50000.0,
            max_fuel_capacity=60000.0,
        ),
        c2_messages=c2_messages,
        cross_domain_gateway=cross_domain_gateway,
        active_modules=["c2_message"],
    )


class TestC2MessageModuleLifecycle:
    """Tests for module lifecycle methods."""

    def test_module_type_is_c2_message(self):
        module = C2MessageModule()
        assert module.module_type == "c2_message"

    def test_dependencies_contains_cross_domain(self):
        module = C2MessageModule()
        assert "cross_domain" in module.dependencies

    def test_initialize_with_no_messages(self):
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(c2_messages=None)

        module.initialize(env, event_bus, config)
        assert module._message_types == []
        assert module._message_log == []

    def test_initialize_stores_message_types(self):
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=2.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        assert len(module._message_types) == 1
        assert module._message_types[0].name == "ATO"

    def test_create_processes_returns_empty_when_no_messages(self):
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(c2_messages=None)

        module.initialize(env, event_bus, config)
        processes = module.create_processes(env)
        assert processes == []

    def test_create_processes_returns_one_per_message_type(self):
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=2.0,
            ),
            C2MessageType(
                entity_id="m2",
                name="ACO",
                source_node="FOB",
                destination_node="HQ",
                priority=2,
                transmission_latency=1.0,
            ),
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        processes = module.create_processes(env)
        assert len(processes) == 2

    def test_finalize_clears_state(self):
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=2.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.finalize()

        assert module._env is None
        assert module._event_bus is None
        assert module._message_types == []
        assert module._route_graph_nodes == set()


class TestMessageDelivery:
    """Tests for successful message delivery."""

    def test_message_delivered_to_reachable_destination(self):
        """Message to a node in route graph is delivered successfully."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=5.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(module.message_log) == 1
        instance = module.message_log[0]
        assert instance.status == "delivered"
        assert instance.message_type == "ATO"

    def test_delivery_timing_equals_generation_plus_latency(self):
        """delivery_time = generation_time + transmission_latency."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=3.5,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        instance = module.message_log[0]
        assert instance.generation_time == 0.0
        assert instance.transmission_start_time == 0.0
        assert instance.delivery_time == 3.5

    def test_timestamps_satisfy_ordering_invariant(self):
        """generation_time <= transmission_start_time <= delivery_time."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=10.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        instance = module.message_log[0]
        assert instance.generation_time <= instance.transmission_start_time
        assert instance.transmission_start_time <= instance.delivery_time

    def test_zero_latency_message_delivered_immediately(self):
        """Message with zero transmission_latency delivers at generation_time."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="FLASH",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=0.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        instance = module.message_log[0]
        assert instance.delivery_time == 0.0
        assert instance.status == "delivered"

    def test_multiple_messages_all_delivered(self):
        """Multiple message types are each processed and delivered."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=2.0,
            ),
            C2MessageType(
                entity_id="m2",
                name="ACO",
                source_node="FOB",
                destination_node="HQ",
                priority=2,
                transmission_latency=3.0,
            ),
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(module.message_log) == 2
        types = {m.message_type for m in module.message_log}
        assert types == {"ATO", "ACO"}
        assert all(m.status == "delivered" for m in module.message_log)


class TestMessageFailure:
    """Tests for unreachable destination handling."""

    def test_unreachable_destination_publishes_failure_event(self):
        """Message to non-existent destination publishes MessageDeliveryFailureEvent."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        failure_events = []

        event_bus.subscribe(
            MessageDeliveryFailureEvent, lambda e: failure_events.append(e), "test"
        )

        messages = [
            C2MessageType(
                entity_id="m1",
                name="OPREP3",
                source_node="HQ",
                destination_node="UNKNOWN_NODE",
                priority=1,
                transmission_latency=2.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(failure_events) == 1
        event = failure_events[0]
        assert event.message_type == "OPREP3"
        assert event.source == "HQ"
        assert event.intended_destination == "UNKNOWN_NODE"
        assert event.generation_time == 0.0

    def test_failed_message_retains_timestamps(self):
        """Failed message retains generation_time and transmission_start_time."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        messages = [
            C2MessageType(
                entity_id="m1",
                name="OPREP3",
                source_node="HQ",
                destination_node="NOWHERE",
                priority=1,
                transmission_latency=2.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(module.message_log) == 1
        instance = module.message_log[0]
        assert instance.status == "failed"
        assert instance.generation_time == 0.0
        assert instance.transmission_start_time == 0.0
        assert instance.delivery_time is None

    def test_failure_event_source_module_is_c2_message(self):
        """Failure event source_module is 'c2_message'."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        failure_events = []

        event_bus.subscribe(
            MessageDeliveryFailureEvent, lambda e: failure_events.append(e), "test"
        )

        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="NONEXISTENT",
                priority=1,
                transmission_latency=1.0,
            )
        ]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert failure_events[0].source_module == "c2_message"


class TestReachability:
    """Tests for destination reachability logic."""

    def test_route_graph_node_is_reachable(self):
        """Nodes in the route graph are reachable."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        msg = C2MessageType(
            entity_id="m1",
            name="ATO",
            source_node="HQ",
            destination_node="HQ",
            priority=1,
            transmission_latency=1.0,
        )
        messages = [msg]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        assert module._is_reachable("HQ", msg)
        assert module._is_reachable("FOB", msg)

    def test_message_source_destination_nodes_of_other_messages_are_reachable(self):
        """Source and destination nodes of other configured messages are reachable."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        msg1 = C2MessageType(
            entity_id="m1",
            name="ATO",
            source_node="CUSTOM_SRC",
            destination_node="CUSTOM_DST",
            priority=1,
            transmission_latency=1.0,
        )
        msg2 = C2MessageType(
            entity_id="m2",
            name="ACO",
            source_node="HQ",
            destination_node="CUSTOM_SRC",
            priority=1,
            transmission_latency=1.0,
        )
        messages = [msg1, msg2]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        # CUSTOM_SRC is reachable for msg2 because msg1 has it as source_node
        assert module._is_reachable("CUSTOM_SRC", msg2)
        # CUSTOM_DST is reachable for msg2 because msg1 has it as destination_node
        assert module._is_reachable("CUSTOM_DST", msg2)

    def test_unknown_node_is_not_reachable(self):
        """Nodes not in route graph or other message configs are unreachable."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        msg = C2MessageType(
            entity_id="m1",
            name="ATO",
            source_node="HQ",
            destination_node="FOB",
            priority=1,
            transmission_latency=1.0,
        )
        messages = [msg]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        assert not module._is_reachable("NONEXISTENT", msg)

    def test_own_destination_does_not_make_self_reachable(self):
        """A message's own destination_node does not make it reachable (no self-reference)."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()
        msg = C2MessageType(
            entity_id="m1",
            name="OPREP3",
            source_node="HQ",
            destination_node="UNKNOWN_NODE",
            priority=1,
            transmission_latency=2.0,
        )
        messages = [msg]
        config = _make_mission_config(c2_messages=messages)

        module.initialize(env, event_bus, config)
        assert not module._is_reachable("UNKNOWN_NODE", msg)


class TestCrossDomainTraversal:
    """Tests for cross-domain gateway integration."""

    def test_cross_domain_adds_sanitization_latency(self):
        """Message with classification adds sanitization_latency to delivery time."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()

        gateway_config = CrossDomainGatewayConfig(
            sanitization_latency=5.0,
            enclaves=[
                SecurityEnclave(entity_id="e1", name="SIPRNET", classification_level="SECRET"),
                SecurityEnclave(entity_id="e2", name="CENTRIXS", classification_level="SECRET"),
            ],
            routing_rules=[
                RoutingRule(
                    source_enclave="SIPRNET",
                    destination_enclave="CENTRIXS",
                    permitted_classifications=["SECRET"],
                )
            ],
        )

        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=3.0,
                classification="SECRET",
            )
        ]
        config = _make_mission_config(
            c2_messages=messages, cross_domain_gateway=gateway_config
        )

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        instance = module.message_log[0]
        assert instance.status == "delivered"
        # delivery_time = transmission_latency + sanitization_latency = 3.0 + 5.0 = 8.0
        assert instance.delivery_time == 8.0

    def test_no_gateway_config_no_extra_latency(self):
        """Without gateway config, no sanitization latency is added."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()

        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=3.0,
                classification="SECRET",
            )
        ]
        config = _make_mission_config(c2_messages=messages, cross_domain_gateway=None)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        instance = module.message_log[0]
        assert instance.delivery_time == 3.0

    def test_no_classification_no_gateway_traversal(self):
        """Message without classification does not traverse gateway."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()

        gateway_config = CrossDomainGatewayConfig(
            sanitization_latency=5.0,
            enclaves=[
                SecurityEnclave(entity_id="e1", name="SIPRNET", classification_level="SECRET"),
                SecurityEnclave(entity_id="e2", name="CENTRIXS", classification_level="SECRET"),
            ],
            routing_rules=[],
        )

        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=3.0,
                # No classification set (defaults to None)
            )
        ]
        config = _make_mission_config(
            c2_messages=messages, cross_domain_gateway=gateway_config
        )

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        instance = module.message_log[0]
        # No gateway traversal, delivery_time = transmission_latency only
        assert instance.delivery_time == 3.0

    def test_cross_domain_zero_sanitization_latency(self):
        """Gateway with zero sanitization_latency adds nothing."""
        module = C2MessageModule()
        env = simpy.Environment()
        event_bus = EventBus()

        gateway_config = CrossDomainGatewayConfig(
            sanitization_latency=0.0,
            enclaves=[
                SecurityEnclave(entity_id="e1", name="SIPRNET", classification_level="SECRET"),
                SecurityEnclave(entity_id="e2", name="CENTRIXS", classification_level="SECRET"),
            ],
            routing_rules=[],
        )

        messages = [
            C2MessageType(
                entity_id="m1",
                name="ATO",
                source_node="HQ",
                destination_node="FOB",
                priority=1,
                transmission_latency=4.0,
                classification="SECRET",
            )
        ]
        config = _make_mission_config(
            c2_messages=messages, cross_domain_gateway=gateway_config
        )

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        instance = module.message_log[0]
        assert instance.delivery_time == 4.0
