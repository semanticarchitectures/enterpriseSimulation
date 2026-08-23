"""Unit tests for CrossDomainGatewayModule.

Tests cover:
- Initialization with and without gateway config
- Routing rule enforcement (permit/deny)
- process_message SimPy generator behavior
- Event publishing for violations and sanitization complete
- Finalize cleanup
"""

import simpy
import pytest

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import (
    ClassificationViolationEvent,
    SanitizationCompleteEvent,
)
from enterprise_sim.models.security import (
    CrossDomainGatewayConfig,
    RoutingRule,
    SecurityEnclave,
)
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteGraphConfig, RouteNode, RouteEdge
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.modules.cross_domain import CrossDomainGatewayModule


def _make_minimal_mission_config(
    gateway_config: CrossDomainGatewayConfig | None = None,
) -> MissionConfiguration:
    """Create a minimal MissionConfiguration with optional gateway config."""
    return MissionConfiguration(
        mission_id="test-mission",
        mission_name="Test Mission",
        route_graph=RouteGraphConfig(
            nodes=[
                RouteNode(entity_id="n1", name="Alpha", node_type="base"),
                RouteNode(entity_id="n2", name="Bravo", node_type="waypoint"),
            ],
            edges=[RouteEdge(source="Alpha", destination="Bravo", distance_nm=100.0)],
            origin="Alpha",
            destination="Bravo",
        ),
        aircraft=AircraftConfig(
            entity_id="ac1",
            aircraft_type="C-17",
            lift_to_drag_ratio=18.0,
            specific_fuel_consumption=0.6,
            initial_fuel_weight=50000.0,
            max_fuel_capacity=60000.0,
        ),
        cross_domain_gateway=gateway_config,
        active_modules=["cross_domain"],
    )


def _make_gateway_config() -> CrossDomainGatewayConfig:
    """Create a standard test gateway configuration."""
    return CrossDomainGatewayConfig(
        sanitization_latency=2.5,
        enclaves=[
            SecurityEnclave(
                entity_id="enc1", name="SIPRNET", classification_level="SECRET"
            ),
            SecurityEnclave(
                entity_id="enc2", name="NIPRNET", classification_level="UNCLASSIFIED"
            ),
        ],
        routing_rules=[
            RoutingRule(
                source_enclave="SIPRNET",
                destination_enclave="NIPRNET",
                permitted_classifications=["UNCLASSIFIED"],
            ),
            RoutingRule(
                source_enclave="NIPRNET",
                destination_enclave="SIPRNET",
                permitted_classifications=["UNCLASSIFIED", "SECRET"],
            ),
        ],
    )


class TestCrossDomainGatewayModuleMetadata:
    """Test module class-level metadata."""

    def test_module_type(self):
        module = CrossDomainGatewayModule()
        assert module.module_type == "cross_domain"

    def test_dependencies_empty(self):
        module = CrossDomainGatewayModule()
        assert module.dependencies == set()


