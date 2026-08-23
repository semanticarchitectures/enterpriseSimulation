"""Integration tests for cross-module communication wiring.

Verifies that event subscriptions are correctly established during initialization
and that modules communicate through the Event Bus as expected:
1. Aerodynamics → Fiscal: FuelConsumedEvent triggers fiscal debit
2. C2 Message → CrossDomainGateway: Gateway reference obtained during init
3. Airdrop → Aerodynamics: Airdrop triggers on route graph progression to drop zone

Requirements: 6.5, 8.1, 11.2
"""

import simpy
import pytest

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.engine.module_registry import ModuleRegistry
from enterprise_sim.engine.simulation_engine import SimulationEngine
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase, CargoItem
from enterprise_sim.models.events import (
    ExtractionCompleteEvent,
    ExtractionStartEvent,
    FuelConsumedEvent,
    FundsExhaustedEvent,
    LowBalanceWarningEvent,
    SimulationEvent,
)
from enterprise_sim.models.fiscal import FiscalAccount, FiscalConfig
from enterprise_sim.models.messaging import C2MessageType
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.models.security import (
    CrossDomainGatewayConfig,
    RoutingRule,
    SecurityEnclave,
)
from enterprise_sim.modules.aerodynamics import AerodynamicsModule
from enterprise_sim.modules.airdrop import AirdropModule
from enterprise_sim.modules.c2_message import C2MessageModule
from enterprise_sim.modules.cross_domain import CrossDomainGatewayModule
from enterprise_sim.modules.fiscal import FiscalModule


def _make_basic_route_graph():
    """Create a basic route graph with origin, waypoint, and destination."""
    return RouteGraphConfig(
        nodes=[
            RouteNode(
                entity_id="n1", name="base", node_type="base",
                latitude=0.0, longitude=0.0
            ),
            RouteNode(
                entity_id="n2", name="waypoint_1", node_type="waypoint",
                latitude=1.0, longitude=1.0
            ),
            RouteNode(
                entity_id="n3", name="dest", node_type="base",
                latitude=2.0, longitude=2.0
            ),
        ],
        edges=[
            RouteEdge(source="base", destination="waypoint_1", distance_nm=100.0),
            RouteEdge(source="waypoint_1", destination="dest", distance_nm=150.0),
        ],
        origin="base",
        destination="dest",
    )


def _make_route_graph_with_drop_zone():
    """Create a route graph that includes a drop zone node."""
    return RouteGraphConfig(
        nodes=[
            RouteNode(
                entity_id="n1", name="base", node_type="base",
                latitude=0.0, longitude=0.0
            ),
            RouteNode(
                entity_id="n2", name="drop_zone_alpha", node_type="drop_zone",
                latitude=1.0, longitude=1.0
            ),
            RouteNode(
                entity_id="n3", name="dest", node_type="base",
                latitude=2.0, longitude=2.0
            ),
        ],
        edges=[
            RouteEdge(source="base", destination="drop_zone_alpha", distance_nm=100.0),
            RouteEdge(source="drop_zone_alpha", destination="dest", distance_nm=80.0),
        ],
        origin="base",
        destination="dest",
    )


def _make_aircraft_config():
    """Create standard aircraft config for testing."""
    return AircraftConfig(
        entity_id="ac1",
        aircraft_type="C-130",
        lift_to_drag_ratio=10.0,
        specific_fuel_consumption=0.5,
        initial_fuel_weight=10000.0,
        max_fuel_capacity=12000.0,
    )


def _make_fiscal_config():
    """Create a fiscal config with one account and rates."""
    return FiscalConfig(
        accounts=[
            FiscalAccount(
                entity_id="fa1",
                account_id="ops_fund",
                initial_allocation=100000.0,
                current_balance=100000.0,
                warning_threshold_pct=0.1,
            )
        ],
        cost_per_flying_hour=500.0,
        cost_per_fuel_unit=2.0,
    )


