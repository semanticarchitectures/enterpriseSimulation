"""Unit tests for AerodynamicsModule.

Tests fuel burn computation, flight leg processing, event publishing,
fuel insufficiency detection, and lifecycle methods.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import simpy

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.engine.exceptions import ConfigurationError
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.events import FuelConsumedEvent, FuelInsufficientEvent
from enterprise_sim.models.fiscal import FiscalAccount, FiscalConfig
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.modules.aerodynamics import AerodynamicsModule


def _make_aircraft(
    lift_to_drag_ratio: float = 15.0,
    specific_fuel_consumption: float = 0.5,
    initial_fuel_weight: float = 5000.0,
    max_fuel_capacity: float = 6000.0,
) -> AircraftConfig:
    """Create an AircraftConfig with configurable parameters."""
    return AircraftConfig(
        entity_id="aircraft_1",
        aircraft_type="C-17",
        lift_to_drag_ratio=lift_to_drag_ratio,
        specific_fuel_consumption=specific_fuel_consumption,
        initial_fuel_weight=initial_fuel_weight,
        max_fuel_capacity=max_fuel_capacity,
    )


def _make_route_graph(
    nodes: list[RouteNode] | None = None,
    edges: list[RouteEdge] | None = None,
    origin: str = "A",
    destination: str = "C",
) -> RouteGraphConfig:
    """Create a RouteGraphConfig for testing."""
    if nodes is None:
        nodes = [
            RouteNode(entity_id="node_a", name="A"),
            RouteNode(entity_id="node_b", name="B"),
            RouteNode(entity_id="node_c", name="C"),
        ]
    if edges is None:
        edges = [
            RouteEdge(source="A", destination="B", distance_nm=100.0),
            RouteEdge(source="B", destination="C", distance_nm=200.0),
        ]
    return RouteGraphConfig(
        nodes=nodes,
        edges=edges,
        origin=origin,
        destination=destination,
    )


def _make_config(
    aircraft: AircraftConfig | None = None,
    route_graph: RouteGraphConfig | None = None,
    fiscal_parameters: FiscalConfig | None = None,
):
    """Create a mock MissionConfiguration."""
    if aircraft is None:
        aircraft = _make_aircraft()
    if route_graph is None:
        route_graph = _make_route_graph()

    config = MagicMock()
    config.aircraft = aircraft
    config.route_graph = route_graph
    config.fiscal_parameters = fiscal_parameters
    return config


class TestAerodynamicsModuleMetadata:
    """Test class-level metadata."""

    def test_module_type(self):
        assert AerodynamicsModule.module_type == "aerodynamics"

    def test_dependencies_includes_fiscal(self):
        assert AerodynamicsModule.dependencies == {"fiscal"}


class TestAerodynamicsModuleInitialize:
    """Test initialization behavior."""

    def test_initialize_stores_aircraft_parameters(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()

        module.initialize(env, event_bus, config)

        assert module._lift_to_drag_ratio == 15.0
        assert module._specific_fuel_consumption == 0.5
        assert module._initial_fuel_weight == 5000.0

    def test_initialize_sets_cumulative_fuel_to_zero(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()

        module.initialize(env, event_bus, config)

        assert module.cumulative_fuel_consumed == 0.0

    def test_initialize_computes_leg_sequence(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()

        module.initialize(env, event_bus, config)

        assert len(module._legs) == 2
        assert module._legs[0] == ("A", "B", 100.0)
        assert module._legs[1] == ("B", "C", 200.0)


class TestAerodynamicsModuleValidation:
    """Test aircraft parameter validation during initialization."""

    def test_rejects_zero_lift_to_drag_ratio(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        # Pydantic will reject gt=0 constraint, so we use a mock
        aircraft = MagicMock()
        aircraft.lift_to_drag_ratio = 0
        aircraft.specific_fuel_consumption = 0.5
        aircraft.initial_fuel_weight = 5000.0
        aircraft.max_fuel_capacity = 6000.0

        config = MagicMock()
        config.aircraft = aircraft
        config.route_graph = _make_route_graph()
        config.fiscal_parameters = None

        with pytest.raises(ConfigurationError):
            module.initialize(env, event_bus, config)

    def test_rejects_negative_specific_fuel_consumption(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        aircraft = MagicMock()
        aircraft.lift_to_drag_ratio = 15.0
        aircraft.specific_fuel_consumption = -1.0
        aircraft.initial_fuel_weight = 5000.0
        aircraft.max_fuel_capacity = 6000.0

        config = MagicMock()
        config.aircraft = aircraft
        config.route_graph = _make_route_graph()
        config.fiscal_parameters = None

        with pytest.raises(ConfigurationError):
            module.initialize(env, event_bus, config)

    def test_rejects_negative_initial_fuel_weight(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        aircraft = MagicMock()
        aircraft.lift_to_drag_ratio = 15.0
        aircraft.specific_fuel_consumption = 0.5
        aircraft.initial_fuel_weight = -100.0
        aircraft.max_fuel_capacity = 6000.0

        config = MagicMock()
        config.aircraft = aircraft
        config.route_graph = _make_route_graph()
        config.fiscal_parameters = None

        with pytest.raises(ConfigurationError):
            module.initialize(env, event_bus, config)

    def test_rejects_multiple_invalid_params(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        aircraft = MagicMock()
        aircraft.lift_to_drag_ratio = -5.0
        aircraft.specific_fuel_consumption = 0
        aircraft.initial_fuel_weight = -100.0
        aircraft.max_fuel_capacity = -1.0

        config = MagicMock()
        config.aircraft = aircraft
        config.route_graph = _make_route_graph()
        config.fiscal_parameters = None

        with pytest.raises(ConfigurationError) as exc_info:
            module.initialize(env, event_bus, config)

        error = exc_info.value
        assert "lift_to_drag_ratio" in error.invalid_fields
        assert "specific_fuel_consumption" in error.invalid_fields
        assert "initial_fuel_weight" in error.invalid_fields
        assert "max_fuel_capacity" in error.invalid_fields


class TestComputeFuelBurn:
    """Test fuel burn computation using linearized Breguet equation."""

    def test_basic_fuel_burn_calculation(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        # sfc=0.5, L/D=15 → fuel = distance * 0.5 / 15
        config = _make_config(
            aircraft=_make_aircraft(
                specific_fuel_consumption=0.5,
                lift_to_drag_ratio=15.0,
            )
        )
        module.initialize(env, event_bus, config)

        fuel = module.compute_fuel_burn(300.0)

        # 300 * 0.5 / 15 = 10.0
        assert fuel == pytest.approx(10.0)

    def test_zero_distance_yields_zero_fuel(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        fuel = module.compute_fuel_burn(0.0)

        assert fuel == 0.0

    def test_fuel_burn_proportional_to_distance(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(
            aircraft=_make_aircraft(
                specific_fuel_consumption=1.0,
                lift_to_drag_ratio=10.0,
            )
        )
        module.initialize(env, event_bus, config)

        # fuel = distance * 1.0 / 10.0
        fuel_100 = module.compute_fuel_burn(100.0)
        fuel_200 = module.compute_fuel_burn(200.0)

        assert fuel_200 == pytest.approx(2.0 * fuel_100)


class TestFlightLegProcess:
    """Test the flight leg SimPy process."""

    def test_successful_flight_publishes_fuel_consumed_events(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(
            aircraft=_make_aircraft(initial_fuel_weight=5000.0)
        )
        module.initialize(env, event_bus, config)

        consumed_events: list[FuelConsumedEvent] = []
        event_bus.subscribe(
            FuelConsumedEvent,
            lambda e: consumed_events.append(e),
            "test_sub",
        )

        # Run the simulation
        module.create_processes(env)
        env.run()

        # Should have consumed events for both legs (A->B, B->C)
        assert len(consumed_events) == 2
        assert consumed_events[0].leg_id == "A->B"
        assert consumed_events[1].leg_id == "B->C"

    def test_cumulative_fuel_consumed_updates_after_legs(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        # sfc=0.5, L/D=15: fuel_per_nm = 0.5/15 = 1/30
        # A->B (100nm): fuel = 100 * 0.5 / 15 ≈ 3.333
        # B->C (200nm): fuel = 200 * 0.5 / 15 ≈ 6.667
        # Total ≈ 10.0
        config = _make_config(
            aircraft=_make_aircraft(
                specific_fuel_consumption=0.5,
                lift_to_drag_ratio=15.0,
                initial_fuel_weight=5000.0,
            )
        )
        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        expected_total = (100.0 * 0.5 / 15.0) + (200.0 * 0.5 / 15.0)
        assert module.cumulative_fuel_consumed == pytest.approx(expected_total)

    def test_remaining_fuel_property(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(
            aircraft=_make_aircraft(
                specific_fuel_consumption=0.5,
                lift_to_drag_ratio=15.0,
                initial_fuel_weight=5000.0,
            )
        )
        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        expected_consumed = (100.0 * 0.5 / 15.0) + (200.0 * 0.5 / 15.0)
        expected_remaining = 5000.0 - expected_consumed
        assert module.remaining_fuel == pytest.approx(expected_remaining)

    def test_fuel_insufficient_event_published_when_not_enough_fuel(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        # Set fuel so low it can't complete even the first leg
        # First leg A->B is 100nm. fuel_burn = 100 * 0.5/15 = 3.33
        # With initial_fuel_weight=2.0, this will fail
        config = _make_config(
            aircraft=_make_aircraft(
                specific_fuel_consumption=0.5,
                lift_to_drag_ratio=15.0,
                initial_fuel_weight=2.0,
            )
        )
        module.initialize(env, event_bus, config)

        insufficient_events: list[FuelInsufficientEvent] = []
        event_bus.subscribe(
            FuelInsufficientEvent,
            lambda e: insufficient_events.append(e),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        assert len(insufficient_events) == 1
        evt = insufficient_events[0]
        assert evt.leg_id == "A->B"
        assert evt.required_fuel == pytest.approx(100.0 * 0.5 / 15.0)
        assert evt.available_fuel == 2.0

    def test_fuel_insufficient_stops_further_legs(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        # First leg uses 100 * 0.5/15 ≈ 3.33, second uses 200 * 0.5/15 ≈ 6.67
        # Set fuel to 5.0 — enough for first leg but not second
        config = _make_config(
            aircraft=_make_aircraft(
                specific_fuel_consumption=0.5,
                lift_to_drag_ratio=15.0,
                initial_fuel_weight=5.0,
            )
        )
        module.initialize(env, event_bus, config)

        consumed_events: list[FuelConsumedEvent] = []
        insufficient_events: list[FuelInsufficientEvent] = []
        event_bus.subscribe(
            FuelConsumedEvent,
            lambda e: consumed_events.append(e),
            "test_sub_consumed",
        )
        event_bus.subscribe(
            FuelInsufficientEvent,
            lambda e: insufficient_events.append(e),
            "test_sub_insufficient",
        )

        module.create_processes(env)
        env.run()

        # First leg completed
        assert len(consumed_events) == 1
        assert consumed_events[0].leg_id == "A->B"

        # Second leg failed
        assert len(insufficient_events) == 1
        assert insufficient_events[0].leg_id == "B->C"

    def test_simulation_time_advances_by_distance(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(
            aircraft=_make_aircraft(initial_fuel_weight=5000.0)
        )
        module.initialize(env, event_bus, config)

        timestamps: list[float] = []
        event_bus.subscribe(
            FuelConsumedEvent,
            lambda e: timestamps.append(e.timestamp),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        # A->B is 100nm, B->C is 200nm
        # First leg completes at t=100, second at t=300
        assert timestamps[0] == pytest.approx(100.0)
        assert timestamps[1] == pytest.approx(300.0)

    def test_single_leg_route(self):
        """A single-edge route A->B produces one process with one leg."""
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()

        single_nodes = [
            RouteNode(entity_id="node_a", name="A"),
            RouteNode(entity_id="node_b", name="B"),
        ]
        single_edges = [RouteEdge(source="A", destination="B", distance_nm=50.0)]
        single_route = RouteGraphConfig(
            nodes=single_nodes,
            edges=single_edges,
            origin="A",
            destination="B",
        )
        config = _make_config(route_graph=single_route)
        module.initialize(env, event_bus, config)

        processes = module.create_processes(env)
        assert len(processes) == 1  # One process for the single leg

        consumed_events: list[FuelConsumedEvent] = []
        event_bus.subscribe(
            FuelConsumedEvent,
            lambda e: consumed_events.append(e),
            "test_sub",
        )
        env.run()
        assert len(consumed_events) == 1
        assert consumed_events[0].leg_id == "A->B"


class TestAerodynamicsModuleLifecycle:
    """Test lifecycle methods."""

    def test_create_processes_returns_one_process(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        processes = module.create_processes(env)

        assert len(processes) == 1

    def test_finalize_resets_state(self):
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        # Run to accumulate fuel
        module.create_processes(env)
        env.run()

        assert module.cumulative_fuel_consumed > 0

        module.finalize()

        assert module.cumulative_fuel_consumed == 0.0
        assert module._legs == []


class TestRoutePathComputation:
    """Test BFS path computation through route graph."""

    def test_linear_path(self):
        """A -> B -> C produces two legs."""
        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        assert module._legs == [
            ("A", "B", 100.0),
            ("B", "C", 200.0),
        ]

    def test_branching_path_takes_shortest_by_hops(self):
        """With branching, BFS finds shortest hop path."""
        nodes = [
            RouteNode(entity_id="n1", name="X"),
            RouteNode(entity_id="n2", name="Y"),
            RouteNode(entity_id="n3", name="Z"),
            RouteNode(entity_id="n4", name="W"),
        ]
        edges = [
            # Direct path: X -> Z (1 hop, 500nm)
            RouteEdge(source="X", destination="Z", distance_nm=500.0),
            # Indirect: X -> Y -> Z (2 hops, 100+100nm)
            RouteEdge(source="X", destination="Y", distance_nm=100.0),
            RouteEdge(source="Y", destination="Z", distance_nm=100.0),
            # W has an outgoing edge so not a dead end
            RouteEdge(source="X", destination="W", distance_nm=50.0),
            RouteEdge(source="W", destination="Z", distance_nm=60.0),
        ]
        route = RouteGraphConfig(
            nodes=nodes, edges=edges, origin="X", destination="Z"
        )

        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(route_graph=route)
        module.initialize(env, event_bus, config)

        # BFS finds shortest path by hops: X -> Z directly
        assert module._legs == [("X", "Z", 500.0)]

    def test_multi_hop_path(self):
        """Multi-hop path through multiple waypoints."""
        nodes = [
            RouteNode(entity_id="n1", name="Start"),
            RouteNode(entity_id="n2", name="WP1"),
            RouteNode(entity_id="n3", name="WP2"),
            RouteNode(entity_id="n4", name="End"),
        ]
        edges = [
            RouteEdge(source="Start", destination="WP1", distance_nm=50.0),
            RouteEdge(source="WP1", destination="WP2", distance_nm=75.0),
            RouteEdge(source="WP2", destination="End", distance_nm=100.0),
        ]
        route = RouteGraphConfig(
            nodes=nodes, edges=edges, origin="Start", destination="End"
        )

        module = AerodynamicsModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(route_graph=route)
        module.initialize(env, event_bus, config)

        assert module._legs == [
            ("Start", "WP1", 50.0),
            ("WP1", "WP2", 75.0),
            ("WP2", "End", 100.0),
        ]
