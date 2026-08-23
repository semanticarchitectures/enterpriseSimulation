"""Unit tests for Personnel module authorization gate binding logic.

Tests the _handle_gate_activation handler and gate blocking/timeout behavior
for requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6.
"""

import simpy

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


class TestGateActivationReadyPersonnel:
    """Test that a READY personnel with the bound role allows gate to proceed."""

    def test_ready_personnel_approves_gate(self):
        """Req 5.2: READY personnel with bound role allows gate to proceed."""
        env = simpy.Environment()
        event_bus = EventBus()

        commander = _make_personnel("Col_Jones", "AOC_Commander")
        roster = [commander]
        bindings = [AuthorizationGateBinding(gate_name="EXORD_gate", approving_role="AOC_Commander")]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Simulate a gate completion event
        gate_event = GateCompletionEvent(
            entity_id="completion_EXORD_gate",
            timestamp=5.0,
            source_module="authorization",
            gate_name="EXORD_gate",
            chain_id="chain_1",
        )
        event_bus.publish(gate_event)

        # Verify approving personnel is recorded
        assert module.gate_approvals.get("EXORD_gate") == "Col_Jones"
        assert "EXORD_gate" not in module.blocked_gates

    def test_records_first_ready_in_roster_order(self):
        """Req 5.2: First READY personnel in roster order is selected."""
        env = simpy.Environment()
        event_bus = EventBus()

        commander1 = _make_personnel("Col_Jones", "AOC_Commander")
        commander2 = _make_personnel("Col_Smith", "AOC_Commander")
        roster = [commander1, commander2]
        bindings = [AuthorizationGateBinding(gate_name="EXORD_gate", approving_role="AOC_Commander")]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        gate_event = GateCompletionEvent(
            entity_id="completion_EXORD_gate",
            timestamp=5.0,
            source_module="authorization",
            gate_name="EXORD_gate",
            chain_id="chain_1",
        )
        event_bus.publish(gate_event)

        # First in roster order should be selected
        assert module.gate_approvals.get("EXORD_gate") == "Col_Jones"


class TestGateActivationNoReadyPersonnel:
    """Test that missing READY personnel blocks the gate."""

    def test_no_ready_personnel_publishes_gate_blocked(self):
        """Req 5.4: No READY personnel publishes GateBlockedEvent."""
        env = simpy.Environment()
        event_bus = EventBus()

        commander = _make_personnel(
            "Col_Jones", "AOC_Commander", readiness=ReadinessState.UNAVAILABLE
        )
        roster = [commander]
        bindings = [AuthorizationGateBinding(gate_name="EXORD_gate", approving_role="AOC_Commander")]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Capture published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        gate_event = GateCompletionEvent(
            entity_id="completion_EXORD_gate",
            timestamp=5.0,
            source_module="authorization",
            gate_name="EXORD_gate",
            chain_id="chain_1",
        )
        event_bus.publish(gate_event)

        # Should have published a GateBlockedEvent
        blocked_events = [e for e in published_events if isinstance(e, GateBlockedEvent)]
        assert len(blocked_events) == 1
        assert blocked_events[0].gate_name == "EXORD_gate"
        assert blocked_events[0].required_role == "AOC_Commander"
        assert blocked_events[0].timestamp == 5.0

        # Gate should be tracked as blocked
        assert module.blocked_gates.get("EXORD_gate") == "AOC_Commander"

    def test_no_binding_for_gate_does_nothing(self):
        """If no binding exists for the gate, handler does nothing."""
        env = simpy.Environment()
        event_bus = EventBus()

        commander = _make_personnel("Col_Jones", "AOC_Commander")
        roster = [commander]
        config = _make_config(roster, gate_bindings=[])

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        gate_event = GateCompletionEvent(
            entity_id="completion_unknown_gate",
            timestamp=5.0,
            source_module="authorization",
            gate_name="unknown_gate",
            chain_id="chain_1",
        )
        event_bus.publish(gate_event)

        # No events should be published (no binding, nothing to do)
        blocked_events = [e for e in published_events if isinstance(e, GateBlockedEvent)]
        assert len(blocked_events) == 0
        assert len(module.gate_approvals) == 0


