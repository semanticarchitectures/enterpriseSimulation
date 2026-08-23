"""Unit tests for the AuthorizationModule.

Tests sequential and parallel chain execution, gate timeouts,
event publication, and module lifecycle.
"""

from __future__ import annotations

from typing import Optional

import simpy
import pytest

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.authorization import AuthorizationChainConfig, AuthorizationGate
from enterprise_sim.models.events import (
    ChainCompletionEvent,
    GateCompletionEvent,
    GateTimeoutEvent,
)
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteGraphConfig, RouteNode, RouteEdge
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.modules.authorization import AuthorizationModule


def _make_mission_config(chain_config: Optional[AuthorizationChainConfig]) -> MissionConfiguration:
    """Build a minimal MissionConfiguration with the given authorization chain."""
    return MissionConfiguration(
        mission_id="test-mission",
        mission_name="Test Mission",
        route_graph=RouteGraphConfig(
            nodes=[
                RouteNode(entity_id="n1", name="Origin", node_type="base"),
                RouteNode(entity_id="n2", name="Dest", node_type="base"),
            ],
            edges=[RouteEdge(source="Origin", destination="Dest", distance_nm=100.0)],
            origin="Origin",
            destination="Dest",
        ),
        aircraft=AircraftConfig(
            entity_id="ac1",
            aircraft_type="C-17",
            lift_to_drag_ratio=15.0,
            specific_fuel_consumption=0.6,
            initial_fuel_weight=50000.0,
            max_fuel_capacity=60000.0,
        ),
        authorization_chain=chain_config,
        active_modules=["authorization"],
    )