class TestCrossDomainGatewayInitialize:
    """Test initialization behavior."""

    def test_initialize_with_gateway_config(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())

        module.initialize(env, event_bus, config)

        assert module._sanitization_latency == 2.5
        assert "SIPRNET" in module._enclaves
        assert "NIPRNET" in module._enclaves
        assert ("SIPRNET", "NIPRNET") in module._routing_rules
        assert ("NIPRNET", "SIPRNET") in module._routing_rules

    def test_initialize_without_gateway_config(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(None)

        module.initialize(env, event_bus, config)

        assert module._sanitization_latency == 0.0
        assert module._enclaves == {}
        assert module._routing_rules == {}

    def test_routing_rules_stored_as_sets(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())

        module.initialize(env, event_bus, config)

        # Verify the lookup is a set for O(1) membership testing
        permitted = module._routing_rules[("NIPRNET", "SIPRNET")]
        assert isinstance(permitted, set)
        assert permitted == {"UNCLASSIFIED", "SECRET"}


class TestCheckRouting:
    """Test check_routing method."""

    def setup_method(self):
        self.module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        self.module.initialize(env, event_bus, config)

    def test_permitted_classification(self):
        assert self.module.check_routing("UNCLASSIFIED", "SIPRNET", "NIPRNET") is True

    def test_denied_classification(self):
        # SECRET is not permitted from SIPRNET → NIPRNET
        assert self.module.check_routing("SECRET", "SIPRNET", "NIPRNET") is False

    def test_reverse_direction_permitted(self):
        # SECRET is permitted from NIPRNET → SIPRNET
        assert self.module.check_routing("SECRET", "NIPRNET", "SIPRNET") is True

    def test_no_rule_for_pair(self):
        # No rule exists for this pair
        assert self.module.check_routing("SECRET", "UNKNOWN", "NIPRNET") is False

    def test_multiple_permitted_classifications(self):
        # NIPRNET → SIPRNET permits both UNCLASSIFIED and SECRET
        assert (
            self.module.check_routing("UNCLASSIFIED", "NIPRNET", "SIPRNET") is True
        )
        assert self.module.check_routing("SECRET", "NIPRNET", "SIPRNET") is True
        assert (
            self.module.check_routing("TOP_SECRET", "NIPRNET", "SIPRNET") is False
        )


class TestProcessMessage:
    """Test process_message SimPy generator behavior."""

    def test_permitted_message_returns_latency(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        module.initialize(env, event_bus, config)

        result_holder = []

        def run_process():
            result = yield from module.process_message(
                env, "msg-001", "UNCLASSIFIED", "SIPRNET", "NIPRNET"
            )
            result_holder.append(result)

        env.process(run_process())
        env.run()

        assert result_holder[0] == 2.5
        assert env.now == 2.5

    def test_denied_message_returns_none(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        module.initialize(env, event_bus, config)

        result_holder = []

        def run_process():
            result = yield from module.process_message(
                env, "msg-002", "SECRET", "SIPRNET", "NIPRNET"
            )
            result_holder.append(result)

        env.process(run_process())
        env.run()

        assert result_holder[0] is None
        # No time should have elapsed for denied messages
        assert env.now == 0

    def test_denied_message_publishes_violation_event(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        module.initialize(env, event_bus, config)

        received_events = []
        event_bus.subscribe(
            ClassificationViolationEvent,
            lambda e: received_events.append(e),
            "test-subscriber",
        )

        def run_process():
            yield from module.process_message(
                env, "msg-003", "TOP_SECRET", "SIPRNET", "NIPRNET"
            )

        env.process(run_process())
        env.run()

        assert len(received_events) == 1
        evt = received_events[0]
        assert evt.message_id == "msg-003"
        assert evt.source_enclave == "SIPRNET"
        assert evt.destination_enclave == "NIPRNET"
        assert evt.message_classification == "TOP_SECRET"
        assert evt.source_module == "cross_domain"

    def test_permitted_message_publishes_sanitization_event(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        module.initialize(env, event_bus, config)

        received_events = []
        event_bus.subscribe(
            SanitizationCompleteEvent,
            lambda e: received_events.append(e),
            "test-subscriber",
        )

        def run_process():
            yield from module.process_message(
                env, "msg-004", "UNCLASSIFIED", "SIPRNET", "NIPRNET"
            )

        env.process(run_process())
        env.run()

        assert len(received_events) == 1
        evt = received_events[0]
        assert evt.sanitization_duration == 2.5
        assert evt.source_enclave == "SIPRNET"
        assert evt.destination_enclave == "NIPRNET"
        assert evt.source_module == "cross_domain"

    def test_sanitization_event_timestamp_reflects_delay(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        module.initialize(env, event_bus, config)

        received_events = []
        event_bus.subscribe(
            SanitizationCompleteEvent,
            lambda e: received_events.append(e),
            "test-subscriber",
        )

        def run_process():
            yield from module.process_message(
                env, "msg-005", "UNCLASSIFIED", "SIPRNET", "NIPRNET"
            )

        env.process(run_process())
        env.run()

        assert received_events[0].timestamp == 2.5

    def test_zero_sanitization_latency(self):
        """When sanitization_latency is 0, message passes instantly."""
        gateway_config = CrossDomainGatewayConfig(
            sanitization_latency=0.0,
            enclaves=[
                SecurityEnclave(
                    entity_id="enc1", name="A", classification_level="SECRET"
                ),
                SecurityEnclave(
                    entity_id="enc2", name="B", classification_level="UNCLASSIFIED"
                ),
            ],
            routing_rules=[
                RoutingRule(
                    source_enclave="A",
                    destination_enclave="B",
                    permitted_classifications=["UNCLASSIFIED"],
                ),
            ],
        )
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(gateway_config)
        module.initialize(env, event_bus, config)

        result_holder = []

        def run_process():
            result = yield from module.process_message(
                env, "msg-006", "UNCLASSIFIED", "A", "B"
            )
            result_holder.append(result)

        env.process(run_process())
        env.run()

        assert result_holder[0] == 0.0
        assert env.now == 0.0


class TestCreateProcesses:
    """Test create_processes returns empty list."""

    def test_returns_empty_list(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        module.initialize(env, event_bus, config)

        processes = module.create_processes(env)
        assert processes == []


class TestFinalize:
    """Test finalize cleanup."""

    def test_finalize_cleans_up_state(self):
        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_minimal_mission_config(_make_gateway_config())
        module.initialize(env, event_bus, config)

        # Verify state is populated
        assert len(module._enclaves) > 0
        assert len(module._routing_rules) > 0

        module.finalize()

        assert module._enclaves == {}
        assert module._routing_rules == {}
        assert module._sanitization_latency == 0.0
