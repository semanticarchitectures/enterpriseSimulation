"""Property tests for Aerodynamics Module.

**Property 18: Aerodynamic Fuel Accounting Invariant**
**Property 19: Fuel Insufficiency Detection**
**Validates: Requirements 8.1, 8.2, 8.5, 8.7**

Property 18: For any sequence of flight legs with an aircraft configuration, the
cumulative fuel consumed SHALL equal the sum of individual Breguet-computed fuel
burns for each leg, and the remaining fuel after each leg SHALL equal
(initial_fuel_weight − cumulative_fuel_consumed).

Property 19: For any flight leg where the computed fuel-burn exceeds the aircraft's
remaining fuel (initial minus cumulative prior consumption), the Aerodynamics_Module
SHALL publish a fuel-insufficient event before the leg begins, containing the
required fuel and available fuel.
"""

import simpy
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.events import (
    FuelConsumedEvent,
    FuelInsufficientEvent,
    SimulationEvent,
)
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.modules.aerodynamics import AerodynamicsModule


# === Hypothesis Strategies ===

# Positive floats for aircraft parameters
positive_float = st.floats(
    min_value=0.1, max_value=1e4, allow_nan=False, allow_infinity=False
)

# Distance for flight legs (in nautical miles)
distance_nm_st = st.floats(
    min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False
)

# Number of intermediate waypoints (producing multi-leg routes)
num_intermediate_st = st.integers(min_value=0, max_value=5)


# === Helper Functions ===


def _make_linear_route_graph(
    distances: list[float],
) -> RouteGraphConfig:
    """Create a linear route graph: A -> W1 -> W2 -> ... -> B with given distances.

    Each entry in `distances` represents the distance of one leg.
    """
    num_legs = len(distances)
    node_names = ["origin"] + [f"wp_{i}" for i in range(num_legs - 1)] + ["destination"]

    nodes = [
        RouteNode(entity_id=f"node_{name}", name=name, node_type="waypoint")
        for name in node_names
    ]

    edges = [
        RouteEdge(
            source=node_names[i],
            destination=node_names[i + 1],
            distance_nm=distances[i],
        )
        for i in range(num_legs)
    ]

    return RouteGraphConfig(
        nodes=nodes,
        edges=edges,
        origin="origin",
        destination="destination",
    )


def _make_aircraft_config(
    lift_to_drag_ratio: float,
    specific_fuel_consumption: float,
    initial_fuel_weight: float,
) -> AircraftConfig:
    """Create an AircraftConfig with the given parameters."""
    return AircraftConfig(
        entity_id="aircraft_test",
        aircraft_type="TestAircraft",
        lift_to_drag_ratio=lift_to_drag_ratio,
        specific_fuel_consumption=specific_fuel_consumption,
        initial_fuel_weight=initial_fuel_weight,
        max_fuel_capacity=initial_fuel_weight * 1.2,
    )


def _make_mission_config(
    route_graph: RouteGraphConfig,
    aircraft: AircraftConfig,
) -> MissionConfiguration:
    """Build a MissionConfiguration for aerodynamics testing."""
    return MissionConfiguration(
        mission_id="aero_test",
        mission_name="Aerodynamics Test Mission",
        route_graph=route_graph,
        aircraft=aircraft,
        active_modules=["aerodynamics"],
    )


def _setup_aerodynamics_module(
    distances: list[float],
    lift_to_drag_ratio: float,
    specific_fuel_consumption: float,
    initial_fuel_weight: float,
) -> tuple[AerodynamicsModule, EventBus, simpy.Environment]:
    """Create and initialize an AerodynamicsModule with the given parameters."""
    env = simpy.Environment()
    event_bus = EventBus()
    module = AerodynamicsModule()

    route_graph = _make_linear_route_graph(distances)
    aircraft = _make_aircraft_config(
        lift_to_drag_ratio=lift_to_drag_ratio,
        specific_fuel_consumption=specific_fuel_consumption,
        initial_fuel_weight=initial_fuel_weight,
    )
    config = _make_mission_config(route_graph, aircraft)

    module.initialize(env, event_bus, config)
    return module, event_bus, env


def _run_simulation(env: simpy.Environment, module: AerodynamicsModule) -> None:
    """Run the SimPy simulation with the module's processes."""
    processes = module.create_processes(env)
    for proc in processes:
        pass  # processes are already registered via env.process() in create_processes
    env.run()


# === Strategy for generating multi-leg routes with sufficient fuel ===


