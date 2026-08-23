"""Property tests for Authorization Module.

**Property 8: Authorization Chain Timing**
**Property 9: Authorization Gate Timeout Cascading**
**Validates: Requirements 4.3, 4.5, 4.6**

Property 8: For any authorization chain, sequential chains SHALL complete in total
time equal to the sum of gate durations, and parallel chains SHALL complete in total
time equal to the maximum gate duration.

Property 9: For any authorization gate where the configured timeout is less than the
gate duration, the gate SHALL transition to a failed state, all dependent gates SHALL
be cancelled, and a timeout event SHALL be published containing the gate name and
chain identifier.
"""

import simpy
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.authorization import AuthorizationChainConfig, AuthorizationGate
from enterprise_sim.models.events import (
    ChainCompletionEvent,
    GateCompletionEvent,
    GateTimeoutEvent,
    SimulationEvent,
)
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.modules.authorization import AuthorizationModule


# === Hypothesis Strategies ===

# Positive float for gate durations (keep small for SimPy timing precision)
positive_duration_st = st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False)

# Non-empty string for identifiers
non_empty_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=15,
)


@st.composite
def gate_without_timeout_st(draw, name=None):
    """Generate an AuthorizationGate with no timeout (or timeout > duration)."""
    gate_name = name or draw(non_empty_str)
    duration = draw(positive_duration_st)
    # Either no timeout, or timeout that is greater than duration (won't trigger)
    use_timeout = draw(st.booleans())
    if use_timeout:
        timeout = duration + draw(st.floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False))
    else:
        timeout = None

    return AuthorizationGate(
        entity_id=f"gate_{gate_name}",
        timestamp=0.0,
        start_time=0.0,
        gate_name=gate_name,
        duration=duration,
        timeout=timeout,
        status="pending",
    )


@st.composite
def gate_with_timeout_st(draw, name=None):
    """Generate an AuthorizationGate where timeout < duration (will timeout)."""
    gate_name = name or draw(non_empty_str)
    duration = draw(st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))
    # timeout is strictly less than duration
    timeout = draw(st.floats(min_value=0.1, max_value=duration - 0.01, allow_nan=False, allow_infinity=False))
    assume(timeout < duration)

    return AuthorizationGate(
        entity_id=f"gate_{gate_name}",
        timestamp=0.0,
        start_time=0.0,
        gate_name=gate_name,
        duration=duration,
        timeout=timeout,
        status="pending",
    )


@st.composite
def unique_gates_without_timeout_st(draw, min_size=1, max_size=5):
    """Generate a list of gates with unique names and no timeouts triggering."""
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    names = draw(
        st.lists(non_empty_str, min_size=count, max_size=count, unique=True)
    )
    gates = []
    for name in names:
        gate = draw(gate_without_timeout_st(name=name))
        gates.append(gate)
    return gates


def _make_minimal_mission_config(chain_config: AuthorizationChainConfig) -> MissionConfiguration:
    """Create a minimal MissionConfiguration with only authorization chain."""
    return MissionConfiguration(
        mission_id="test_mission",
        mission_name="Test Mission",
        route_graph=RouteGraphConfig(
            nodes=[
                RouteNode(
                    entity_id="node_origin",
                    name="Origin",
                    node_type="base",
                ),
                RouteNode(
                    entity_id="node_dest",
                    name="Destination",
                    node_type="base",
                ),
            ],
            edges=[
                RouteEdge(source="Origin", destination="Destination", distance_nm=100.0)
            ],
            origin="Origin",
            destination="Destination",
        ),
        aircraft=AircraftConfig(
            entity_id="aircraft_test",
            aircraft_type="C-130",
            lift_to_drag_ratio=15.0,
            specific_fuel_consumption=0.5,
            initial_fuel_weight=10000.0,
            max_fuel_capacity=15000.0,
        ),
        authorization_chain=chain_config,
        active_modules=["authorization"],
    )


