"""Property tests for the Decision Point Processor.

**Property 10: Decision Point Role Resolution (Roster Order)**
**Property 11: Decision Evaluation Timing and Event**
**Property 12: DENY Outcome Publishes Mission-Blocked Event**
**Property 13: Decision Deferral and Timeout**
**Property 14: Duplicate Decision ID Validation**

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.9**
"""

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from pydantic import ValidationError
import pytest
import simpy

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    ClearanceLevel,
    CredentialType,
    DecisionOutcome,
    DecisionPointConfig,
    DecisionType,
    Personnel,
    PersonnelConfig,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    DecisionDeferredEvent,
    DecisionRenderedEvent,
    DecisionTimeoutEvent,
    MissionBlockedEvent,
)
from enterprise_sim.modules.personnel.decision_processor import DecisionPointProcessor


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
valid_decision_type = st.sampled_from(list(DecisionType))
positive_duration = st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False)
positive_timeout = st.floats(min_value=0.1, max_value=500.0, allow_nan=False, allow_infinity=False)

identifier_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip() != "")


@st.composite
def credential_strategy(draw):
    """Generate a valid AuthenticationCredential."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=draw(valid_clearance),
        authorized_enclaves=draw(valid_enclaves),
    )


@st.composite
def personnel_strategy(draw, role=None, readiness=None, name_suffix=None):
    """Generate a valid Personnel instance with optional fixed role/readiness."""
    suffix = name_suffix if name_suffix is not None else draw(st.integers(min_value=0, max_value=9999))
    return Personnel(
        entity_id=f"personnel-{suffix}",
        name=f"Person_{suffix}",
        role=role if role is not None else draw(valid_role),
        duty_station=draw(valid_duty_station),
        credential=draw(credential_strategy()),
        readiness_state=readiness if readiness is not None else draw(st.sampled_from(list(ReadinessState))),
    )


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


# === Property 10: Decision Point Role Resolution (Roster Order) ===
# Feature: personnel-module, Property 10: Decision Point Role Resolution (Roster Order)


class TestDecisionPointRoleResolution:
    """Property 10: Decision Point Role Resolution (Roster Order).

    **Validates: Requirements 3.2**

    For any Decision_Point with an assigned_role where multiple Personnel members
    have that role, the module SHALL select the first member in Personnel_Roster order
    whose readiness state is READY.
    """

    @given(
        role=valid_role,
        num_with_role=st.integers(min_value=2, max_value=6),
        first_ready_index=st.integers(min_value=0, max_value=5),
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_selects_first_ready_in_roster_order(self, role, num_with_role, first_ready_index, data):
        """The processor selects the first READY member in roster order for the assigned role."""
        # Clamp first_ready_index to valid range
        first_ready_index = first_ready_index % num_with_role

        # Build a roster with multiple personnel of the same role
        roster = []
        for i in range(num_with_role):
            if i < first_ready_index:
                # Before the first_ready_index, these are NOT ready
                readiness = ReadinessState.UNAVAILABLE
            elif i == first_ready_index:
                readiness = ReadinessState.READY
            else:
                # After, mix of states
                readiness = data.draw(st.sampled_from(list(ReadinessState)))

            roster.append(Personnel(
                entity_id=f"p-{i}",
                name=f"Member_{i}",
                role=role,
                duty_station="Hickam_AFB",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.SECRET,
                    authorized_enclaves=["SIPR"],
                ),
                readiness_state=readiness,
            ))

        # Create decision point with the assigned role
        decision_id = "test_decision"
        dp_config = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role,
            decision_type=DecisionType.GO_NO_GO,
            deliberation_duration=5.0,
        )

        processor = DecisionPointProcessor()
        processor.register_decision_points([dp_config])

        # Resolve personnel
        assigned = processor.get_assigned_personnel(decision_id, roster)

        # The assigned member should be the first READY one in roster order
        assert assigned is not None
        assert assigned.name == f"Member_{first_ready_index}"
        assert assigned.readiness_state == ReadinessState.READY

    @given(
        role=valid_role,
        num_with_role=st.integers(min_value=2, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_returns_none_when_no_ready(self, role, num_with_role):
        """When no personnel with the role is READY, returns None."""
        roster = []
        for i in range(num_with_role):
            roster.append(Personnel(
                entity_id=f"p-{i}",
                name=f"Member_{i}",
                role=role,
                duty_station="Hickam_AFB",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.SECRET,
                    authorized_enclaves=["SIPR"],
                ),
                readiness_state=ReadinessState.UNAVAILABLE,
            ))

        decision_id = "test_decision"
        dp_config = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role,
            decision_type=DecisionType.APPROVE_DENY,
            deliberation_duration=3.0,
        )

        processor = DecisionPointProcessor()
        processor.register_decision_points([dp_config])

        assigned = processor.get_assigned_personnel(decision_id, roster)
        assert assigned is None


# === Property 11: Decision Evaluation Timing and Event ===
# Feature: personnel-module, Property 11: Decision Evaluation Timing and Event


class TestDecisionEvaluationTimingAndEvent:
    """Property 11: Decision Evaluation Timing and Event.

    **Validates: Requirements 3.3, 3.4**

    For any Decision_Point with a configured deliberation_duration, evaluation SHALL
    consume exactly that duration in simulation time, and upon completion SHALL publish
    a decision-rendered event containing the decision_id, personnel name, decision_type,
    outcome, and simulation timestamp.
    """

    @given(
        deliberation_duration=positive_duration,
        decision_type=valid_decision_type,
        role=valid_role,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_evaluation_consumes_exact_duration(self, deliberation_duration, decision_type, role):
        """Decision evaluation SHALL consume exactly deliberation_duration in sim time."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        decision_id = "timing_test"
        dp_config = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role,
            decision_type=decision_type,
            deliberation_duration=deliberation_duration,
        )

        # Create a single READY personnel member with matching role
        roster = [Personnel(
            entity_id="p-0",
            name="TestPerson",
            role=role,
            duty_station="Hickam_AFB",
            credential=AuthenticationCredential(
                credential_type=CredentialType.CAC_PKI,
                clearance_level=ClearanceLevel.SECRET,
                authorized_enclaves=["SIPR"],
            ),
            readiness_state=ReadinessState.READY,
        )]

        processor = DecisionPointProcessor()
        processor.register_decision_points([dp_config])

        start_time = env.now
        env.process(processor.trigger_decision(decision_id, env, event_bus, roster))
        env.run()

        # Verify exact time consumption
        elapsed = env.now - start_time
        assert abs(elapsed - deliberation_duration) < 1e-9

        # Verify decision-rendered event was published
        rendered_events = collector.of_type(DecisionRenderedEvent)
        assert len(rendered_events) == 1
        event = rendered_events[0]
        assert event.decision_id == decision_id
        assert event.personnel_name == "TestPerson"
        assert event.decision_type == decision_type.value
        assert event.outcome in [o.value for o in DecisionOutcome]