@st.composite
def sufficient_fuel_scenario(draw):
    """Generate a scenario where the aircraft has enough fuel for all legs.

    Returns (distances, lift_to_drag_ratio, specific_fuel_consumption, initial_fuel_weight)
    where the fuel is guaranteed to be sufficient for all legs.
    """
    num_legs = draw(st.integers(min_value=1, max_value=6))
    distances = [
        draw(st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False))
        for _ in range(num_legs)
    ]
    lift_to_drag_ratio = draw(
        st.floats(min_value=5.0, max_value=25.0, allow_nan=False, allow_infinity=False)
    )
    specific_fuel_consumption = draw(
        st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False)
    )

    # Compute the total fuel required
    total_fuel_required = sum(
        d * specific_fuel_consumption / lift_to_drag_ratio for d in distances
    )

    # Ensure we have more than enough fuel (add 10% buffer)
    initial_fuel_weight = total_fuel_required * draw(
        st.floats(min_value=1.1, max_value=5.0, allow_nan=False, allow_infinity=False)
    )

    return distances, lift_to_drag_ratio, specific_fuel_consumption, initial_fuel_weight


@st.composite
def insufficient_fuel_scenario(draw):
    """Generate a scenario where fuel will run out partway through the route.

    Returns (distances, lift_to_drag_ratio, specific_fuel_consumption, initial_fuel_weight,
             expected_insufficient_leg_index)
    The aircraft will have enough fuel for legs before the insufficient leg,
    but not enough for the insufficient leg itself.
    """
    num_legs = draw(st.integers(min_value=2, max_value=6))
    distances = [
        draw(st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False))
        for _ in range(num_legs)
    ]
    lift_to_drag_ratio = draw(
        st.floats(min_value=5.0, max_value=25.0, allow_nan=False, allow_infinity=False)
    )
    specific_fuel_consumption = draw(
        st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False)
    )

    # Choose which leg will be the one where fuel runs out
    insufficient_leg_idx = draw(st.integers(min_value=0, max_value=num_legs - 1))

    # Compute fuel needed for legs BEFORE the insufficient leg
    fuel_for_prior_legs = sum(
        distances[i] * specific_fuel_consumption / lift_to_drag_ratio
        for i in range(insufficient_leg_idx)
    )

    # Compute fuel needed for the insufficient leg
    fuel_for_insufficient_leg = (
        distances[insufficient_leg_idx] * specific_fuel_consumption / lift_to_drag_ratio
    )

    # Set initial fuel so that: prior legs succeed but insufficient leg fails
    # Fuel = enough for prior legs + partial amount for the insufficient leg
    partial_factor = draw(
        st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
    )
    initial_fuel_weight = fuel_for_prior_legs + (fuel_for_insufficient_leg * partial_factor)

    # Guard against degenerate case where initial_fuel_weight is too small
    assume(initial_fuel_weight > 0.01)

    return (
        distances,
        lift_to_drag_ratio,
        specific_fuel_consumption,
        initial_fuel_weight,
        insufficient_leg_idx,
    )


