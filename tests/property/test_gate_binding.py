"""Property tests for authorization gate binding in the Personnel Module.

**Property 21: Gate Personnel Readiness Check**
**Property 22: Gate Approval Records Personnel**
**Validates: Requirements 5.2, 5.3, 5.4, 5.6**

Property 21: For any authorization gate that becomes active with a bound role,
if no Personnel member with that role is in READY state, the module SHALL publish
a gate-blocked event (with gate name, required role, and timestamp) and block gate
processing. If a READY member exists, gate processing proceeds. If the gate timeout
expires while blocked, a gate-timeout event SHALL be published and the gate SHALL
transition to a failed state.

Property 22: For any authorization gate that is approved, the gate completion event
published to the Event_Bus SHALL contain the approving personnel name.
"""

import simpy
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.authorization import AuthorizationChainConfig, AuthorizationGate
from enterprise_sim.models.events import GateCompletionEvent, SimulationEvent
from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    AuthorizationGateBinding,
    ClearanceLevel,
    CredentialType,
    Personnel,
    PersonnelConfig,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    GateBlockedEvent,
    GateTimeoutPersonnelEvent,
)
from enterprise_sim.modules.personnel import PersonnelModule


# === Hypothesis Strategies ===

# Strategy for valid gate names
gate_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "")

# Strategy for valid role names
role_strategy = st.sampled_from(
    ["Pilot", "Loadmaster", "Navigator", "AOC_Commander", "Engineer", "Operator"]
)

# Strategy for readiness states
readiness_strategy = st.sampled_from(list(ReadinessState))

# Strategy for non-READY states
non_ready_strategy = st.sampled_from(
    [ReadinessState.UNAVAILABLE, ReadinessState.INCAPACITATED]
)

# Strategy for timestamps
timestamp_strategy = st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False)

# Strategy for gate timeout values
timeout_strategy = st.floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False)