# === Property 12: DENY Outcome Publishes Mission-Blocked Event ===
# Feature: personnel-module, Property 12: DENY Outcome Publishes Mission-Blocked Event


class TestDenyOutcomePublishesMissionBlocked:
    """Property 12: DENY Outcome Publishes Mission-Blocked Event.

    **Validates: Requirements 3.5**

    For any Decision_Point whose outcome is DENY, the module SHALL publish a
    mission-blocked event containing the decision_id, blocking personnel name, and reason.
    """

    @given(
        deliberation_duration=positive_duration,
        decision_type=valid_decision_type,
        role=valid_role,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_deny_outcome_publishes_mission_blocked(self, deliberation_duration, decision_type, role):
        """A DENY outcome SHALL publish a mission-blocked event."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        decision_id = "deny_test"
        dp_config = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role,
            decision_type=decision_type,
            deliberation_duration=deliberation_duration,
        )

        roster = [Personnel(
            entity_id="p-0",
            name="BlockingPerson",
            role=role,
            duty_station="Hickam_AFB",
            credential=AuthenticationCredential(
                credential_type=CredentialType.CAC_PKI,
                clearance_level=ClearanceLevel.SECRET,
                authorized_enclaves=["SIPR"],
            ),
            readiness_state=ReadinessState.READY,
        )]

        processor = DecisionPointProcessor()
        processor.register_decision_points([dp_config])

        # Use trigger_decision_with_outcome to force DENY
        env.process(processor.trigger_decision_with_outcome(
            decision_id, env, event_bus, roster, DecisionOutcome.DENY
        ))
        env.run()

        # Verify decision-rendered event with DENY outcome
        rendered_events = collector.of_type(DecisionRenderedEvent)
        assert len(rendered_events) == 1
        assert rendered_events[0].outcome == DecisionOutcome.DENY.value

        # Verify mission-blocked event was published
        blocked_events = collector.of_type(MissionBlockedEvent)
        assert len(blocked_events) == 1
        blocked_event = blocked_events[0]
        assert blocked_event.decision_id == decision_id
        assert blocked_event.blocking_personnel == "BlockingPerson"
        assert blocked_event.reason != ""

    @given(
        deliberation_duration=positive_duration,
        decision_type=valid_decision_type,
        role=valid_role,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_approve_outcome_does_not_publish_mission_blocked(self, deliberation_duration, decision_type, role):
        """An APPROVE outcome SHALL NOT publish a mission-blocked event."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        decision_id = "approve_test"
        dp_config = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role,
            decision_type=decision_type,
            deliberation_duration=deliberation_duration,
        )

        roster = [Personnel(
            entity_id="p-0",
            name="ApprovingPerson",
            role=role,
            duty_station="Hickam_AFB",
            credential=AuthenticationCredential(
                credential_type=CredentialType.CAC_PKI,
                clearance_level=ClearanceLevel.SECRET,
                authorized_enclaves=["SIPR"],
            ),
            readiness_state=ReadinessState.READY,
        )]

        processor = DecisionPointProcessor()
        processor.register_decision_points([dp_config])

        env.process(processor.trigger_decision_with_outcome(
            decision_id, env, event_bus, roster, DecisionOutcome.APPROVE
        ))
        env.run()

        # No mission-blocked event for APPROVE
        blocked_events = collector.of_type(MissionBlockedEvent)
        assert len(blocked_events) == 0


