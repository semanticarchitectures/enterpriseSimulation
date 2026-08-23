"""Property tests for readiness state transitions.

**Property 6: Readiness State Transition Event Publication**
**Property 7: Non-READY Personnel Rejects Decision Assignment**
**Property 8: Invalid Readiness State Transition Preserves Current State**
**Property 9: In-Progress Decision Deferred on Readiness Loss**

**Validates: Requirements 2.2, 2.3, 2.5, 2.6**

Property 6: For any Personnel member and any valid readiness state transition
(from one ReadinessState to a different ReadinessState), the module SHALL publish
a readiness-changed event containing the personnel name, previous state, new state,
and current simulation timestamp.

Property 7: For any Personnel member in UNAVAILABLE or INCAPACITATED state and any
Decision_Point, attempting to assign the decision to that member SHALL be rejected,
and an assignment-rejected event SHALL be published containing the personnel name,
current readiness state, and decision_id.

Property 8: For any Personnel member and any state value not in {READY, UNAVAILABLE,
INCAPACITATED}, requesting a transition to that value SHALL be rejected, the member's
readiness state SHALL remain unchanged, and an error SHALL identify the invalid value.

Property 9: For any Personnel member who is the assigned actor for an in-progress
Decision_Point, if that member's readiness state transitions to UNAVAILABLE or
INCAPACITATED, the Decision_Point outcome SHALL be set to DEFER and a
decision-deferred event SHALL be published containing the decision_id, personnel
name, and new readiness state.
"""

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st
import simpy

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    ClearanceLevel,
    CredentialType,
    DecisionPointConfig,
    DecisionType,
    Personnel,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    DecisionDeferredEvent,
    ReadinessChangedEvent,
)
from enterprise_sim.modules.personnel import PersonnelModule


# === Hypothesis Strategies ===

valid_role = st.sampled_from(["Pilot", "Loadmaster", "Navigator", "AOC_Commander", "Engineer"])
valid_duty_station = st.sampled_from(["Hickam_AFB", "Luzon_DZ", "Kadena_AB", "Clark_AB", "Manila"])
valid_clearance = st.sampled_from(list(ClearanceLevel))
valid_enclaves = st.lists(
    st.sampled_from(["NIPR", "SIPR", "JWICS", "CENTRIXS"]),
    min_size=1,
    max_size=3,
    unique=True,
)
valid_readiness_state = st.sampled_from(list(ReadinessState))
non_ready_state = st.sampled_from([ReadinessState.UNAVAILABLE, ReadinessState.INCAPACITATED])
positive_timestamp = st.floats(min_value=0.0, max_value=100000.0, allow_nan=False, allow_infinity=False)
positive_duration = st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False)

# Strategy for invalid state strings (not valid ReadinessState values)
invalid_state_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "" and s not in {"READY", "UNAVAILABLE", "INCAPACITATED"})


# === Helper: Event Collector ===


class EventCollector:
    """Collects all events published to the EventBus for assertion."""

    def __init__(self, event_bus: EventBus):
        self.events: list = []
        event_bus.subscribe_all(self._collect, "test_collector")

    def _collect(self, event):
        self.events.append(event)

    def of_type(self, event_type):
        return [e for e in self.events if isinstance(e, event_type)]


# === Helpers ===


def _make_personnel(
    name: str,
    role: str = "Pilot",
    duty_station: str = "Hickam_AFB",
    readiness: ReadinessState = ReadinessState.READY,
) -> Personnel:
    """Create a valid Personnel entry."""
    return Personnel(
        entity_id=f"personnel-{name}",
        name=name,
        role=role,
        duty_station=duty_station,
        credential=AuthenticationCredential(
            credential_type=CredentialType.CAC_PKI,
            clearance_level=ClearanceLevel.SECRET,
            authorized_enclaves=["NIPR"],
        ),
        readiness_state=readiness,
    )