def _run_authorization_chain(chain_config: AuthorizationChainConfig) -> tuple[list[SimulationEvent], float]:
    """Run an authorization chain in a SimPy environment and collect events."""
    env = simpy.Environment()
    event_bus = EventBus()
    collected_events: list[SimulationEvent] = []

    # Subscribe to all authorization events
    event_bus.subscribe(
        ChainCompletionEvent,
        lambda e: collected_events.append(e),
        "test_chain_completion",
    )
    event_bus.subscribe(
        GateCompletionEvent,
        lambda e: collected_events.append(e),
        "test_gate_completion",
    )
    event_bus.subscribe(
        GateTimeoutEvent,
        lambda e: collected_events.append(e),
        "test_gate_timeout",
    )

    module = AuthorizationModule()
    config = _make_minimal_mission_config(chain_config)
    module.initialize(env, event_bus, config)

    processes = module.create_processes(env)
    for proc in processes:
        pass  # Processes are already registered with env via env.process()

    env.run()
    final_time = env.now

    module.finalize()

    return collected_events, final_time


class TestAuthorizationChainTiming:
    """Property 8: Authorization Chain Timing.

    **Validates: Requirements 4.3, 4.6**

    For any authorization chain, sequential chains SHALL complete in total time equal
    to the sum of gate durations, and parallel chains SHALL complete in total time
    equal to the maximum gate duration.
    """

    @given(gates=unique_gates_without_timeout_st(min_size=1, max_size=5))
    @settings(max_examples=100)
    def test_sequential_chain_elapsed_time_equals_sum_of_durations(
        self, gates: list[AuthorizationGate]
    ):
        """Sequential chain total elapsed time equals sum of all gate durations."""
        chain_config = AuthorizationChainConfig(
            chain_id="seq_chain",
            chain_type="sequential",
            gates=gates,
        )

        events, final_time = _run_authorization_chain(chain_config)

        # Find the chain completion event
        chain_completions = [e for e in events if isinstance(e, ChainCompletionEvent)]
        assert len(chain_completions) == 1, "Expected exactly one ChainCompletionEvent"

        expected_elapsed = sum(g.duration for g in gates)
        actual_elapsed = chain_completions[0].elapsed_time

        # Allow small floating-point tolerance
        assert abs(actual_elapsed - expected_elapsed) < 1e-9, (
            f"Sequential chain elapsed_time={actual_elapsed} != "
            f"sum(durations)={expected_elapsed}"
        )

        # Also verify final simulation time matches
        assert abs(final_time - expected_elapsed) < 1e-9

    @given(gates=unique_gates_without_timeout_st(min_size=1, max_size=5))
    @settings(max_examples=100)
    def test_parallel_chain_elapsed_time_equals_max_duration(
        self, gates: list[AuthorizationGate]
    ):
        """Parallel chain total elapsed time equals maximum gate duration."""
        chain_config = AuthorizationChainConfig(
            chain_id="par_chain",
            chain_type="parallel",
            gates=gates,
        )

        events, final_time = _run_authorization_chain(chain_config)

        # Find the chain completion event
        chain_completions = [e for e in events if isinstance(e, ChainCompletionEvent)]
        assert len(chain_completions) == 1, "Expected exactly one ChainCompletionEvent"

        expected_elapsed = max(g.duration for g in gates)
        actual_elapsed = chain_completions[0].elapsed_time

        # Allow small floating-point tolerance
        assert abs(actual_elapsed - expected_elapsed) < 1e-9, (
            f"Parallel chain elapsed_time={actual_elapsed} != "
            f"max(durations)={expected_elapsed}"
        )

        # Also verify final simulation time matches
        assert abs(final_time - expected_elapsed) < 1e-9

    @given(gates=unique_gates_without_timeout_st(min_size=2, max_size=5))
    @settings(max_examples=100)
    def test_sequential_chain_publishes_all_gate_completions(
        self, gates: list[AuthorizationGate]
    ):
        """Sequential chain publishes a GateCompletionEvent for each gate."""
        chain_config = AuthorizationChainConfig(
            chain_id="seq_chain",
            chain_type="sequential",
            gates=gates,
        )

        events, _ = _run_authorization_chain(chain_config)

        gate_completions = [e for e in events if isinstance(e, GateCompletionEvent)]
        assert len(gate_completions) == len(gates), (
            f"Expected {len(gates)} GateCompletionEvents, got {len(gate_completions)}"
        )

        # Verify gate names match in order
        for gc, gate in zip(gate_completions, gates):
            assert gc.gate_name == gate.gate_name
            assert gc.chain_id == "seq_chain"

    @given(gates=unique_gates_without_timeout_st(min_size=2, max_size=5))
    @settings(max_examples=100)
    def test_parallel_chain_publishes_all_gate_completions(
        self, gates: list[AuthorizationGate]
    ):
        """Parallel chain publishes a GateCompletionEvent for each gate."""
        chain_config = AuthorizationChainConfig(
            chain_id="par_chain",
            chain_type="parallel",
            gates=gates,
        )

        events, _ = _run_authorization_chain(chain_config)

        gate_completions = [e for e in events if isinstance(e, GateCompletionEvent)]
        assert len(gate_completions) == len(gates), (
            f"Expected {len(gates)} GateCompletionEvents, got {len(gate_completions)}"
        )

        # Verify all gate names present (order may differ in parallel)
        completion_names = {gc.gate_name for gc in gate_completions}
        expected_names = {g.gate_name for g in gates}
        assert completion_names == expected_names