class TestAerodynamicsFiscalWiring:
    """Test that FuelConsumedEvent from Aerodynamics triggers Fiscal debit."""

    def test_fuel_consumed_triggers_fiscal_debit(self):
        """When aerodynamics publishes FuelConsumedEvent, fiscal debits fuel cost."""
        env = simpy.Environment()
        event_bus = EventBus()

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="Aero-Fiscal Wiring Test",
            route_graph=_make_basic_route_graph(),
            aircraft=_make_aircraft_config(),
            fiscal_parameters=_make_fiscal_config(),
            active_modules=["fiscal", "aerodynamics"],
        )

        # Initialize fiscal module (subscribes to FuelConsumedEvent)
        fiscal = FiscalModule()
        fiscal.initialize(env, event_bus, config)

        # Initialize aerodynamics module
        aero = AerodynamicsModule()
        aero.initialize(env, event_bus, config)

        # Run the aerodynamics process
        processes = aero.create_processes(env)
        assert len(processes) > 0
        env.run()

        # Verify that the fiscal module received debits for fuel
        assert fiscal.cumulative_debits["ops_fund"] > 0
        records = fiscal.debit_records["ops_fund"]
        assert len(records) > 0
        assert all(r.cost_category == "fuel" for r in records)

        # Verify the debit amount matches expected fuel cost
        # Each leg fuel burn = distance * sfc / L/D = distance * 0.5 / 10 = distance * 0.05
        # Leg 1: 100nm → 5.0 fuel units → cost = 5.0 * 2.0 = 10.0
        # Leg 2: 150nm → 7.5 fuel units → cost = 7.5 * 2.0 = 15.0
        expected_total_cost = (100.0 * 0.05 * 2.0) + (150.0 * 0.05 * 2.0)
        assert abs(fiscal.cumulative_debits["ops_fund"] - expected_total_cost) < 1e-9

    def test_fiscal_debit_respects_balance(self):
        """Fiscal module rejects debit when FuelConsumedEvent would overdraft."""
        env = simpy.Environment()
        event_bus = EventBus()

        # Set up with very low balance
        fiscal_config = FiscalConfig(
            accounts=[
                FiscalAccount(
                    entity_id="fa1",
                    account_id="ops_fund",
                    initial_allocation=1.0,
                    current_balance=1.0,
                    warning_threshold_pct=0.5,
                )
            ],
            cost_per_flying_hour=500.0,
            cost_per_fuel_unit=2.0,
        )

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="Overdraft Test",
            route_graph=_make_basic_route_graph(),
            aircraft=_make_aircraft_config(),
            fiscal_parameters=fiscal_config,
            active_modules=["fiscal", "aerodynamics"],
        )

        # Track events
        events_received: list[SimulationEvent] = []
        event_bus.subscribe(
            FundsExhaustedEvent,
            lambda e: events_received.append(e),
            subscriber_id="test_collector",
        )

        fiscal = FiscalModule()
        fiscal.initialize(env, event_bus, config)

        aero = AerodynamicsModule()
        aero.initialize(env, event_bus, config)

        aero.create_processes(env)
        env.run()

        # The first fuel cost (100nm * 0.05 * 2.0 = 10.0) exceeds balance of 1.0
        assert len(events_received) > 0
        assert events_received[0].account_id == "ops_fund"


class TestC2MessageGatewayWiring:
    """Test that C2MessageModule obtains CrossDomainGateway reference."""

    def test_c2_module_receives_gateway_reference_via_engine(self):
        """SimulationEngine wires gateway reference to C2 module after init."""
        event_bus = EventBus()
        registry = ModuleRegistry()

        # Register modules
        c2 = C2MessageModule()
        gateway = CrossDomainGatewayModule()
        fiscal = FiscalModule()

        registry.register(fiscal)
        registry.register(gateway)
        registry.register(c2)

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="C2-Gateway Wiring Test",
            route_graph=_make_basic_route_graph(),
            aircraft=_make_aircraft_config(),
            cross_domain_gateway=CrossDomainGatewayConfig(
                sanitization_latency=5.0,
                enclaves=[
                    SecurityEnclave(
                        entity_id="e1", name="SIPRNET",
                        classification_level="SECRET"
                    ),
                    SecurityEnclave(
                        entity_id="e2", name="CENTRIXS",
                        classification_level="SECRET"
                    ),
                ],
                routing_rules=[
                    RoutingRule(
                        source_enclave="SIPRNET",
                        destination_enclave="CENTRIXS",
                        permitted_classifications=["SECRET"],
                    )
                ],
            ),
            c2_messages=[
                C2MessageType(
                    entity_id="mt1",
                    name="ATO",
                    source_node="base",
                    destination_node="dest",
                    priority=1,
                    transmission_latency=2.0,
                    classification="SECRET",
                ),
            ],
            active_modules=["fiscal", "cross_domain", "c2_message"],
        )

        # Verify gateway wiring by manually initializing and wiring
        env = simpy.Environment()
        fiscal.initialize(env, event_bus, config)
        gateway.initialize(env, event_bus, config)
        c2.initialize(env, event_bus, config)

        # Before wiring — no gateway
        assert c2._gateway is None

        # Simulate what the engine does post-initialization
        engine = SimulationEngine(registry, event_bus)
        engine._wire_module_dependencies([fiscal, gateway, c2])

        # After wiring — gateway reference set
        assert c2._gateway is gateway

    def test_c2_module_gateway_none_when_not_registered(self):
        """C2 module gateway remains None when cross_domain is not active."""
        env = simpy.Environment()
        event_bus = EventBus()

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="C2 No Gateway Test",
            route_graph=_make_basic_route_graph(),
            aircraft=_make_aircraft_config(),
            c2_messages=[
                C2MessageType(
                    entity_id="mt1",
                    name="ATO",
                    source_node="base",
                    destination_node="dest",
                    priority=1,
                    transmission_latency=2.0,
                ),
            ],
            active_modules=["c2_message"],
        )

        c2 = C2MessageModule()
        c2.initialize(env, event_bus, config)
        assert c2._gateway is None