# === Property 13: Decision Deferral and Timeout ===
# Feature: personnel-module, Property 13: Decision Deferral and Timeout


class TestDecisionDeferralAndTimeout:
    """Property 13: Decision Deferral and Timeout.

    **Validates: Requirements 3.6, 3.7**

    For any Decision_Point where no Personnel member with the assigned_role is in
    READY state, the module SHALL defer evaluation. If the configurable timeout expires
    without a READY member becoming available, the outcome SHALL be set to DEFER and a
    decision-timeout event SHALL be published containing the decision_id and assigned_role.
    """

    @given(
        timeout_value=positive_timeout,
        role=valid_role,
        num_personnel=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_timeout_publishes_decision_timeout_event(self, timeout_value, role, num_personnel):
        """When no READY personnel and timeout expires, a decision-timeout event is published."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        decision_id = "timeout_test"
        dp_config = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role,
            decision_type=DecisionType.GO_NO_GO,
            deliberation_duration=5.0,
            timeout=timeout_value,
        )

        # All personnel with the role are NOT ready
        roster = []
        for i in range(num_personnel):
            roster.append(Personnel(
                entity_id=f"p-{i}",
                name=f"NonReady_{i}",
                role=role,
                duty_station="Hickam_AFB",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.SECRET,
                    authorized_enclaves=["SIPR"],
                ),
                readiness_state=ReadinessState.UNAVAILABLE,
            ))

        processor = DecisionPointProcessor()
        processor.register_decision_points([dp_config])

        env.process(processor.trigger_decision(decision_id, env, event_bus, roster))
        env.run()

        # Verify deferred event was published first
        deferred_events = collector.of_type(DecisionDeferredEvent)
        assert len(deferred_events) >= 1
        assert deferred_events[0].decision_id == decision_id
        assert deferred_events[0].assigned_role == role

        # Verify timeout event was published
        timeout_events = collector.of_type(DecisionTimeoutEvent)
        assert len(timeout_events) == 1
        assert timeout_events[0].decision_id == decision_id
        assert timeout_events[0].assigned_role == role

        # Verify simulation advanced by the timeout value
        assert abs(env.now - timeout_value) < 1e-9

    @given(
        role=valid_role,
        num_personnel=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_timeout_defers_immediately(self, role, num_personnel):
        """When no READY personnel and no timeout configured, evaluation defers immediately."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        decision_id = "no_timeout_test"
        dp_config = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role,
            decision_type=DecisionType.APPROVE_DENY,
            deliberation_duration=5.0,
            timeout=None,
        )

        # All personnel with the role are NOT ready
        roster = []
        for i in range(num_personnel):
            roster.append(Personnel(
                entity_id=f"p-{i}",
                name=f"NonReady_{i}",
                role=role,
                duty_station="Hickam_AFB",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.SECRET,
                    authorized_enclaves=["SIPR"],
                ),
                readiness_state=ReadinessState.UNAVAILABLE,
            ))

        processor = DecisionPointProcessor()
        processor.register_decision_points([dp_config])

        env.process(processor.trigger_decision(decision_id, env, event_bus, roster))
        env.run()

        # Verify deferred event was published
        deferred_events = collector.of_type(DecisionDeferredEvent)
        assert len(deferred_events) >= 1
        assert deferred_events[0].decision_id == decision_id

        # No timeout event since no timeout configured
        timeout_events = collector.of_type(DecisionTimeoutEvent)
        assert len(timeout_events) == 0

        # No simulation time consumed (immediate return)
        assert env.now == 0


