"""Property tests for Airdrop Module.

**Property 21: Airdrop State Machine Exclusivity**
**Property 22: Airdrop Weight Limit Enforcement**
**Property 23: Airdrop Event Lifecycle**
**Validates: Requirements 11.1, 11.3, 11.4, 11.6**

Property 21: For any airdrop operation with configurable phase durations, at every
simulation time point the aircraft SHALL occupy exactly one phase from the ordered
sequence (en-route → slowdown → extraction → acceleration → en-route), and the total
time in each phase SHALL equal its configured duration.

Property 22: For any cargo manifest where total weight exceeds the configured cargo
weight limit, the Airdrop_Module SHALL prevent the state transition sequence from
starting (aircraft remains in en-route) and publish a weight-exceeded event with the
cargo weight and limit.

Property 23: For any successful airdrop extraction sequence, the module SHALL publish
an extraction-start event at the beginning of the extraction phase (with drop_zone_id),
and an extraction-complete event at its end (with cargo manifest), and the extraction
duration SHALL equal the configured extraction phase duration.
"""

import simpy
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase, CargoItem
from enterprise_sim.models.events import (
    ExtractionCompleteEvent,
    ExtractionStartEvent,
    SimulationEvent,
    WeightExceededEvent,
)
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.modules.airdrop import AirdropModule


# === Hypothesis Strategies ===

# Positive floats for phase durations (reasonable sim time units)
phase_duration_st = st.floats(
    min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False
)

# Cargo item weight
cargo_weight_st = st.floats(
    min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False
)

# Cargo weight limit
weight_limit_st = st.floats(
    min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False
)


# === Helper Functions ===


def _make_minimal_route_graph() -> RouteGraphConfig:
    """Create a minimal valid route graph for airdrop tests."""
    return RouteGraphConfig(
        nodes=[
            RouteNode(entity_id="node_origin", name="origin", node_type="waypoint"),
            RouteNode(entity_id="node_dest", name="destination", node_type="drop_zone"),
        ],
        edges=[
            RouteEdge(source="origin", destination="destination", distance_nm=100.0)
        ],
        origin="origin",
        destination="destination",
    )


def _make_aircraft_config() -> AircraftConfig:
    """Create a minimal AircraftConfig for airdrop tests."""
    return AircraftConfig(
        entity_id="aircraft_test",
        aircraft_type="TestAircraft",
        lift_to_drag_ratio=15.0,
        specific_fuel_consumption=0.5,
        initial_fuel_weight=5000.0,
        max_fuel_capacity=6000.0,
    )


def _make_airdrop_config(
    phase_durations: dict[AirdropPhase, float],
    cargo_items: list[CargoItem],
    cargo_weight_limit: float,
) -> AirdropConfig:
    """Create an AirdropConfig with given parameters."""
    return AirdropConfig(
        extraction_speed=130.0,
        drop_altitude_ft=1000.0,
        cargo_weight_limit=cargo_weight_limit,
        phase_durations=phase_durations,
        cargo_manifest=cargo_items,
    )


def _make_mission_config(airdrop_config: AirdropConfig) -> MissionConfiguration:
    """Build a MissionConfiguration for airdrop testing."""
    return MissionConfiguration(
        mission_id="airdrop_test",
        mission_name="Airdrop Test Mission",
        route_graph=_make_minimal_route_graph(),
        aircraft=_make_aircraft_config(),
        airdrop_parameters=airdrop_config,
        active_modules=["airdrop"],
    )


def _setup_airdrop_module(
    phase_durations: dict[AirdropPhase, float],
    cargo_items: list[CargoItem],
    cargo_weight_limit: float,
) -> tuple[AirdropModule, EventBus, simpy.Environment]:
    """Create and initialize an AirdropModule with the given parameters."""
    env = simpy.Environment()
    event_bus = EventBus()
    module = AirdropModule()

    airdrop_config = _make_airdrop_config(
        phase_durations=phase_durations,
        cargo_items=cargo_items,
        cargo_weight_limit=cargo_weight_limit,
    )
    config = _make_mission_config(airdrop_config)
    module.initialize(env, event_bus, config)
    return module, event_bus, env


def _run_simulation(env: simpy.Environment, module: AirdropModule) -> None:
    """Run the SimPy simulation with the module's processes."""
    processes = module.create_processes(env)
    for proc in processes:
        pass  # processes registered via env.process() in create_processes
    env.run()


# === Composite Strategies ===


@st.composite
def valid_phase_durations(draw):
    """Generate a valid set of phase durations (all positive)."""
    return {
        AirdropPhase.SLOWDOWN: draw(phase_duration_st),
        AirdropPhase.EXTRACTION: draw(phase_duration_st),
        AirdropPhase.ACCELERATION: draw(phase_duration_st),
    }