class TestAirdropRouteProgressionWiring:
    """Test that Airdrop triggers based on route graph progression."""

    def test_airdrop_triggers_on_drop_zone_arrival(self):
        """Airdrop state transition starts when aircraft reaches drop zone."""
        env = simpy.Environment()
        event_bus = EventBus()

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="Airdrop Trigger Test",
            route_graph=_make_route_graph_with_drop_zone(),
            aircraft=_make_aircraft_config(),
            fiscal_parameters=_make_fiscal_config(),
            airdrop_parameters=AirdropConfig(
                extraction_speed=130.0,
                drop_altitude_ft=1000.0,
                cargo_weight_limit=5000.0,
                phase_durations={
                    AirdropPhase.SLOWDOWN: 5.0,
                    AirdropPhase.EXTRACTION: 10.0,
                    AirdropPhase.ACCELERATION: 5.0,
                },
                cargo_manifest=[
                    CargoItem(item_id="cargo_1", weight=2000.0, description="Supplies"),
                ],
            ),
            active_modules=["fiscal", "aerodynamics", "airdrop"],
        )

        # Track extraction events
        extraction_starts: list[SimulationEvent] = []
        extraction_completes: list[SimulationEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: extraction_starts.append(e),
            subscriber_id="test_start",
        )
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: extraction_completes.append(e),
            subscriber_id="test_complete",
        )

        # Initialize modules in dependency order
        fiscal = FiscalModule()
        fiscal.initialize(env, event_bus, config)

        aero = AerodynamicsModule()
        aero.initialize(env, event_bus, config)

        airdrop = AirdropModule()
        airdrop.initialize(env, event_bus, config)

        # Create and run processes
        all_processes = []
        all_processes.extend(fiscal.create_processes(env))
        all_processes.extend(aero.create_processes(env))
        all_processes.extend(airdrop.create_processes(env))

        env.run()

        # Airdrop should have triggered after the first leg (base->drop_zone_alpha)
        assert len(extraction_starts) == 1
        assert extraction_starts[0].drop_zone_id == "drop_zone_alpha"
        assert len(extraction_completes) == 1
        assert extraction_completes[0].cargo_manifest == ["cargo_1"]

        # Airdrop should have happened after the first leg flight time (100nm)
        # First leg takes 100 time units, then airdrop phases run
        assert extraction_starts[0].timestamp >= 100.0

    def test_airdrop_no_trigger_without_drop_zone(self):
        """Airdrop runs immediately if no drop_zone nodes in route graph."""
        env = simpy.Environment()
        event_bus = EventBus()

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="Airdrop No DZ Test",
            route_graph=_make_basic_route_graph(),  # No drop_zone nodes
            aircraft=_make_aircraft_config(),
            airdrop_parameters=AirdropConfig(
                extraction_speed=130.0,
                drop_altitude_ft=1000.0,
                cargo_weight_limit=5000.0,
                phase_durations={
                    AirdropPhase.SLOWDOWN: 5.0,
                    AirdropPhase.EXTRACTION: 10.0,
                    AirdropPhase.ACCELERATION: 5.0,
                },
                cargo_manifest=[
                    CargoItem(item_id="cargo_1", weight=2000.0, description="Supplies"),
                ],
            ),
            active_modules=["airdrop"],
        )

        extraction_starts: list[SimulationEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: extraction_starts.append(e),
            subscriber_id="test_start",
        )

        airdrop = AirdropModule()
        airdrop.initialize(env, event_bus, config)

        airdrop.create_processes(env)
        env.run()

        # Should still run (immediate start when no drop zones configured)
        assert len(extraction_starts) == 1


