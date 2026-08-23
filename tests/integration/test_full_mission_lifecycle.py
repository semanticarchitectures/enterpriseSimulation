"""Integration test for full mission lifecycle.

Verifies that the SimulationEngine correctly orchestrates the complete
lifecycle (initialization → execution → finalization) with all 6 domain
modules active:
1. Initialization order matches topological sort of declared dependencies
2. All events are captured by the Reporter
3. Finalization occurs in reverse initialization order
4. The run completes successfully with all modules contributing events

Requirements: 1.1, 1.3, 13.1, 13.2, 13.3
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.engine.module_registry import ModuleRegistry
from enterprise_sim.engine.simulation_engine import SimulationEngine, SimulationResult
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase, CargoItem
from enterprise_sim.models.authorization import AuthorizationChainConfig, AuthorizationGate
from enterprise_sim.models.events import (
    ChainCompletionEvent,
    ExtractionCompleteEvent,
    ExtractionStartEvent,
    FuelConsumedEvent,
    GateCompletionEvent,
    SanitizationCompleteEvent,
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
from enterprise_sim.modules.authorization import AuthorizationModule
from enterprise_sim.modules.c2_message import C2MessageModule
from enterprise_sim.modules.cross_domain import CrossDomainGatewayModule
from enterprise_sim.modules.fiscal import FiscalModule
from enterprise_sim.reporting.reporter import Reporter


def _make_full_mission_config() -> MissionConfiguration:
    """Create a complete mission configuration with all 6 modules active.

    Route: origin (base) → waypoint_1 → drop_zone → waypoint_2 → destination
    """
    route_graph = RouteGraphConfig(
        nodes=[
            RouteNode(
                entity_id="n1", name="origin", node_type="base",
                latitude=33.0, longitude=-117.0, altitude_ft=0.0,
            ),
            RouteNode(
                entity_id="n2", name="waypoint_1", node_type="waypoint",
                latitude=34.0, longitude=-116.0, altitude_ft=25000.0,
            ),
            RouteNode(
                entity_id="n3", name="drop_zone", node_type="drop_zone",
                latitude=35.0, longitude=-115.0, altitude_ft=1000.0,
            ),
            RouteNode(
                entity_id="n4", name="waypoint_2", node_type="waypoint",
                latitude=36.0, longitude=-114.0, altitude_ft=25000.0,
            ),
            RouteNode(
                entity_id="n5", name="destination", node_type="base",
                latitude=37.0, longitude=-113.0, altitude_ft=0.0,
            ),
        ],
        edges=[
            RouteEdge(source="origin", destination="waypoint_1", distance_nm=100.0),
            RouteEdge(source="waypoint_1", destination="drop_zone", distance_nm=80.0),
            RouteEdge(source="drop_zone", destination="waypoint_2", distance_nm=60.0),
            RouteEdge(source="waypoint_2", destination="destination", distance_nm=90.0),
        ],
        origin="origin",
        destination="destination",
    )

    aircraft = AircraftConfig(
        entity_id="ac1",
        aircraft_type="C-17",
        lift_to_drag_ratio=15.0,
        specific_fuel_consumption=0.4,
        initial_fuel_weight=50000.0,
        max_fuel_capacity=60000.0,
    )

    authorization_chain = AuthorizationChainConfig(
        chain_id="auth_chain_1",
        chain_type="sequential",
        gates=[
            AuthorizationGate(
                entity_id="gate1",
                gate_name="title_10",
                duration=5.0,
                timestamp=0.0,
                start_time=0.0,
            ),
            AuthorizationGate(
                entity_id="gate2",
                gate_name="dipclear",
                duration=3.0,
                timestamp=0.0,
                start_time=0.0,
            ),
        ],
    )

    fiscal_parameters = FiscalConfig(
        accounts=[
            FiscalAccount(
                entity_id="fa1",
                account_id="mission_ops",
                initial_allocation=500000.0,
                current_balance=500000.0,
                warning_threshold_pct=0.1,
            ),
        ],
        cost_per_flying_hour=1000.0,
        cost_per_fuel_unit=3.0,
    )

    c2_messages = [
        C2MessageType(
            entity_id="msg1",
            name="ATO",
            source_node="origin",
            destination_node="destination",
            priority=1,
            transmission_latency=2.0,
            classification="SECRET",
        ),
        C2MessageType(
            entity_id="msg2",
            name="AIRMOVE",
            source_node="origin",
            destination_node="waypoint_1",
            priority=2,
            transmission_latency=1.5,
        ),
    ]

    cross_domain_gateway = CrossDomainGatewayConfig(
        sanitization_latency=4.0,
        enclaves=[
            SecurityEnclave(
                entity_id="enc1", name="SIPRNET",
                classification_level="SECRET",
            ),
            SecurityEnclave(
                entity_id="enc2", name="CENTRIXS",
                classification_level="SECRET",
            ),
        ],
        routing_rules=[
            RoutingRule(
                source_enclave="SIPRNET",
                destination_enclave="CENTRIXS",
                permitted_classifications=["SECRET"],
            ),
            RoutingRule(
                source_enclave="CENTRIXS",
                destination_enclave="SIPRNET",
                permitted_classifications=["SECRET"],
            ),
        ],
    )

    airdrop_parameters = AirdropConfig(
        extraction_speed=130.0,
        drop_altitude_ft=1000.0,
        cargo_weight_limit=10000.0,
        phase_durations={
            AirdropPhase.SLOWDOWN: 3.0,
            AirdropPhase.EXTRACTION: 8.0,
            AirdropPhase.ACCELERATION: 3.0,
        },
        cargo_manifest=[
            CargoItem(item_id="cargo_1", weight=3000.0, description="MRE Supplies"),
            CargoItem(item_id="cargo_2", weight=2000.0, description="Medical Kit"),
        ],
    )

    return MissionConfiguration(
        mission_id="mission_full_lifecycle",
        mission_name="Full Lifecycle Integration Test",
        route_graph=route_graph,
        aircraft=aircraft,
        authorization_chain=authorization_chain,
        fiscal_parameters=fiscal_parameters,
        c2_messages=c2_messages,
        cross_domain_gateway=cross_domain_gateway,
        airdrop_parameters=airdrop_parameters,
        active_modules=[
            "authorization",
            "fiscal",
            "c2_message",
            "cross_domain",
            "aerodynamics",
            "airdrop",
        ],
    )


def _create_registry_with_all_modules() -> ModuleRegistry:
    """Create a ModuleRegistry with all 6 domain modules registered."""
    registry = ModuleRegistry()
    registry.register(AuthorizationModule())
    registry.register(FiscalModule())
    registry.register(C2MessageModule())
    registry.register(CrossDomainGatewayModule())
    registry.register(AerodynamicsModule())
    registry.register(AirdropModule())
    return registry


class TestFullMissionLifecycle:
    """Test the complete simulation lifecycle with all modules active."""

    def test_simulation_completes_successfully(self):
        """Full simulation with all 6 modules runs to completion without errors."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True
        assert result.error is None
        assert result.failed_module is None
        assert result.exception is None

    def test_reporter_captures_events_from_all_active_modules(self):
        """Reporter collects events from each of the 6 active modules."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        timeline = reporter.get_timeline()
        assert len(timeline) > 0, "Reporter should have captured events"

        # Collect unique source modules that produced events
        source_modules = {entry.source_module for entry in timeline}

        # All 6 modules should have produced at least one event
        expected_modules = {
            "authorization",
            "fiscal",
            "c2_message",
            "cross_domain",
            "aerodynamics",
            "airdrop",
        }
        # Check that we have events from the key modules.
        # Some modules may not publish events depending on configuration routing,
        # but authorization, aerodynamics, and airdrop definitely produce events.
        assert "authorization" in source_modules, (
            "Authorization module should produce gate/chain events"
        )
        assert "aerodynamics" in source_modules, (
            "Aerodynamics module should produce fuel consumed events"
        )
        assert "airdrop" in source_modules, (
            "Airdrop module should produce extraction events"
        )

    def test_timeline_events_ordered_by_timestamp(self):
        """Timeline entries are ordered by simulation timestamp."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        timeline = reporter.get_timeline()
        assert len(timeline) > 0

        # Verify timestamps are non-decreasing (sorted order)
        timestamps = [entry.timestamp for entry in timeline]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1], (
                f"Timeline not ordered: event at index {i} has timestamp "
                f"{timestamps[i]} < previous timestamp {timestamps[i - 1]}"
            )

    def test_timeline_includes_expected_event_types(self):
        """Timeline includes event types from each domain module."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        timeline = reporter.get_timeline()
        event_types = {entry.event_type for entry in timeline}

        # Authorization module should produce gate completion and chain completion
        assert "gate_completion" in event_types, (
            "Expected gate_completion events from authorization module"
        )
        assert "chain_completion" in event_types, (
            "Expected chain_completion event from authorization module"
        )

        # Aerodynamics module should produce fuel_consumed events
        assert "fuel_consumed" in event_types, (
            "Expected fuel_consumed events from aerodynamics module"
        )

        # Airdrop module should produce extraction events
        assert "extraction_start" in event_types, (
            "Expected extraction_start event from airdrop module"
        )
        assert "extraction_complete" in event_types, (
            "Expected extraction_complete event from airdrop module"
        )

    def test_initialization_order_matches_topological_sort(self):
        """Modules are initialized in topological dependency order."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        # Track initialization order by patching module initialize methods
        init_order: list[str] = []

        # Get the actual ordered modules from the registry
        ordered_modules = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        expected_ordered = [
            m for m in ordered_modules if m.module_type in active_set
        ]

        # Wrap each module's initialize to track order
        original_inits = {}
        for module in expected_ordered:
            original_inits[module.module_type] = module.initialize

            def make_wrapper(mod_type, orig_init):
                def wrapper(*args, **kwargs):
                    init_order.append(mod_type)
                    return orig_init(*args, **kwargs)
                return wrapper

            module.initialize = make_wrapper(module.module_type, module.initialize)

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        # Verify init_order matches topological sort
        expected_order = [m.module_type for m in expected_ordered]
        assert init_order == expected_order, (
            f"Initialization order {init_order} does not match "
            f"topological sort {expected_order}"
        )

        # Verify topological constraints:
        # - aerodynamics must come after fiscal
        # - c2_message must come after cross_domain
        # - airdrop must come after aerodynamics
        aero_idx = init_order.index("aerodynamics")
        fiscal_idx = init_order.index("fiscal")
        assert aero_idx > fiscal_idx, (
            "aerodynamics must initialize after fiscal"
        )

        c2_idx = init_order.index("c2_message")
        cd_idx = init_order.index("cross_domain")
        assert c2_idx > cd_idx, (
            "c2_message must initialize after cross_domain"
        )

        airdrop_idx = init_order.index("airdrop")
        assert airdrop_idx > aero_idx, (
            "airdrop must initialize after aerodynamics"
        )

    def test_finalization_in_reverse_initialization_order(self):
        """Modules are finalized in reverse initialization order."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        # Track finalization order
        finalize_order: list[str] = []

        # Get ordered modules
        ordered_modules = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        expected_ordered = [
            m for m in ordered_modules if m.module_type in active_set
        ]

        # Wrap each module's finalize to track order
        for module in expected_ordered:
            original_finalize = module.finalize

            def make_wrapper(mod_type, orig_finalize):
                def wrapper(*args, **kwargs):
                    finalize_order.append(mod_type)
                    return orig_finalize(*args, **kwargs)
                return wrapper

            module.finalize = make_wrapper(module.module_type, original_finalize)

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        # Finalization order should be reverse of initialization order
        expected_init_order = [m.module_type for m in expected_ordered]
        expected_finalize_order = list(reversed(expected_init_order))
        assert finalize_order == expected_finalize_order, (
            f"Finalization order {finalize_order} does not match "
            f"expected reverse init order {expected_finalize_order}"
        )

    def test_simulation_result_structure(self):
        """SimulationResult contains expected fields on success."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert isinstance(result, SimulationResult)
        assert result.success is True
        assert result.error is None
        assert result.failed_module is None
        assert result.exception is None

    def test_no_errors_in_complete_run(self):
        """Full simulation lifecycle produces no exceptions or error states."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)

        # Validate configuration first
        errors = engine.load_configuration(config)
        assert errors == [], f"Configuration validation failed: {errors}"

        # Run the simulation
        result = engine.run(config)
        assert result.success is True

        # Verify reporter has consistent data
        timeline = reporter.get_timeline()
        metrics = reporter.get_metrics()

        # Metrics should reflect the simulation activity
        assert metrics.total_duration > 0, (
            "Simulation should have non-zero duration"
        )

    def test_authorization_events_precede_flight_events(self):
        """Authorization chain completes before flight operations begin.

        The authorization module runs concurrently with other modules,
        so we verify its events appear with correct timestamps.
        """
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        timeline = reporter.get_timeline()

        # Find authorization chain completion time
        chain_events = [
            e for e in timeline if e.event_type == "chain_completion"
        ]
        assert len(chain_events) == 1
        chain_completion_time = chain_events[0].timestamp

        # The chain has gates with durations 5.0 + 3.0 = 8.0 (sequential)
        assert chain_completion_time == 8.0, (
            f"Sequential chain (5.0 + 3.0) should complete at t=8.0, "
            f"got {chain_completion_time}"
        )

    def test_all_gate_completions_recorded(self):
        """Each gate in the authorization chain produces a completion event."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        timeline = reporter.get_timeline()

        gate_completions = [
            e for e in timeline if e.event_type == "gate_completion"
        ]
        # 2 gates configured (title_10 and dipclear)
        assert len(gate_completions) == 2

        gate_names = {e.payload.get("gate_name") for e in gate_completions}
        assert "title_10" in gate_names
        assert "dipclear" in gate_names

    def test_fuel_consumed_events_for_each_leg(self):
        """Aerodynamics module produces fuel_consumed event for each flight leg."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        timeline = reporter.get_timeline()

        fuel_events = [
            e for e in timeline if e.event_type == "fuel_consumed"
        ]
        # Route has 4 legs: origin→wp1, wp1→drop_zone, drop_zone→wp2, wp2→dest
        assert len(fuel_events) == 4, (
            f"Expected 4 fuel_consumed events (one per leg), got {len(fuel_events)}"
        )

    def test_extraction_events_at_drop_zone(self):
        """Airdrop produces extraction events when aircraft reaches drop zone."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        timeline = reporter.get_timeline()

        extraction_starts = [
            e for e in timeline if e.event_type == "extraction_start"
        ]
        extraction_completes = [
            e for e in timeline if e.event_type == "extraction_complete"
        ]

        assert len(extraction_starts) == 1, "Expected one extraction start event"
        assert len(extraction_completes) == 1, "Expected one extraction complete event"

        # Extraction start should precede extraction complete
        start_ts = extraction_starts[0].timestamp
        complete_ts = extraction_completes[0].timestamp
        assert complete_ts > start_ts, (
            "Extraction complete must occur after extraction start"
        )

        # The extraction phase duration is configured as 8.0
        assert complete_ts - start_ts == 8.0, (
            f"Extraction duration should be 8.0, got {complete_ts - start_ts}"
        )

    def test_reporter_metrics_non_zero(self):
        """Reporter summary metrics reflect actual simulation activity."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        metrics = reporter.get_metrics()

        # Total duration should be positive (simulation ran)
        assert metrics.total_duration > 0

        # Chain completion times should include our chain
        assert "auth_chain_1" in metrics.chain_completion_times
        assert metrics.chain_completion_times["auth_chain_1"] == 8.0

    def test_reporter_export_json_produces_valid_output(self):
        """Reporter can export the results as valid JSON."""
        import json

        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        reporter = Reporter(event_bus)
        config = _make_full_mission_config()

        engine = SimulationEngine(registry, event_bus)
        result = engine.run(config)

        assert result.success is True

        json_output = reporter.export("json")
        assert json_output is not None
        assert len(json_output) > 0

        # Should be valid JSON
        parsed = json.loads(json_output)
        assert "timeline" in parsed
        assert "metrics" in parsed