class TestAuthorizationModuleLifecycle:
    """Tests for module lifecycle methods."""

    def test_module_type_is_authorization(self):
        module = AuthorizationModule()
        assert module.module_type == "authorization"

    def test_dependencies_is_empty(self):
        module = AuthorizationModule()
        assert module.dependencies == set()

    def test_initialize_with_no_chain(self):
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(None)

        module.initialize(env, event_bus, config)
        assert module._gate_states == {}

    def test_initialize_sets_up_gate_states(self):
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        chain = AuthorizationChainConfig(
            chain_id="chain1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Title10", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="DIPCLEAR", duration=3.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        assert module._gate_states == {"Title10": "pending", "DIPCLEAR": "pending"}

    def test_create_processes_returns_empty_when_no_chain(self):
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(None)

        module.initialize(env, event_bus, config)
        processes = module.create_processes(env)
        assert processes == []

    def test_finalize_clears_state(self):
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        chain = AuthorizationChainConfig(
            chain_id="chain1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Title10", duration=5.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.finalize()

        assert module._env is None
        assert module._event_bus is None
        assert module._chain_config is None
        assert module._gate_states == {}


class TestSequentialChain:
    """Tests for sequential chain execution."""

    def test_sequential_chain_total_time_is_sum_of_durations(self):
        """Sequential chains complete in sum of gate durations."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        events_collected = []

        event_bus.subscribe(ChainCompletionEvent, lambda e: events_collected.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="seq1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Gate1", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="Gate2", duration=3.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g3", gate_name="Gate3", duration=2.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        processes = module.create_processes(env)
        env.run()

        assert len(events_collected) == 1
        assert events_collected[0].elapsed_time == 10.0
        assert events_collected[0].chain_id == "seq1"

    def test_sequential_chain_publishes_gate_completions_in_order(self):
        """Each gate publishes a completion event at the correct time."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        gate_events = []

        event_bus.subscribe(GateCompletionEvent, lambda e: gate_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="seq1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Gate1", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="Gate2", duration=3.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(gate_events) == 2
        assert gate_events[0].gate_name == "Gate1"
        assert gate_events[0].timestamp == 5.0
        assert gate_events[1].gate_name == "Gate2"
        assert gate_events[1].timestamp == 8.0

    def test_sequential_chain_timeout_cancels_remaining_gates(self):
        """Timeout in sequential chain cancels all remaining gates."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        timeout_events = []
        completion_events = []
        chain_events = []

        event_bus.subscribe(GateTimeoutEvent, lambda e: timeout_events.append(e), "test")
        event_bus.subscribe(GateCompletionEvent, lambda e: completion_events.append(e), "test")
        event_bus.subscribe(ChainCompletionEvent, lambda e: chain_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="seq1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Gate1", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2",
                    gate_name="Gate2",
                    duration=10.0,
                    timeout=3.0,
                    timestamp=0,
                    start_time=0,
                ),
                AuthorizationGate(
                    entity_id="g3", gate_name="Gate3", duration=2.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        # Gate1 completes, Gate2 times out, Gate3 is cancelled
        assert len(completion_events) == 1
        assert completion_events[0].gate_name == "Gate1"

        assert len(timeout_events) == 1
        assert timeout_events[0].gate_name == "Gate2"
        assert timeout_events[0].timestamp == 8.0  # 5.0 + 3.0

        # No chain completion event because chain failed
        assert len(chain_events) == 0

        # Gate states reflect outcomes
        assert module._gate_states["Gate1"] == "completed"
        assert module._gate_states["Gate2"] == "failed"
        assert module._gate_states["Gate3"] == "failed"

    def test_sequential_single_gate(self):
        """A single-gate sequential chain completes correctly."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        chain_events = []

        event_bus.subscribe(ChainCompletionEvent, lambda e: chain_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="single",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="OnlyGate", duration=7.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(chain_events) == 1
        assert chain_events[0].elapsed_time == 7.0


class TestParallelChain:
    """Tests for parallel chain execution."""

    def test_parallel_chain_total_time_is_max_duration(self):
        """Parallel chains complete in time equal to the max gate duration."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        chain_events = []

        event_bus.subscribe(ChainCompletionEvent, lambda e: chain_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="par1",
            chain_type="parallel",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Gate1", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="Gate2", duration=8.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g3", gate_name="Gate3", duration=3.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(chain_events) == 1
        assert chain_events[0].elapsed_time == 8.0
        assert chain_events[0].chain_id == "par1"

    def test_parallel_chain_publishes_all_gate_completions(self):
        """All gates publish completion events in a parallel chain."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        gate_events = []

        event_bus.subscribe(GateCompletionEvent, lambda e: gate_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="par1",
            chain_type="parallel",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Gate1", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="Gate2", duration=3.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(gate_events) == 2
        gate_names = {e.gate_name for e in gate_events}
        assert gate_names == {"Gate1", "Gate2"}

    def test_parallel_chain_timeout_marks_gate_failed_others_continue(self):
        """In parallel chain, timed-out gate is failed but others continue."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        timeout_events = []
        completion_events = []
        chain_events = []

        event_bus.subscribe(GateTimeoutEvent, lambda e: timeout_events.append(e), "test")
        event_bus.subscribe(GateCompletionEvent, lambda e: completion_events.append(e), "test")
        event_bus.subscribe(ChainCompletionEvent, lambda e: chain_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="par1",
            chain_type="parallel",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Gate1", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2",
                    gate_name="Gate2",
                    duration=10.0,
                    timeout=2.0,
                    timestamp=0,
                    start_time=0,
                ),
                AuthorizationGate(
                    entity_id="g3", gate_name="Gate3", duration=3.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        # Gate2 timed out, Gate1 and Gate3 completed
        assert len(timeout_events) == 1
        assert timeout_events[0].gate_name == "Gate2"
        assert timeout_events[0].timestamp == 2.0

        assert len(completion_events) == 2
        completed_names = {e.gate_name for e in completion_events}
        assert completed_names == {"Gate1", "Gate3"}

        # No chain completion because Gate2 failed
        assert len(chain_events) == 0

        # Gate states
        assert module._gate_states["Gate1"] == "completed"
        assert module._gate_states["Gate2"] == "failed"
        assert module._gate_states["Gate3"] == "completed"

    def test_parallel_chain_all_gates_succeed(self):
        """All gates succeeding in parallel publishes chain completion."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        chain_events = []

        event_bus.subscribe(ChainCompletionEvent, lambda e: chain_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="par1",
            chain_type="parallel",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="Gate1", duration=4.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="Gate2", duration=4.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(chain_events) == 1
        assert chain_events[0].elapsed_time == 4.0


class TestGateTimeout:
    """Tests specifically for timeout behavior."""

    def test_timeout_equal_to_duration_gate_completes(self):
        """When timeout == duration, gate should still complete (timeout not less than duration)."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        gate_events = []
        timeout_events = []

        event_bus.subscribe(GateCompletionEvent, lambda e: gate_events.append(e), "test")
        event_bus.subscribe(GateTimeoutEvent, lambda e: timeout_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="t1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1",
                    gate_name="Gate1",
                    duration=5.0,
                    timeout=5.0,
                    timestamp=0,
                    start_time=0,
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        # timeout is not less than duration, so gate completes
        assert len(gate_events) == 1
        assert len(timeout_events) == 0

    def test_timeout_greater_than_duration_gate_completes(self):
        """When timeout > duration, gate completes normally."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        gate_events = []
        timeout_events = []

        event_bus.subscribe(GateCompletionEvent, lambda e: gate_events.append(e), "test")
        event_bus.subscribe(GateTimeoutEvent, lambda e: timeout_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="t1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1",
                    gate_name="Gate1",
                    duration=5.0,
                    timeout=10.0,
                    timestamp=0,
                    start_time=0,
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(gate_events) == 1
        assert len(timeout_events) == 0

    def test_no_timeout_configured_gate_completes(self):
        """When timeout is None, gate completes normally."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        gate_events = []

        event_bus.subscribe(GateCompletionEvent, lambda e: gate_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="t1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1",
                    gate_name="Gate1",
                    duration=5.0,
                    timeout=None,
                    timestamp=0,
                    start_time=0,
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(gate_events) == 1
        assert gate_events[0].gate_name == "Gate1"

    def test_first_gate_timeout_cancels_all_remaining_sequential(self):
        """First gate timing out should cancel all subsequent gates."""
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        timeout_events = []

        event_bus.subscribe(GateTimeoutEvent, lambda e: timeout_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="t1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1",
                    gate_name="Gate1",
                    duration=10.0,
                    timeout=1.0,
                    timestamp=0,
                    start_time=0,
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="Gate2", duration=5.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g3", gate_name="Gate3", duration=3.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        assert len(timeout_events) == 1
        assert timeout_events[0].gate_name == "Gate1"

        assert module._gate_states["Gate1"] == "failed"
        assert module._gate_states["Gate2"] == "failed"
        assert module._gate_states["Gate3"] == "failed"


class TestEventPayloads:
    """Tests for correct event payload contents."""

    def test_gate_completion_event_has_correct_fields(self):
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        gate_events = []

        event_bus.subscribe(GateCompletionEvent, lambda e: gate_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="chain_abc",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="MyGate", duration=3.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        event = gate_events[0]
        assert event.gate_name == "MyGate"
        assert event.chain_id == "chain_abc"
        assert event.source_module == "authorization"
        assert event.timestamp == 3.0

    def test_chain_completion_event_has_correct_elapsed_time(self):
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        chain_events = []

        event_bus.subscribe(ChainCompletionEvent, lambda e: chain_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="chain_xyz",
            chain_type="parallel",
            gates=[
                AuthorizationGate(
                    entity_id="g1", gate_name="G1", duration=2.0, timestamp=0, start_time=0
                ),
                AuthorizationGate(
                    entity_id="g2", gate_name="G2", duration=7.0, timestamp=0, start_time=0
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        event = chain_events[0]
        assert event.chain_id == "chain_xyz"
        assert event.elapsed_time == 7.0
        assert event.source_module == "authorization"

    def test_gate_timeout_event_has_correct_fields(self):
        module = AuthorizationModule()
        env = simpy.Environment()
        event_bus = EventBus()
        timeout_events = []

        event_bus.subscribe(GateTimeoutEvent, lambda e: timeout_events.append(e), "test")

        chain = AuthorizationChainConfig(
            chain_id="chain_t",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="g1",
                    gate_name="SlowGate",
                    duration=20.0,
                    timeout=4.0,
                    timestamp=0,
                    start_time=0,
                ),
            ],
        )
        config = _make_mission_config(chain)

        module.initialize(env, event_bus, config)
        module.create_processes(env)
        env.run()

        event = timeout_events[0]
        assert event.gate_name == "SlowGate"
        assert event.chain_id == "chain_t"
        assert event.source_module == "authorization"
        assert event.timestamp == 4.0