def _create_initialized_module(
    roster: list[Personnel],
    decision_points: list[DecisionPointConfig] | None = None,
) -> tuple[PersonnelModule, EventBus, simpy.Environment]:
    """Create and initialize a PersonnelModule with the given roster.

    Sets up the module's internal state directly to avoid dependencies on
    unimplemented event handler methods (e.g., _handle_message_delivered
    from task 13.1). This focuses the test on readiness state logic only.

    Returns the module, event_bus, and simpy environment.
    """
    from enterprise_sim.modules.personnel.decision_processor import DecisionPointProcessor
    from enterprise_sim.modules.personnel.message_router import MessageRouter
    from enterprise_sim.modules.personnel.credential_checker import CredentialChecker

    env = simpy.Environment()
    event_bus = EventBus()

    module = PersonnelModule()

    # Manually set up internal state (mirrors initialize logic)
    module._env = env
    module._event_bus = event_bus
    module._roster = list(roster)
    module._roster_by_name = {p.name: p for p in roster}
    module._decision_points = list(decision_points or [])

    # Instantiate internal components
    module._decision_processor = DecisionPointProcessor()
    module._decision_processor.register_decision_points(module._decision_points)
    module._message_router = MessageRouter()
    module._credential_checker = CredentialChecker()

    return module, event_bus, env


# === Property 6: Readiness State Transition Event Publication ===
# Feature: personnel-module, Property 6: Readiness State Transition Event Publication