class TestAerodynamicFuelAccountingInvariant:
    """Property 18: Aerodynamic Fuel Accounting Invariant.

    **Validates: Requirements 8.1, 8.2, 8.7**

    For any sequence of flight legs with an aircraft configuration, the cumulative
    fuel consumed SHALL equal the sum of individual Breguet-computed fuel burns for
    each leg, and the remaining fuel after each leg SHALL equal
    (initial_fuel_weight − cumulative_fuel_consumed).
    """

    @given(scenario=sufficient_fuel_scenario())
    @settings(max_examples=200)
    def test_cumulative_fuel_equals_sum_of_individual_burns(
        self,
        scenario: tuple[list[float], float, float, float],
    ):
        """Cumulative fuel consumed == sum of compute_fuel_burn(distance) for each leg."""
        distances, lift_to_drag_ratio, specific_fuel_consumption, initial_fuel_weight = scenario

        module, event_bus, env = _setup_aerodynamics_module(
            distances=distances,
            lift_to_drag_ratio=lift_to_drag_ratio,
            specific_fuel_consumption=specific_fuel_consumption,
            initial_fuel_weight=initial_fuel_weight,
        )

        # Collect fuel consumed events
        fuel_events: list[FuelConsumedEvent] = []
        event_bus.subscribe(
            FuelConsumedEvent,
            lambda e: fuel_events.append(e),
            "test_fuel_subscriber",
        )

        # Run the simulation
        _run_simulation(env, module)

        # Compute expected fuel burns using the Breguet formula
        expected_burns = [
            d * specific_fuel_consumption / lift_to_drag_ratio for d in distances
        ]
        expected_total = sum(expected_burns)

        # Verify cumulative fuel consumed matches the sum of individual burns
        assert abs(module.cumulative_fuel_consumed - expected_total) < 1e-9, (
            f"Expected cumulative fuel {expected_total}, got {module.cumulative_fuel_consumed}"
        )

        # Verify each event reports the correct fuel quantity
        assert len(fuel_events) == len(distances), (
            f"Expected {len(distances)} fuel consumed events, got {len(fuel_events)}"
        )
        for i, event in enumerate(fuel_events):
            assert abs(event.fuel_quantity - expected_burns[i]) < 1e-9, (
                f"Leg {i}: expected burn {expected_burns[i]}, event reported {event.fuel_quantity}"
            )

    @given(scenario=sufficient_fuel_scenario())
    @settings(max_examples=200)
    def test_remaining_fuel_equals_initial_minus_cumulative(
        self,
        scenario: tuple[list[float], float, float, float],
    ):
        """Remaining fuel after all legs == initial_fuel_weight - cumulative_fuel_consumed."""
        distances, lift_to_drag_ratio, specific_fuel_consumption, initial_fuel_weight = scenario

        module, event_bus, env = _setup_aerodynamics_module(
            distances=distances,
            lift_to_drag_ratio=lift_to_drag_ratio,
            specific_fuel_consumption=specific_fuel_consumption,
            initial_fuel_weight=initial_fuel_weight,
        )

        # Run the simulation
        _run_simulation(env, module)

        # Verify the invariant: remaining_fuel == initial - cumulative
        expected_remaining = initial_fuel_weight - module.cumulative_fuel_consumed
        assert abs(module.remaining_fuel - expected_remaining) < 1e-9, (
            f"Expected remaining fuel {expected_remaining}, got {module.remaining_fuel}"
        )

    @given(scenario=sufficient_fuel_scenario())
    @settings(max_examples=200)
    def test_fuel_conservation_initial_equals_consumed_plus_remaining(
        self,
        scenario: tuple[list[float], float, float, float],
    ):
        """initial_fuel_weight == cumulative_fuel_consumed + remaining_fuel (conservation)."""
        distances, lift_to_drag_ratio, specific_fuel_consumption, initial_fuel_weight = scenario

        module, event_bus, env = _setup_aerodynamics_module(
            distances=distances,
            lift_to_drag_ratio=lift_to_drag_ratio,
            specific_fuel_consumption=specific_fuel_consumption,
            initial_fuel_weight=initial_fuel_weight,
        )

        # Run the simulation
        _run_simulation(env, module)

        # Verify conservation: initial == consumed + remaining
        total = module.cumulative_fuel_consumed + module.remaining_fuel
        assert abs(total - initial_fuel_weight) < 1e-9, (
            f"Consumed ({module.cumulative_fuel_consumed}) + remaining ({module.remaining_fuel}) "
            f"= {total}, expected initial_fuel_weight = {initial_fuel_weight}"
        )