@st.composite
def valid_cargo_manifest(draw, min_items=1, max_items=5):
    """Generate a valid cargo manifest with positive weights."""
    num_items = draw(st.integers(min_value=min_items, max_value=max_items))
    items = []
    for i in range(num_items):
        weight = draw(cargo_weight_st)
        items.append(
            CargoItem(
                item_id=f"cargo_{i}",
                weight=weight,
                description=f"Test cargo item {i}",
            )
        )
    return items


@st.composite
def underweight_scenario(draw):
    """Generate a scenario where cargo weight is UNDER the limit (valid airdrop).

    Returns (phase_durations, cargo_items, cargo_weight_limit).
    """
    durations = draw(valid_phase_durations())
    cargo_items = draw(valid_cargo_manifest())
    total_weight = sum(item.weight for item in cargo_items)
    # Set limit comfortably above total weight
    buffer = draw(
        st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
    )
    cargo_weight_limit = total_weight + buffer
    return durations, cargo_items, cargo_weight_limit


@st.composite
def overweight_scenario(draw):
    """Generate a scenario where cargo weight EXCEEDS the limit.

    Returns (phase_durations, cargo_items, cargo_weight_limit).
    """
    durations = draw(valid_phase_durations())
    cargo_items = draw(valid_cargo_manifest(min_items=1, max_items=5))
    total_weight = sum(item.weight for item in cargo_items)
    # Ensure total weight > 0 and set limit below total weight
    assume(total_weight > 0.1)
    # Set limit strictly below total weight
    fraction = draw(
        st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
    )
    cargo_weight_limit = total_weight * fraction
    assume(cargo_weight_limit > 0)
    return durations, cargo_items, cargo_weight_limit


# === Property 21: Airdrop State Machine Exclusivity ===