# === Property 14: Duplicate Decision ID Validation ===
# Feature: personnel-module, Property 14: Duplicate Decision ID Validation


class TestDuplicateDecisionIdValidation:
    """Property 14: Duplicate Decision ID Validation.

    **Validates: Requirements 3.9**

    For any personnel configuration containing two or more Decision_Points with the
    same decision_id, validation SHALL reject the configuration and identify the
    duplicated decision_id.
    """

    @given(
        decision_id=identifier_st,
        decision_type_1=valid_decision_type,
        decision_type_2=valid_decision_type,
        role_1=valid_role,
        role_2=valid_role,
        duration_1=positive_duration,
        duration_2=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_duplicate_decision_ids_rejected(
        self, decision_id, decision_type_1, decision_type_2, role_1, role_2, duration_1, duration_2
    ):
        """Configuration with duplicate decision_ids SHALL be rejected."""
        dp1 = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role_1,
            decision_type=decision_type_1,
            deliberation_duration=duration_1,
        )
        dp2 = DecisionPointConfig(
            decision_id=decision_id,
            assigned_role=role_2,
            decision_type=decision_type_2,
            deliberation_duration=duration_2,
        )

        roster = [Personnel(
            entity_id="p-0",
            name="TestPerson",
            role="Pilot",
            duty_station="Hickam_AFB",
            credential=AuthenticationCredential(
                credential_type=CredentialType.CAC_PKI,
                clearance_level=ClearanceLevel.SECRET,
                authorized_enclaves=["SIPR"],
            ),
            readiness_state=ReadinessState.READY,
        )]

        with pytest.raises(ValidationError) as exc_info:
            PersonnelConfig(roster=roster, decision_points=[dp1, dp2])

        error_text = str(exc_info.value)
        assert "Duplicate" in error_text or "duplicate" in error_text or decision_id in error_text

    @given(
        num_decision_points=st.integers(min_value=2, max_value=5),
        decision_type=valid_decision_type,
        role=valid_role,
        duration=positive_duration,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_unique_decision_ids_accepted(
        self, num_decision_points, decision_type, role, duration
    ):
        """Configuration with unique decision_ids SHALL be accepted."""
        dps = []
        for i in range(num_decision_points):
            dps.append(DecisionPointConfig(
                decision_id=f"decision_{i}",
                assigned_role=role,
                decision_type=decision_type,
                deliberation_duration=duration,
            ))

        roster = [Personnel(
            entity_id="p-0",
            name="TestPerson",
            role="Pilot",
            duty_station="Hickam_AFB",
            credential=AuthenticationCredential(
                credential_type=CredentialType.CAC_PKI,
                clearance_level=ClearanceLevel.SECRET,
                authorized_enclaves=["SIPR"],
            ),
            readiness_state=ReadinessState.READY,
        )]

        config = PersonnelConfig(roster=roster, decision_points=dps)
        assert len(config.decision_points) == num_decision_points