class TestGateTimeout:
    """Test gate timeout scenarios."""

    def test_gate_timeout_publishes_timeout_event(self):
        """Req 5.6: Gate timeout publishes GateTimeoutPersonnelEvent."""
        env = simpy.Environment()
        event_bus = EventBus()

        commander = _make_personnel(
            "Col_Jones", "AOC_Commander", readiness=ReadinessState.UNAVAILABLE
        )
        roster = [commander]
        bindings = [AuthorizationGateBinding(gate_name="EXORD_gate", approving_role="AOC_Commander")]

        # Auth chain with timeout configured on the gate
        auth_chain = AuthorizationChainConfig(
            chain_id="chain_1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="gate_exord",
                    gate_name="EXORD_gate",
                    duration=5.0,
                    timeout=10.0,
                    start_time=0.0,
                    timestamp=0.0,
                )
            ],
        )
        config = _make_config(roster, gate_bindings=bindings, auth_chain=auth_chain)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        # Trigger the gate activation (personnel is UNAVAILABLE)
        gate_event = GateCompletionEvent(
            entity_id="completion_EXORD_gate",
            timestamp=0.0,
            source_module="authorization",
            gate_name="EXORD_gate",
            chain_id="chain_1",
        )
        event_bus.publish(gate_event)

        # Gate should be blocked
        assert "EXORD_gate" in module.blocked_gates

        # Run the simulation to trigger the timeout
        env.run()

        # Should have published GateTimeoutPersonnelEvent
        timeout_events = [e for e in published_events if isinstance(e, GateTimeoutPersonnelEvent)]
        assert len(timeout_events) == 1
        assert timeout_events[0].gate_name == "EXORD_gate"
        assert timeout_events[0].required_role == "AOC_Commander"

        # Gate should no longer be blocked (it timed out)
        assert "EXORD_gate" not in module.blocked_gates

    def test_gate_resolved_before_timeout_no_timeout_event(self):
        """Req 5.4: Gate resolved by readiness change before timeout."""
        env = simpy.Environment()
        event_bus = EventBus()

        commander = _make_personnel(
            "Col_Jones", "AOC_Commander", readiness=ReadinessState.UNAVAILABLE
        )
        roster = [commander]
        bindings = [AuthorizationGateBinding(gate_name="EXORD_gate", approving_role="AOC_Commander")]

        auth_chain = AuthorizationChainConfig(
            chain_id="chain_1",
            chain_type="sequential",
            gates=[
                AuthorizationGate(
                    entity_id="gate_exord",
                    gate_name="EXORD_gate",
                    duration=5.0,
                    timeout=10.0,
                    start_time=0.0,
                    timestamp=0.0,
                )
            ],
        )
        config = _make_config(roster, gate_bindings=bindings, auth_chain=auth_chain)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(lambda e: published_events.append(e), "test_collector")

        # Trigger gate activation — blocked
        gate_event = GateCompletionEvent(
            entity_id="completion_EXORD_gate",
            timestamp=0.0,
            source_module="authorization",
            gate_name="EXORD_gate",
            chain_id="chain_1",
        )
        event_bus.publish(gate_event)
        assert "EXORD_gate" in module.blocked_gates

        # Resolve by changing readiness to READY before timeout
        def resolve_at_time_5(env, module):
            yield env.timeout(5.0)
            module.change_readiness("Col_Jones", ReadinessState.READY, env.now)

        env.process(resolve_at_time_5(env, module))
        env.run()

        # Gate should be resolved, not timed out
        assert "EXORD_gate" not in module.blocked_gates
        assert module.gate_approvals.get("EXORD_gate") == "Col_Jones"

        # Should NOT have a timeout event
        timeout_events = [e for e in published_events if isinstance(e, GateTimeoutPersonnelEvent)]
        assert len(timeout_events) == 0


class TestResolveBlockedGates:
    """Test that readiness changes resolve blocked gates."""

    def test_readiness_change_resolves_blocked_gate(self):
        """Req 5.4: Personnel becoming READY resolves blocked gate."""
        env = simpy.Environment()
        event_bus = EventBus()

        commander = _make_personnel(
            "Col_Jones", "AOC_Commander", readiness=ReadinessState.UNAVAILABLE
        )
        roster = [commander]
        bindings = [AuthorizationGateBinding(gate_name="EXORD_gate", approving_role="AOC_Commander")]
        config = _make_config(roster, gate_bindings=bindings)

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Trigger gate (blocked)
        gate_event = GateCompletionEvent(
            entity_id="completion_EXORD_gate",
            timestamp=0.0,
            source_module="authorization",
            gate_name="EXORD_gate",
            chain_id="chain_1",
        )
        event_bus.publish(gate_event)
        assert "EXORD_gate" in module.blocked_gates

        # Change readiness to READY
        module.change_readiness("Col_Jones", ReadinessState.READY, 5.0)

        # Gate should be resolved
        assert "EXORD_gate" not in module.blocked_gates
        assert module.gate_approvals.get("EXORD_gate") == "Col_Jones"