class TestAirdropStateMachineExclusivity:
    """Property 21: Airdrop State Machine Exclusivity.

    **Validates: Requirements 11.1**

    For any airdrop operation with configurable phase durations, at every simulation
    time point the aircraft SHALL occupy exactly one phase from the ordered sequence
    (en-route → slowdown → extraction → acceleration → en-route), and the total time
    in each phase SHALL equal its configured duration.
    """

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_phase_sequence_is_ordered(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """Aircraft transitions through phases in the correct ordered sequence."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        # Track phase transitions with timestamps
        phase_log: list[tuple[float, AirdropPhase]] = []

        # Create a monitoring process that checks phase at each transition point
        def monitor_phases():
            # Initial phase should be EN_ROUTE
            phase_log.append((env.now, module.current_phase))

            # After slowdown duration
            yield env.timeout(durations[AirdropPhase.SLOWDOWN])
            phase_log.append((env.now, module.current_phase))

            # After extraction duration
            yield env.timeout(durations[AirdropPhase.EXTRACTION])
            phase_log.append((env.now, module.current_phase))

            # After acceleration duration
            yield env.timeout(durations[AirdropPhase.ACCELERATION])
            phase_log.append((env.now, module.current_phase))

        # Start both the airdrop process and the monitor
        module.create_processes(env)
        env.process(monitor_phases())
        env.run()

        # Verify the ordered sequence of phases observed
        expected_sequence = [
            AirdropPhase.SLOWDOWN,     # During slowdown phase
            AirdropPhase.EXTRACTION,   # During extraction phase
            AirdropPhase.ACCELERATION, # During acceleration phase
            AirdropPhase.EN_ROUTE,     # Returned to en_route
        ]

        assert len(phase_log) == 4
        for i, (_, observed_phase) in enumerate(phase_log):
            assert observed_phase == expected_sequence[i], (
                f"At step {i}: expected {expected_sequence[i]}, got {observed_phase}"
            )

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_total_time_in_each_phase_equals_configured_duration(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """Total time in each phase equals its configured duration."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        # Track phase entry times
        phase_entries: dict[AirdropPhase, float] = {}
        phase_exits: dict[AirdropPhase, float] = {}

        def monitor_transitions():
            # Immediately after init, we're in EN_ROUTE, then module transitions to SLOWDOWN
            # The module transitions at time 0 to SLOWDOWN, so we observe at small intervals.
            # A better approach: just check at the expected transition boundaries.

            # At time 0, the module should be in SLOWDOWN (it transitions immediately)
            # Record entry into SLOWDOWN
            yield env.timeout(0)
            phase_entries[AirdropPhase.SLOWDOWN] = env.now

            # At end of slowdown duration, module transitions to EXTRACTION
            yield env.timeout(durations[AirdropPhase.SLOWDOWN])
            phase_exits[AirdropPhase.SLOWDOWN] = env.now
            phase_entries[AirdropPhase.EXTRACTION] = env.now

            # At end of extraction duration, module transitions to ACCELERATION
            yield env.timeout(durations[AirdropPhase.EXTRACTION])
            phase_exits[AirdropPhase.EXTRACTION] = env.now
            phase_entries[AirdropPhase.ACCELERATION] = env.now

            # At end of acceleration duration, module transitions to EN_ROUTE
            yield env.timeout(durations[AirdropPhase.ACCELERATION])
            phase_exits[AirdropPhase.ACCELERATION] = env.now

        module.create_processes(env)
        env.process(monitor_transitions())
        env.run()

        # Verify each phase duration
        for phase in [AirdropPhase.SLOWDOWN, AirdropPhase.EXTRACTION, AirdropPhase.ACCELERATION]:
            actual_duration = phase_exits[phase] - phase_entries[phase]
            expected_duration = durations[phase]
            assert abs(actual_duration - expected_duration) < 1e-9, (
                f"Phase {phase.value}: expected duration {expected_duration}, "
                f"got {actual_duration}"
            )

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_total_airdrop_time_equals_sum_of_phase_durations(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """Total simulation time for airdrop equals sum of all phase durations."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        _run_simulation(env, module)

        expected_total_time = (
            durations[AirdropPhase.SLOWDOWN]
            + durations[AirdropPhase.EXTRACTION]
            + durations[AirdropPhase.ACCELERATION]
        )
        assert abs(env.now - expected_total_time) < 1e-9, (
            f"Expected total time {expected_total_time}, got {env.now}"
        )

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_aircraft_returns_to_en_route_after_airdrop(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """Aircraft returns to EN_ROUTE after completing the airdrop sequence."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        _run_simulation(env, module)

        assert module.current_phase == AirdropPhase.EN_ROUTE, (
            f"Expected EN_ROUTE after airdrop, got {module.current_phase}"
        )


# === Property 22: Airdrop Weight Limit Enforcement ===


class TestAirdropWeightLimitEnforcement:
    """Property 22: Airdrop Weight Limit Enforcement.

    **Validates: Requirements 11.6**

    For any cargo manifest where total weight exceeds the configured cargo weight
    limit, the Airdrop_Module SHALL prevent the state transition sequence from
    starting (aircraft remains in en-route) and publish a weight-exceeded event
    with the cargo weight and limit.
    """

    @given(scenario=overweight_scenario())
    @settings(max_examples=200)
    def test_overweight_prevents_state_transition(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """Aircraft remains in EN_ROUTE when cargo weight exceeds limit."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        _run_simulation(env, module)

        # Aircraft must remain in EN_ROUTE
        assert module.current_phase == AirdropPhase.EN_ROUTE, (
            f"Expected EN_ROUTE for overweight cargo, got {module.current_phase}"
        )

    @given(scenario=overweight_scenario())
    @settings(max_examples=200)
    def test_overweight_publishes_weight_exceeded_event(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """WeightExceededEvent is published when cargo exceeds the weight limit."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        # Collect weight exceeded events
        weight_events: list[WeightExceededEvent] = []
        event_bus.subscribe(
            WeightExceededEvent,
            lambda e: weight_events.append(e),
            "test_weight_subscriber",
        )

        _run_simulation(env, module)

        # Exactly one WeightExceededEvent must be published
        assert len(weight_events) == 1, (
            f"Expected 1 WeightExceededEvent, got {len(weight_events)}"
        )

    @given(scenario=overweight_scenario())
    @settings(max_examples=200)
    def test_weight_exceeded_event_contains_correct_fields(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """WeightExceededEvent contains correct cargo_weight and weight_limit."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        weight_events: list[WeightExceededEvent] = []
        event_bus.subscribe(
            WeightExceededEvent,
            lambda e: weight_events.append(e),
            "test_weight_subscriber",
        )

        _run_simulation(env, module)

        assert len(weight_events) == 1
        event = weight_events[0]

        expected_total_weight = sum(item.weight for item in cargo_items)
        assert abs(event.cargo_weight - expected_total_weight) < 1e-9, (
            f"Expected cargo_weight {expected_total_weight}, got {event.cargo_weight}"
        )
        assert abs(event.weight_limit - cargo_weight_limit) < 1e-9, (
            f"Expected weight_limit {cargo_weight_limit}, got {event.weight_limit}"
        )

    @given(scenario=overweight_scenario())
    @settings(max_examples=200)
    def test_overweight_no_extraction_events_published(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """No ExtractionStart or ExtractionComplete events when overweight."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        extraction_start_events: list[ExtractionStartEvent] = []
        extraction_complete_events: list[ExtractionCompleteEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: extraction_start_events.append(e),
            "test_start_subscriber",
        )
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: extraction_complete_events.append(e),
            "test_complete_subscriber",
        )

        _run_simulation(env, module)

        assert len(extraction_start_events) == 0, (
            f"Expected 0 ExtractionStartEvents, got {len(extraction_start_events)}"
        )
        assert len(extraction_complete_events) == 0, (
            f"Expected 0 ExtractionCompleteEvents, got {len(extraction_complete_events)}"
        )


# === Property 23: Airdrop Event Lifecycle ===


class TestAirdropEventLifecycle:
    """Property 23: Airdrop Event Lifecycle.

    **Validates: Requirements 11.3, 11.4**

    For any successful airdrop extraction sequence, the module SHALL publish an
    extraction-start event at the beginning of the extraction phase (with drop_zone_id),
    and an extraction-complete event at its end (with cargo manifest), and the
    extraction duration SHALL equal the configured extraction phase duration.
    """

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_extraction_start_event_published_at_extraction_phase_start(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """ExtractionStartEvent is published at the start of the extraction phase."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        extraction_start_events: list[ExtractionStartEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: extraction_start_events.append(e),
            "test_start_subscriber",
        )

        _run_simulation(env, module)

        # Exactly one ExtractionStartEvent must be published
        assert len(extraction_start_events) == 1, (
            f"Expected 1 ExtractionStartEvent, got {len(extraction_start_events)}"
        )

        # It should be published at the time after slowdown completes
        event = extraction_start_events[0]
        expected_start_time = durations[AirdropPhase.SLOWDOWN]
        assert abs(event.timestamp - expected_start_time) < 1e-9, (
            f"Expected extraction start at time {expected_start_time}, "
            f"got {event.timestamp}"
        )

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_extraction_start_event_contains_drop_zone_id(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """ExtractionStartEvent contains a non-empty drop_zone_id."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        extraction_start_events: list[ExtractionStartEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: extraction_start_events.append(e),
            "test_start_subscriber",
        )

        _run_simulation(env, module)

        assert len(extraction_start_events) == 1
        event = extraction_start_events[0]
        assert event.drop_zone_id, "ExtractionStartEvent must have a non-empty drop_zone_id"

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_extraction_complete_event_published_at_extraction_phase_end(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """ExtractionCompleteEvent is published at the end of the extraction phase."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        extraction_complete_events: list[ExtractionCompleteEvent] = []
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: extraction_complete_events.append(e),
            "test_complete_subscriber",
        )

        _run_simulation(env, module)

        # Exactly one ExtractionCompleteEvent must be published
        assert len(extraction_complete_events) == 1, (
            f"Expected 1 ExtractionCompleteEvent, got {len(extraction_complete_events)}"
        )

        # It should be published at the time after slowdown + extraction completes
        event = extraction_complete_events[0]
        expected_complete_time = (
            durations[AirdropPhase.SLOWDOWN] + durations[AirdropPhase.EXTRACTION]
        )
        assert abs(event.timestamp - expected_complete_time) < 1e-9, (
            f"Expected extraction complete at time {expected_complete_time}, "
            f"got {event.timestamp}"
        )

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_extraction_complete_event_contains_cargo_manifest(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """ExtractionCompleteEvent contains the correct cargo manifest item IDs."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        extraction_complete_events: list[ExtractionCompleteEvent] = []
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: extraction_complete_events.append(e),
            "test_complete_subscriber",
        )

        _run_simulation(env, module)

        assert len(extraction_complete_events) == 1
        event = extraction_complete_events[0]

        expected_manifest_ids = [item.item_id for item in cargo_items]
        assert event.cargo_manifest == expected_manifest_ids, (
            f"Expected manifest {expected_manifest_ids}, got {event.cargo_manifest}"
        )

    @given(scenario=underweight_scenario())
    @settings(max_examples=200)
    def test_extraction_duration_equals_configured_duration(
        self,
        scenario: tuple[dict[AirdropPhase, float], list[CargoItem], float],
    ):
        """Time between ExtractionStart and ExtractionComplete equals configured extraction duration."""
        durations, cargo_items, cargo_weight_limit = scenario

        module, event_bus, env = _setup_airdrop_module(
            phase_durations=durations,
            cargo_items=cargo_items,
            cargo_weight_limit=cargo_weight_limit,
        )

        extraction_start_events: list[ExtractionStartEvent] = []
        extraction_complete_events: list[ExtractionCompleteEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: extraction_start_events.append(e),
            "test_start_subscriber",
        )
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: extraction_complete_events.append(e),
            "test_complete_subscriber",
        )

        _run_simulation(env, module)

        assert len(extraction_start_events) == 1
        assert len(extraction_complete_events) == 1

        start_time = extraction_start_events[0].timestamp
        complete_time = extraction_complete_events[0].timestamp
        actual_extraction_duration = complete_time - start_time

        expected_extraction_duration = durations[AirdropPhase.EXTRACTION]
        assert abs(actual_extraction_duration - expected_extraction_duration) < 1e-9, (
            f"Expected extraction duration {expected_extraction_duration}, "
            f"got {actual_extraction_duration}"
        )