class TestAuthorizationGateTimeoutCascading:
    """Property 9: Authorization Gate Timeout Cascading.

    **Validates: Requirements 4.5, 4.6**

    For any authorization gate where the configured timeout is less than the gate
    duration, the gate SHALL transition to a failed state, all dependent gates SHALL
    be cancelled, and a timeout event SHALL be published containing the gate name
    and chain identifier.
    """

    @given(
        good_gates=unique_gates_without_timeout_st(min_size=1, max_size=3),
        timeout_gate=gate_with_timeout_st(),
        trailing_gates=unique_gates_without_timeout_st(min_size=1, max_size=3),
    )
    @settings(max_examples=100)
    def test_sequential_timeout_gate_transitions_to_failed(
        self,
        good_gates: list[AuthorizationGate],
        timeout_gate: AuthorizationGate,
        trailing_gates: list[AuthorizationGate],
    ):
        """In a sequential chain, a timed-out gate transitions to failed state."""
        # Ensure unique gate names across all gates
        all_names = [g.gate_name for g in good_gates] + [timeout_gate.gate_name] + [g.gate_name for g in trailing_gates]
        assume(len(all_names) == len(set(all_names)))

        gates = good_gates + [timeout_gate] + trailing_gates
        chain_config = AuthorizationChainConfig(
            chain_id="seq_timeout_chain",
            chain_type="sequential",
            gates=gates,
        )

        events, _ = _run_authorization_chain(chain_config)

        # A GateTimeoutEvent should be published for the timeout gate
        timeout_events = [e for e in events if isinstance(e, GateTimeoutEvent)]
        assert len(timeout_events) == 1, f"Expected 1 GateTimeoutEvent, got {len(timeout_events)}"
        assert timeout_events[0].gate_name == timeout_gate.gate_name
        assert timeout_events[0].chain_id == "seq_timeout_chain"

    @given(
        good_gates=unique_gates_without_timeout_st(min_size=0, max_size=2),
        timeout_gate=gate_with_timeout_st(),
        trailing_gates=unique_gates_without_timeout_st(min_size=1, max_size=3),
    )
    @settings(max_examples=100)
    def test_sequential_timeout_cancels_dependent_gates(
        self,
        good_gates: list[AuthorizationGate],
        timeout_gate: AuthorizationGate,
        trailing_gates: list[AuthorizationGate],
    ):
        """In a sequential chain, trailing gates after a timeout are cancelled (failed)."""
        all_names = [g.gate_name for g in good_gates] + [timeout_gate.gate_name] + [g.gate_name for g in trailing_gates]
        assume(len(all_names) == len(set(all_names)))

        gates = good_gates + [timeout_gate] + trailing_gates
        chain_config = AuthorizationChainConfig(
            chain_id="seq_cancel_chain",
            chain_type="sequential",
            gates=gates,
        )

        # Run the chain and inspect internal gate states
        env = simpy.Environment()
        event_bus = EventBus()
        collected_events: list[SimulationEvent] = []

        event_bus.subscribe(GateTimeoutEvent, lambda e: collected_events.append(e), "test_timeout")
        event_bus.subscribe(GateCompletionEvent, lambda e: collected_events.append(e), "test_completion")
        event_bus.subscribe(ChainCompletionEvent, lambda e: collected_events.append(e), "test_chain")

        module = AuthorizationModule()
        config = _make_minimal_mission_config(chain_config)
        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        # Trailing gates should be in "failed" state (cancelled)
        for trailing in trailing_gates:
            assert module._gate_states[trailing.gate_name] == "failed", (
                f"Trailing gate '{trailing.gate_name}' should be 'failed' but is "
                f"'{module._gate_states[trailing.gate_name]}'"
            )

        # No chain completion event should be published
        chain_completions = [e for e in collected_events if isinstance(e, ChainCompletionEvent)]
        assert len(chain_completions) == 0, "No ChainCompletionEvent should be published when a gate times out"

        module.finalize()

    @given(
        good_gates=unique_gates_without_timeout_st(min_size=1, max_size=2),
        timeout_gate=gate_with_timeout_st(),
        trailing_gates=unique_gates_without_timeout_st(min_size=1, max_size=2),
    )
    @settings(max_examples=100)
    def test_sequential_timeout_event_contains_gate_name_and_chain_id(
        self,
        good_gates: list[AuthorizationGate],
        timeout_gate: AuthorizationGate,
        trailing_gates: list[AuthorizationGate],
    ):
        """GateTimeoutEvent contains the gate name and chain identifier."""
        all_names = [g.gate_name for g in good_gates] + [timeout_gate.gate_name] + [g.gate_name for g in trailing_gates]
        assume(len(all_names) == len(set(all_names)))

        chain_id = "timeout_event_chain"
        gates = good_gates + [timeout_gate] + trailing_gates
        chain_config = AuthorizationChainConfig(
            chain_id=chain_id,
            chain_type="sequential",
            gates=gates,
        )

        events, _ = _run_authorization_chain(chain_config)

        timeout_events = [e for e in events if isinstance(e, GateTimeoutEvent)]
        assert len(timeout_events) == 1
        assert timeout_events[0].gate_name == timeout_gate.gate_name
        assert timeout_events[0].chain_id == chain_id

    @given(timeout_gate=gate_with_timeout_st())
    @settings(max_examples=100)
    def test_parallel_timeout_gate_transitions_to_failed(
        self, timeout_gate: AuthorizationGate
    ):
        """In a parallel chain, a timed-out gate transitions to failed state."""
        # Create a parallel chain with the timeout gate and one good gate
        good_gate_name = timeout_gate.gate_name + "_good"
        good_gate = AuthorizationGate(
            entity_id=f"gate_{good_gate_name}",
            timestamp=0.0,
            start_time=0.0,
            gate_name=good_gate_name,
            duration=1.0,
            timeout=None,
            status="pending",
        )

        chain_config = AuthorizationChainConfig(
            chain_id="par_timeout_chain",
            chain_type="parallel",
            gates=[good_gate, timeout_gate],
        )

        # Run and check state
        env = simpy.Environment()
        event_bus = EventBus()
        collected_events: list[SimulationEvent] = []

        event_bus.subscribe(GateTimeoutEvent, lambda e: collected_events.append(e), "test_timeout")
        event_bus.subscribe(GateCompletionEvent, lambda e: collected_events.append(e), "test_completion")
        event_bus.subscribe(ChainCompletionEvent, lambda e: collected_events.append(e), "test_chain")

        module = AuthorizationModule()
        config = _make_minimal_mission_config(chain_config)
        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        # The timeout gate should be in failed state
        assert module._gate_states[timeout_gate.gate_name] == "failed"

        # A timeout event should be published
        timeout_events = [e for e in collected_events if isinstance(e, GateTimeoutEvent)]
        assert len(timeout_events) == 1
        assert timeout_events[0].gate_name == timeout_gate.gate_name
        assert timeout_events[0].chain_id == "par_timeout_chain"

        module.finalize()

    @given(
        good_gates=unique_gates_without_timeout_st(min_size=1, max_size=2),
        timeout_gate=gate_with_timeout_st(),
    )
    @settings(max_examples=100)
    def test_parallel_timeout_prevents_chain_completion(
        self,
        good_gates: list[AuthorizationGate],
        timeout_gate: AuthorizationGate,
    ):
        """In a parallel chain, if any gate times out, no chain completion is published."""
        all_names = [g.gate_name for g in good_gates] + [timeout_gate.gate_name]
        assume(len(all_names) == len(set(all_names)))

        gates = good_gates + [timeout_gate]
        chain_config = AuthorizationChainConfig(
            chain_id="par_no_completion_chain",
            chain_type="parallel",
            gates=gates,
        )

        events, _ = _run_authorization_chain(chain_config)

        # No chain completion event since one gate failed
        chain_completions = [e for e in events if isinstance(e, ChainCompletionEvent)]
        assert len(chain_completions) == 0, (
            "No ChainCompletionEvent should be published when a gate times out in parallel chain"
        )