class TestReadinessStateTransitionEventPublication:
    """Property 6: Readiness State Transition Event Publication.

    **Validates: Requirements 2.2**

    For any Personnel member and any valid readiness state transition (from one
    ReadinessState to a different ReadinessState), the module SHALL publish a
    readiness-changed event containing the personnel name, previous state, new state,
    and current simulation timestamp.
    """

    @given(
        initial_state=valid_readiness_state,
        target_state=valid_readiness_state,
        timestamp=positive_timestamp,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_valid_transition_publishes_readiness_changed_event(
        self, initial_state: ReadinessState, target_state: ReadinessState, timestamp: float
    ):
        """Any valid state transition SHALL publish a ReadinessChangedEvent."""
        person = _make_personnel("TestPilot", role="Pilot", readiness=initial_state)
        module, event_bus, env = _create_initialized_module(roster=[person])
        collector = EventCollector(event_bus)

        result = module.change_readiness("TestPilot", target_state.value, timestamp)

        # Should succeed
        assert result is None, f"Expected success, got error: {result}"

        # Should publish exactly one ReadinessChangedEvent
        readiness_events = collector.of_type(ReadinessChangedEvent)
        assert len(readiness_events) == 1, (
            f"Expected 1 ReadinessChangedEvent, got {len(readiness_events)}"
        )

        event = readiness_events[0]
        assert event.personnel_name == "TestPilot"
        assert event.previous_state == initial_state.value
        assert event.new_state == target_state.value
        assert event.timestamp == timestamp

    @given(
        initial_state=valid_readiness_state,
        target_state=valid_readiness_state,
        timestamp=positive_timestamp,
        person_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=40,
        ).filter(lambda s: s.strip() != ""),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_event_contains_correct_personnel_name(
        self,
        initial_state: ReadinessState,
        target_state: ReadinessState,
        timestamp: float,
        person_name: str,
    ):
        """The published event SHALL contain the correct personnel name."""
        person = _make_personnel(person_name, role="Pilot", readiness=initial_state)
        module, event_bus, env = _create_initialized_module(roster=[person])
        collector = EventCollector(event_bus)

        result = module.change_readiness(person_name, target_state.value, timestamp)
        assert result is None

        readiness_events = collector.of_type(ReadinessChangedEvent)
        assert len(readiness_events) == 1
        assert readiness_events[0].personnel_name == person_name

    @given(
        initial_state=valid_readiness_state,
        target_state=valid_readiness_state,
        timestamp=positive_timestamp,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_event_timestamp_matches_simulation_timestamp(
        self, initial_state: ReadinessState, target_state: ReadinessState, timestamp: float
    ):
        """The event timestamp SHALL match the simulation timestamp provided."""
        person = _make_personnel("TimePilot", role="Pilot", readiness=initial_state)
        module, event_bus, env = _create_initialized_module(roster=[person])
        collector = EventCollector(event_bus)

        module.change_readiness("TimePilot", target_state.value, timestamp)

        readiness_events = collector.of_type(ReadinessChangedEvent)
        assert len(readiness_events) == 1
        assert readiness_events[0].timestamp == timestamp


# === Property 7: Non-READY Personnel Rejects Decision Assignment ===
# Feature: personnel-module, Property 7: Non-READY Personnel Rejects Decision Assignment


class TestNonReadyPersonnelRejectsDecisionAssignment:
    """Property 7: Non-READY Personnel Rejects Decision Assignment.

    **Validates: Requirements 2.3**

    For any Personnel member in UNAVAILABLE or INCAPACITATED state and any
    Decision_Point, attempting to assign the decision to that member SHALL be
    rejected (get_assigned_personnel returns None).
    """

    @given(
        non_ready=non_ready_state,
        decision_id=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip() != ""),
        deliberation=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_non_ready_personnel_not_assigned(
        self, non_ready: ReadinessState, decision_id: str, deliberation: float
    ):
        """Personnel in UNAVAILABLE or INCAPACITATED state SHALL not be assigned."""
        person = _make_personnel("NonReadyPerson", role="Pilot", readiness=non_ready)

        decision_point = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role="Pilot",
            decision_type=DecisionType.GO_NO_GO,
            deliberation_duration=deliberation,
        )

        module, event_bus, env = _create_initialized_module(
            roster=[person],
            decision_points=[decision_point],
        )

        # The decision processor should not find a READY person
        assigned = module.decision_processor.get_assigned_personnel(
            decision_id, module.roster
        )
        assert assigned is None, (
            f"Expected None for non-READY personnel (state={non_ready.value}), "
            f"got {assigned}"
        )

    @given(
        non_ready=non_ready_state,
        deliberation=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_non_ready_skipped_when_multiple_personnel(
        self, non_ready: ReadinessState, deliberation: float
    ):
        """Non-READY personnel SHALL be skipped; first READY member assigned."""
        non_ready_person = _make_personnel(
            "NonReadyPerson", role="Pilot", readiness=non_ready
        )
        ready_person = _make_personnel(
            "ReadyPerson", role="Pilot", readiness=ReadinessState.READY
        )

        decision_point = DecisionPointConfig(
            decision_id="dp-test",
            assigned_role="Pilot",
            decision_type=DecisionType.GO_NO_GO,
            deliberation_duration=deliberation,
        )

        # Non-ready person first in roster order
        module, event_bus, env = _create_initialized_module(
            roster=[non_ready_person, ready_person],
            decision_points=[decision_point],
        )

        assigned = module.decision_processor.get_assigned_personnel(
            "dp-test", module.roster
        )
        assert assigned is not None
        assert assigned.name == "ReadyPerson"

    @given(
        non_ready=non_ready_state,
        num_non_ready=st.integers(min_value=1, max_value=5),
        deliberation=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_all_non_ready_results_in_no_assignment(
        self, non_ready: ReadinessState, num_non_ready: int, deliberation: float
    ):
        """When ALL personnel with the role are non-READY, assignment returns None."""
        roster = [
            _make_personnel(f"Person_{i}", role="Pilot", readiness=non_ready)
            for i in range(num_non_ready)
        ]

        decision_point = DecisionPointConfig(
            decision_id="dp-all-non-ready",
            assigned_role="Pilot",
            decision_type=DecisionType.APPROVE_DENY,
            deliberation_duration=deliberation,
        )

        module, event_bus, env = _create_initialized_module(
            roster=roster,
            decision_points=[decision_point],
        )

        assigned = module.decision_processor.get_assigned_personnel(
            "dp-all-non-ready", module.roster
        )
        assert assigned is None


# === Property 8: Invalid Readiness State Transition Preserves Current State ===
# Feature: personnel-module, Property 8: Invalid Readiness State Transition Preserves Current State


class TestInvalidReadinessStateTransitionPreservesState:
    """Property 8: Invalid Readiness State Transition Preserves Current State.

    **Validates: Requirements 2.5**

    For any Personnel member and any state value not in {READY, UNAVAILABLE,
    INCAPACITATED}, requesting a transition to that value SHALL be rejected,
    the member's readiness state SHALL remain unchanged, and an error SHALL
    identify the invalid value.
    """

    @given(
        initial_state=valid_readiness_state,
        invalid_state=invalid_state_strategy,
        timestamp=positive_timestamp,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_invalid_state_rejected_with_error(
        self, initial_state: ReadinessState, invalid_state: str, timestamp: float
    ):
        """Transition to invalid state SHALL be rejected with an error message."""
        person = _make_personnel("TestPerson", role="Pilot", readiness=initial_state)
        module, event_bus, env = _create_initialized_module(roster=[person])

        result = module.change_readiness("TestPerson", invalid_state, timestamp)

        # Should return an error (non-None)
        assert result is not None, "Expected error for invalid state, got None"
        assert invalid_state in result, (
            f"Error should identify invalid value '{invalid_state}', got: {result}"
        )

    @given(
        initial_state=valid_readiness_state,
        invalid_state=invalid_state_strategy,
        timestamp=positive_timestamp,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_invalid_state_preserves_current_state(
        self, initial_state: ReadinessState, invalid_state: str, timestamp: float
    ):
        """Personnel readiness state SHALL remain unchanged after invalid transition."""
        person = _make_personnel("TestPerson", role="Pilot", readiness=initial_state)
        module, event_bus, env = _create_initialized_module(roster=[person])

        module.change_readiness("TestPerson", invalid_state, timestamp)

        # State should remain unchanged
        current_person = module.roster_by_name["TestPerson"]
        assert current_person.readiness_state == initial_state, (
            f"Expected state to remain {initial_state.value}, "
            f"got {current_person.readiness_state.value}"
        )

    @given(
        initial_state=valid_readiness_state,
        invalid_state=invalid_state_strategy,
        timestamp=positive_timestamp,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_invalid_state_does_not_publish_event(
        self, initial_state: ReadinessState, invalid_state: str, timestamp: float
    ):
        """No ReadinessChangedEvent SHALL be published for invalid transitions."""
        person = _make_personnel("TestPerson", role="Pilot", readiness=initial_state)
        module, event_bus, env = _create_initialized_module(roster=[person])
        collector = EventCollector(event_bus)

        module.change_readiness("TestPerson", invalid_state, timestamp)

        # Should NOT publish any readiness changed event
        readiness_events = collector.of_type(ReadinessChangedEvent)
        assert len(readiness_events) == 0, (
            f"Expected no ReadinessChangedEvent for invalid state, "
            f"got {len(readiness_events)}"
        )


# === Property 9: In-Progress Decision Deferred on Readiness Loss ===
# Feature: personnel-module, Property 9: In-Progress Decision Deferred on Readiness Loss


class TestInProgressDecisionDeferredOnReadinessLoss:
    """Property 9: In-Progress Decision Deferred on Readiness Loss.

    **Validates: Requirements 2.6**

    For any Personnel member who is the assigned actor for an in-progress
    Decision_Point, if that member's readiness state transitions to UNAVAILABLE
    or INCAPACITATED, the Decision_Point outcome SHALL be set to DEFER and a
    decision-deferred event SHALL be published containing the decision_id,
    personnel name, and new readiness state.
    """

    @given(
        target_state=non_ready_state,
        timestamp=positive_timestamp,
        decision_id=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip() != ""),
        deliberation=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_readiness_loss_defers_in_progress_decision(
        self,
        target_state: ReadinessState,
        timestamp: float,
        decision_id: str,
        deliberation: float,
    ):
        """Readiness loss SHALL defer in-progress decisions and publish event."""
        person = _make_personnel("DecisionMaker", role="Pilot", readiness=ReadinessState.READY)

        decision_point = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role="Pilot",
            decision_type=DecisionType.GO_NO_GO,
            deliberation_duration=deliberation,
        )

        module, event_bus, env = _create_initialized_module(
            roster=[person],
            decision_points=[decision_point],
        )

        # Simulate an in-progress decision by directly registering it
        module.decision_processor.in_progress[decision_id] = "DecisionMaker"

        collector = EventCollector(event_bus)

        # Now change readiness to a non-READY state
        result = module.change_readiness("DecisionMaker", target_state.value, timestamp)
        assert result is None, f"Expected success, got error: {result}"

        # Should publish a DecisionDeferredEvent
        deferred_events = collector.of_type(DecisionDeferredEvent)
        assert len(deferred_events) >= 1, (
            f"Expected at least 1 DecisionDeferredEvent, got {len(deferred_events)}"
        )

        # Find the deferred event for our decision_id
        matching = [e for e in deferred_events if e.decision_id == decision_id]
        assert len(matching) == 1, (
            f"Expected exactly 1 DecisionDeferredEvent for '{decision_id}', "
            f"got {len(matching)}"
        )

        event = matching[0]
        assert event.personnel_name == "DecisionMaker"
        assert event.readiness_state == target_state.value
        assert event.timestamp == timestamp

    @given(
        target_state=non_ready_state,
        timestamp=positive_timestamp,
        decision_id=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip() != ""),
        deliberation=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_in_progress_decision_removed_after_deferral(
        self,
        target_state: ReadinessState,
        timestamp: float,
        decision_id: str,
        deliberation: float,
    ):
        """Deferred decision SHALL be removed from in-progress tracking."""
        person = _make_personnel("DecisionMaker", role="Pilot", readiness=ReadinessState.READY)

        decision_point = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role="Pilot",
            decision_type=DecisionType.GO_NO_GO,
            deliberation_duration=deliberation,
        )

        module, event_bus, env = _create_initialized_module(
            roster=[person],
            decision_points=[decision_point],
        )

        # Simulate in-progress decision
        module.decision_processor.in_progress[decision_id] = "DecisionMaker"

        module.change_readiness("DecisionMaker", target_state.value, timestamp)

        # Decision should no longer be in-progress
        assert decision_id not in module.decision_processor.in_progress, (
            f"Decision '{decision_id}' should be removed from in_progress after deferral"
        )

    @given(
        target_state=non_ready_state,
        timestamp=positive_timestamp,
        num_decisions=st.integers(min_value=1, max_value=3),
        deliberation=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_in_progress_decisions_all_deferred(
        self,
        target_state: ReadinessState,
        timestamp: float,
        num_decisions: int,
        deliberation: float,
    ):
        """ALL in-progress decisions for the person SHALL be deferred."""
        person = _make_personnel("MultiDecider", role="Pilot", readiness=ReadinessState.READY)

        decision_points = [
            DecisionPointConfig(
                decision_id=f"dp-{i}",
                assigned_role="Pilot",
                decision_type=DecisionType.GO_NO_GO,
                deliberation_duration=deliberation,
            )
            for i in range(num_decisions)
        ]

        module, event_bus, env = _create_initialized_module(
            roster=[person],
            decision_points=decision_points,
        )

        # Simulate multiple in-progress decisions
        for i in range(num_decisions):
            module.decision_processor.in_progress[f"dp-{i}"] = "MultiDecider"

        collector = EventCollector(event_bus)

        module.change_readiness("MultiDecider", target_state.value, timestamp)

        # All decisions should be deferred
        deferred_events = collector.of_type(DecisionDeferredEvent)
        assert len(deferred_events) == num_decisions, (
            f"Expected {num_decisions} DecisionDeferredEvents, "
            f"got {len(deferred_events)}"
        )

        # All should reference "MultiDecider"
        for event in deferred_events:
            assert event.personnel_name == "MultiDecider"
            assert event.readiness_state == target_state.value

        # No decisions should remain in-progress
        for i in range(num_decisions):
            assert f"dp-{i}" not in module.decision_processor.in_progress