class TestEventSubscriptionEstablishment:
    """Verify all event subscriptions are correctly established during init."""

    def test_fiscal_subscribes_to_fuel_consumed(self):
        """FiscalModule subscribes to FuelConsumedEvent during initialization."""
        env = simpy.Environment()
        event_bus = EventBus()

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="Subscription Test",
            route_graph=_make_basic_route_graph(),
            aircraft=_make_aircraft_config(),
            fiscal_parameters=_make_fiscal_config(),
            active_modules=["fiscal"],
        )

        fiscal = FiscalModule()
        fiscal.initialize(env, event_bus, config)

        # Verify subscription exists by publishing a FuelConsumedEvent
        # and checking that a debit occurs
        event_bus.publish(
            FuelConsumedEvent(
                entity_id="test_event",
                timestamp=0.0,
                source_module="aerodynamics",
                leg_id="test_leg",
                fuel_quantity=10.0,
            )
        )

        # Fuel cost = 10.0 * 2.0 (cost_per_fuel_unit) = 20.0
        assert fiscal.cumulative_debits["ops_fund"] == 20.0

    def test_airdrop_subscribes_to_fuel_consumed_when_drop_zones_exist(self):
        """AirdropModule subscribes to FuelConsumedEvent when drop zones configured."""
        env = simpy.Environment()
        event_bus = EventBus()

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="Airdrop Subscription Test",
            route_graph=_make_route_graph_with_drop_zone(),
            aircraft=_make_aircraft_config(),
            airdrop_parameters=AirdropConfig(
                extraction_speed=130.0,
                drop_altitude_ft=1000.0,
                cargo_weight_limit=5000.0,
                phase_durations={
                    AirdropPhase.SLOWDOWN: 5.0,
                    AirdropPhase.EXTRACTION: 10.0,
                    AirdropPhase.ACCELERATION: 5.0,
                },
                cargo_manifest=[
                    CargoItem(item_id="cargo_1", weight=2000.0, description="Supplies"),
                ],
            ),
            active_modules=["aerodynamics", "airdrop"],
        )

        airdrop = AirdropModule()
        airdrop.initialize(env, event_bus, config)

        # Verify subscription: publish FuelConsumedEvent with drop zone destination
        event_bus.publish(
            FuelConsumedEvent(
                entity_id="test_event",
                timestamp=100.0,
                source_module="aerodynamics",
                leg_id="base->drop_zone_alpha",
                fuel_quantity=5.0,
            )
        )

        # The trigger event should have been fired
        assert airdrop._airdrop_triggered is True
        assert airdrop._trigger_event.triggered is True

    def test_full_engine_establishes_all_subscriptions(self):
        """Full simulation engine run establishes all expected subscriptions."""
        event_bus = EventBus()
        registry = ModuleRegistry()

        registry.register(FiscalModule())
        registry.register(CrossDomainGatewayModule())
        registry.register(C2MessageModule())
        registry.register(AerodynamicsModule())
        registry.register(AirdropModule())

        config = MissionConfiguration(
            mission_id="test_mission",
            mission_name="Full Wiring Test",
            route_graph=_make_route_graph_with_drop_zone(),
            aircraft=_make_aircraft_config(),
            fiscal_parameters=_make_fiscal_config(),
            cross_domain_gateway=CrossDomainGatewayConfig(
                sanitization_latency=3.0,
                enclaves=[
                    SecurityEnclave(
                        entity_id="e1", name="SIPRNET",
                        classification_level="SECRET"
                    ),
                    SecurityEnclave(
                        entity_id="e2", name="CENTRIXS",
                        classification_level="SECRET"
                    ),
                ],
                routing_rules=[
                    RoutingRule(
                        source_enclave="SIPRNET",
                        destination_enclave="CENTRIXS",
                        permitted_classifications=["SECRET"],
                    )
                ],
            ),
            airdrop_parameters=AirdropConfig(
                extraction_speed=130.0,
                drop_altitude_ft=1000.0,
                cargo_weight_limit=5000.0,
                phase_durations={
                    AirdropPhase.SLOWDOWN: 5.0,
                    AirdropPhase.EXTRACTION: 10.0,
                    AirdropPhase.ACCELERATION: 5.0,
                },
                cargo_manifest=[
                    CargoItem(item_id="cargo_1", weight=2000.0, description="Supplies"),
                ],
            ),
            active_modules=[
                "fiscal", "cross_domain", "c2_message", "aerodynamics", "airdrop"
            ],
        )

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)
        assert result.success