class TestFuelInsufficiencyDetection:
    """Property 19: Fuel Insufficiency Detection.

    **Validates: Requirements 8.5**

    For any flight leg where the computed fuel-burn exceeds the aircraft's
    remaining fuel (initial minus cumulative prior consumption), the
    Aerodynamics_Module SHALL publish a fuel-insufficient event before the leg
    begins, containing the required fuel and available fuel.
    """

    @given(scenario=insufficient_fuel_scenario())
    @settings(max_examples=200)
    def test_fuel_insufficient_event_published(
        self,
        scenario: tuple[list[float], float, float, float, int],
    ):
        """FuelInsufficientEvent is published when fuel-burn exceeds remaining fuel."""
        (
            distances,
            lift_to_drag_ratio,
            specific_fuel_consumption,
            initial_fuel_weight,
            expected_insufficient_leg_idx,
        ) = scenario

        module, event_bus, env = _setup_aerodynamics_module(
            distances=distances,
            lift_to_drag_ratio=lift_to_drag_ratio,
            specific_fuel_consumption=specific_fuel_consumption,
            initial_fuel_weight=initial_fuel_weight,
        )

        # Collect events
        insufficient_events: list[FuelInsufficientEvent] = []
        event_bus.subscribe(
            FuelInsufficientEvent,
            lambda e: insufficient_events.append(e),
            "test_insufficient_subscriber",
        )

        consumed_events: list[FuelConsumedEvent] = []
        event_bus.subscribe(
            FuelConsumedEvent,
            lambda e: consumed_events.append(e),
            "test_consumed_subscriber",
        )

        # Run the simulation
        _run_simulation(env, module)

        # There must be exactly one FuelInsufficientEvent
        assert len(insufficient_events) == 1, (
            f"Expected 1 FuelInsufficientEvent, got {len(insufficient_events)}"
        )

        # Only legs before the insufficient leg should have completed
        assert len(consumed_events) == expected_insufficient_leg_idx, (
            f"Expected {expected_insufficient_leg_idx} completed legs, "
            f"got {len(consumed_events)}"
        )

    @given(scenario=insufficient_fuel_scenario())
    @settings(max_examples=200)
    def test_fuel_insufficient_event_contains_correct_fields(
        self,
        scenario: tuple[list[float], float, float, float, int],
    ):
        """FuelInsufficientEvent contains correct required_fuel and available_fuel."""
        (
            distances,
            lift_to_drag_ratio,
            specific_fuel_consumption,
            initial_fuel_weight,
            expected_insufficient_leg_idx,
        ) = scenario

        module, event_bus, env = _setup_aerodynamics_module(
            distances=distances,
            lift_to_drag_ratio=lift_to_drag_ratio,
            specific_fuel_consumption=specific_fuel_consumption,
            initial_fuel_weight=initial_fuel_weight,
        )

        # Collect FuelInsufficientEvent
        insufficient_events: list[FuelInsufficientEvent] = []
        event_bus.subscribe(
            FuelInsufficientEvent,
            lambda e: insufficient_events.append(e),
            "test_insufficient_subscriber",
        )

        # Run the simulation
        _run_simulation(env, module)

        assert len(insufficient_events) == 1

        event = insufficient_events[0]

        # Compute expected values
        fuel_consumed_prior = sum(
            distances[i] * specific_fuel_consumption / lift_to_drag_ratio
            for i in range(expected_insufficient_leg_idx)
        )
        expected_available = initial_fuel_weight - fuel_consumed_prior
        expected_required = (
            distances[expected_insufficient_leg_idx]
            * specific_fuel_consumption
            / lift_to_drag_ratio
        )

        # Verify the event fields
        assert abs(event.required_fuel - expected_required) < 1e-9, (
            f"Expected required_fuel {expected_required}, got {event.required_fuel}"
        )
        assert abs(event.available_fuel - expected_available) < 1e-9, (
            f"Expected available_fuel {expected_available}, got {event.available_fuel}"
        )

    @given(scenario=insufficient_fuel_scenario())
    @settings(max_examples=200)
    def test_no_fuel_consumed_after_insufficient_event(
        self,
        scenario: tuple[list[float], float, float, float, int],
    ):
        """No FuelConsumedEvent is published for the insufficient leg or subsequent legs."""
        (
            distances,
            lift_to_drag_ratio,
            specific_fuel_consumption,
            initial_fuel_weight,
            expected_insufficient_leg_idx,
        ) = scenario

        module, event_bus, env = _setup_aerodynamics_module(
            distances=distances,
            lift_to_drag_ratio=lift_to_drag_ratio,
            specific_fuel_consumption=specific_fuel_consumption,
            initial_fuel_weight=initial_fuel_weight,
        )

        # Collect all events
        consumed_events: list[FuelConsumedEvent] = []
        event_bus.subscribe(
            FuelConsumedEvent,
            lambda e: consumed_events.append(e),
            "test_consumed_subscriber",
        )

        # Run the simulation
        _run_simulation(env, module)

        # Only legs before the insufficient leg should have completed
        assert len(consumed_events) == expected_insufficient_leg_idx, (
            f"Expected {expected_insufficient_leg_idx} consumed events (legs before "
            f"insufficient), got {len(consumed_events)}"
        )

        # The cumulative fuel consumed should only reflect prior legs
        expected_cumulative = sum(
            distances[i] * specific_fuel_consumption / lift_to_drag_ratio
            for i in range(expected_insufficient_leg_idx)
        )
        assert abs(module.cumulative_fuel_consumed - expected_cumulative) < 1e-9, (
            f"Expected cumulative {expected_cumulative}, got {module.cumulative_fuel_consumed}"
        )