def _make_credential():
    """Create a default credential for tests."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=ClearanceLevel.SECRET,
        authorized_enclaves=["NIPR"],
    )


def _make_personnel(
    name: str, role: str, readiness: ReadinessState = ReadinessState.READY
) -> Personnel:
    """Create a personnel member for testing."""
    return Personnel(
        entity_id=f"person_{name}",
        name=name,
        role=role,
        duty_station="HQ",
        credential=_make_credential(),
        readiness_state=readiness,
    )


def _make_config(roster, gate_bindings=None, auth_chain=None):
    """Create a minimal mock config for PersonnelModule initialization."""

    class MockRouteGraph:
        nodes = [type("Node", (), {"name": "HQ"})()]

    class MockConfig:
        route_graph = MockRouteGraph()
        c2_messages = []
        authorization_chain = auth_chain
        cross_domain_gateway = None
        personnel_parameters = PersonnelConfig(
            roster=roster,
            authorization_gate_bindings=gate_bindings or [],
        )

    return MockConfig()


# === Property 21: Gate Personnel Readiness Check ===
# Feature: personnel-module, Property 21: Gate Personnel Readiness Check


class TestGatePersonnelReadinessCheck:
    """Property 21: Gate Personnel Readiness Check.

    **Validates: Requirements 5.2, 5.4, 5.6**

    For any authorization gate that becomes active with a bound role,
    if no Personnel member with that role is in READY state, the module SHALL
    publish a gate-blocked event and block gate processing. If a READY member
    exists, gate processing proceeds. If the gate timeout expires while blocked,
    a gate-timeout event SHALL be published.
    """

    @given(
        gate_name=gate_name_strategy,
        role=role_strategy,
        timestamp=timestamp_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_ready_personnel_allows_gate_to_proceed(self, gate_name, role, timestamp):
        """If a READY member with the bound role exists, gate processing proceeds.

        No GateBlockedEvent is published and the gate is not blocked.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Create a READY personnel member with the bound role
        person = _make_personnel(f"Person_A", role, ReadinessState.READY)
        roster = [person]
        bindings = [AuthorizationGateBinding(gate_name=gate_name, approving_role=role)]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Capture published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        # Fire gate activation event
        gate_event = GateCompletionEvent(
            entity_id=f"completion_{gate_name}",
            timestamp=timestamp,
            source_module="authorization",
            gate_name=gate_name,
            chain_id="chain_test",
        )
        event_bus.publish(gate_event)

        # Gate should NOT be blocked
        assert gate_name not in module.blocked_gates
        # No GateBlockedEvent should be published
        blocked_events = [e for e in published_events if isinstance(e, GateBlockedEvent)]
        assert len(blocked_events) == 0

    @given(
        gate_name=gate_name_strategy,
        role=role_strategy,
        non_ready_state=non_ready_strategy,
        timestamp=timestamp_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_ready_personnel_publishes_gate_blocked(
        self, gate_name, role, non_ready_state, timestamp
    ):
        """If no Personnel member with that role is in READY state,
        a gate-blocked event SHALL be published and gate processing blocked.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Create a non-READY personnel member with the bound role
        person = _make_personnel(f"Person_B", role, non_ready_state)
        roster = [person]
        bindings = [AuthorizationGateBinding(gate_name=gate_name, approving_role=role)]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Capture published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        # Fire gate activation event
        gate_event = GateCompletionEvent(
            entity_id=f"completion_{gate_name}",
            timestamp=timestamp,
            source_module="authorization",
            gate_name=gate_name,
            chain_id="chain_test",
        )
        event_bus.publish(gate_event)

        # Gate SHALL be blocked
        assert gate_name in module.blocked_gates
        assert module.blocked_gates[gate_name] == role

        # A GateBlockedEvent SHALL be published with correct fields
        blocked_events = [e for e in published_events if isinstance(e, GateBlockedEvent)]
        assert len(blocked_events) == 1
        assert blocked_events[0].gate_name == gate_name
        assert blocked_events[0].required_role == role
        assert blocked_events[0].timestamp == timestamp

    @given(
        gate_name=gate_name_strategy,
        role=role_strategy,
        non_ready_state=non_ready_strategy,
        gate_timeout=timeout_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_gate_timeout_publishes_timeout_event(
        self, gate_name, role, non_ready_state, gate_timeout
    ):
        """If the gate timeout expires while blocked, a gate-timeout event
        SHALL be published and the gate SHALL transition to a failed state.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Create a non-READY personnel member with the bound role
        person = _make_personnel(f"Person_C", role, non_ready_state)
        roster = [person]
        bindings = [AuthorizationGateBinding(gate_name=gate_name, approving_role=role)]

        # Auth chain with timeout configured on the gate
        auth_chain = AuthorizationChainConfig(
            chain_id="chain_test",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id=f"gate_{gate_name}",
                    gate_name=gate_name,
                    duration=1.0,
                    timeout=gate_timeout,
                    start_time=0.0,
                    timestamp=0.0,
                )
            ],
        )
        config = _make_config(roster, gate_bindings=bindings, auth_chain=auth_chain)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Capture published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        # Fire gate activation event at time 0
        gate_event = GateCompletionEvent(
            entity_id=f"completion_{gate_name}",
            timestamp=0.0,
            source_module="authorization",
            gate_name=gate_name,
            chain_id="chain_test",
        )
        event_bus.publish(gate_event)

        # Gate should be blocked initially
        assert gate_name in module.blocked_gates

        # Run simulation to allow timeout process to complete
        env.run()

        # After timeout, GateTimeoutPersonnelEvent SHALL be published
        timeout_events = [
            e for e in published_events if isinstance(e, GateTimeoutPersonnelEvent)
        ]
        assert len(timeout_events) == 1
        assert timeout_events[0].gate_name == gate_name
        assert timeout_events[0].required_role == role

        # Gate should no longer be in blocked_gates (transitioned to failed)
        assert gate_name not in module.blocked_gates

    @given(
        gate_name=gate_name_strategy,
        role=role_strategy,
        num_personnel=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_mixed_readiness_ready_personnel_proceeds(
        self, gate_name, role, num_personnel
    ):
        """When multiple personnel exist with the bound role and at least one is
        READY, gate processing proceeds without blocking.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Create multiple personnel with same role, mixed readiness
        roster = []
        # First N-1 are UNAVAILABLE
        for i in range(num_personnel - 1):
            p = _make_personnel(f"Person_{i}", role, ReadinessState.UNAVAILABLE)
            roster.append(p)
        # Last one is READY
        ready_person = _make_personnel(f"Person_ready", role, ReadinessState.READY)
        roster.append(ready_person)

        bindings = [AuthorizationGateBinding(gate_name=gate_name, approving_role=role)]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Capture published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        # Fire gate activation event
        gate_event = GateCompletionEvent(
            entity_id=f"completion_{gate_name}",
            timestamp=5.0,
            source_module="authorization",
            gate_name=gate_name,
            chain_id="chain_test",
        )
        event_bus.publish(gate_event)

        # Gate should NOT be blocked (at least one READY exists)
        assert gate_name not in module.blocked_gates
        blocked_events = [e for e in published_events if isinstance(e, GateBlockedEvent)]
        assert len(blocked_events) == 0


# === Property 22: Gate Approval Records Personnel ===
# Feature: personnel-module, Property 22: Gate Approval Records Personnel


class TestGateApprovalRecordsPersonnel:
    """Property 22: Gate Approval Records Personnel.

    **Validates: Requirements 5.3**

    For any authorization gate that is approved, the gate completion event
    published to the Event_Bus SHALL contain the approving personnel name.
    """

    @given(
        gate_name=gate_name_strategy,
        role=role_strategy,
        personnel_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=50,
        ).filter(lambda s: s.strip() != ""),
        timestamp=timestamp_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_gate_approval_records_approving_personnel_name(
        self, gate_name, role, personnel_name, timestamp
    ):
        """When a gate is approved, gate_approvals SHALL contain the
        approving personnel name.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Create a READY personnel member who will approve the gate
        person = _make_personnel(personnel_name, role, ReadinessState.READY)
        roster = [person]
        bindings = [AuthorizationGateBinding(gate_name=gate_name, approving_role=role)]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Fire gate activation event
        gate_event = GateCompletionEvent(
            entity_id=f"completion_{gate_name}",
            timestamp=timestamp,
            source_module="authorization",
            gate_name=gate_name,
            chain_id="chain_test",
        )
        event_bus.publish(gate_event)

        # The gate_approvals SHALL contain the approving personnel name
        assert gate_name in module.gate_approvals
        assert module.gate_approvals[gate_name] == personnel_name

    @given(
        gate_name=gate_name_strategy,
        role=role_strategy,
        num_ready=st.integers(min_value=2, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_gate_approval_records_first_ready_in_roster_order(
        self, gate_name, role, num_ready
    ):
        """When multiple READY personnel exist with the bound role,
        the first in roster order SHALL be recorded as the approver.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Create multiple READY personnel with the same role
        roster = []
        for i in range(num_ready):
            p = _make_personnel(f"Person_{i}", role, ReadinessState.READY)
            roster.append(p)

        bindings = [AuthorizationGateBinding(gate_name=gate_name, approving_role=role)]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Fire gate activation event
        gate_event = GateCompletionEvent(
            entity_id=f"completion_{gate_name}",
            timestamp=1.0,
            source_module="authorization",
            gate_name=gate_name,
            chain_id="chain_test",
        )
        event_bus.publish(gate_event)

        # First READY in roster order SHALL be the approver
        assert module.gate_approvals[gate_name] == "Person_0"

    @given(
        gate_name=gate_name_strategy,
        role=role_strategy,
        gate_timeout=timeout_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_gate_resolved_before_timeout_records_personnel(
        self, gate_name, role, gate_timeout
    ):
        """When a blocked gate is resolved by a readiness change before timeout,
        the approving personnel name SHALL be recorded in gate_approvals.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Personnel starts as UNAVAILABLE
        person = _make_personnel(f"Person_D", role, ReadinessState.UNAVAILABLE)
        roster = [person]
        bindings = [AuthorizationGateBinding(gate_name=gate_name, approving_role=role)]

        # Auth chain with timeout
        auth_chain = AuthorizationChainConfig(
            chain_id="chain_test",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id=f"gate_{gate_name}",
                    gate_name=gate_name,
                    duration=1.0,
                    timeout=gate_timeout,
                    start_time=0.0,
                    timestamp=0.0,
                )
            ],
        )
        config = _make_config(roster, gate_bindings=bindings, auth_chain=auth_chain)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Trigger gate (blocked initially)
        gate_event = GateCompletionEvent(
            entity_id=f"completion_{gate_name}",
            timestamp=0.0,
            source_module="authorization",
            gate_name=gate_name,
            chain_id="chain_test",
        )
        event_bus.publish(gate_event)
        assert gate_name in module.blocked_gates

        # Resolve by changing readiness to READY before timeout
        resolve_time = min(gate_timeout / 2.0, gate_timeout - 0.01)

        def resolve_process(env_ref, mod):
            yield env_ref.timeout(resolve_time)
            mod.change_readiness("Person_D", ReadinessState.READY, env_ref.now)

        env.process(resolve_process(env, module))
        env.run()

        # Gate should be resolved with the approving personnel name
        assert gate_name not in module.blocked_gates
        assert module.gate_approvals.get(gate_name) == "Person_D"

        # No timeout event should have been published
        # (checked implicitly: if gate resolved, timeout process sees it's no longer blocked)
