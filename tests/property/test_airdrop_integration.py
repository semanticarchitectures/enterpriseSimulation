"""Property tests for airdrop integration (Loadmaster Decision Gates Extraction).

**Property 26: Loadmaster Decision Gates Extraction**
**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**

For any airdrop extraction sequence, the module SHALL require a Decision_Point
evaluation from a Personnel member with Loadmaster role at the drop zone node.
If outcome is APPROVE, a cargo-release-confirmed event is published and extraction
proceeds. If DENY, a cargo-release-denied event is published and extraction does
NOT initiate. If no READY Loadmaster exists, extraction is blocked with a
decision-deferred event until one becomes READY or timeout expires.
"""

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
import simpy

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import ExtractionStartEvent, SimulationEvent
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
    CargoReleaseConfirmedEvent,
    CargoReleaseDeniedEvent,
    DecisionDeferredEvent,
    DecisionRenderedEvent,
    DecisionTimeoutEvent,
)
from enterprise_sim.modules.personnel import PersonnelModule


# === Hypothesis Strategies ===

valid_drop_zone = st.sampled_from(["Luzon_DZ", "Kadena_DZ", "Clark_DZ", "Manila_DZ", "Subic_DZ"])
valid_duty_station = st.sampled_from(["Hickam_AFB", "Luzon_DZ", "Kadena_DZ", "Clark_DZ", "Manila_DZ", "Subic_DZ"])
valid_clearance = st.sampled_from(list(ClearanceLevel))
valid_enclaves = st.lists(
    st.sampled_from(["NIPR", "SIPR", "JWICS", "CENTRIXS"]),
    min_size=1,
    max_size=3,
    unique=True,
)
positive_duration = st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False)
positive_timeout = st.floats(min_value=0.5, max_value=50.0, allow_nan=False, allow_infinity=False)
non_ready_state = st.sampled_from([ReadinessState.UNAVAILABLE, ReadinessState.INCAPACITATED])


@st.composite
def credential_strategy(draw):
    """Generate a valid AuthenticationCredential."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=draw(valid_clearance),
        authorized_enclaves=draw(valid_enclaves),
    )


@st.composite
def loadmaster_at_drop_zone(draw, drop_zone, readiness=None, name_suffix=None):
    """Generate a Loadmaster Personnel at a specific drop zone."""
    suffix = name_suffix if name_suffix is not None else draw(st.integers(min_value=0, max_value=9999))
    return Personnel(
        entity_id=f"loadmaster-{suffix}",
        name=f"Loadmaster_{suffix}",
        role="Loadmaster",
        duty_station=drop_zone,
        credential=draw(credential_strategy()),
        readiness_state=readiness if readiness is not None else ReadinessState.READY,
    )


@st.composite
def non_loadmaster_personnel(draw, name_suffix=None):
    """Generate a non-Loadmaster Personnel member."""
    suffix = name_suffix if name_suffix is not None else draw(st.integers(min_value=0, max_value=9999))
    role = draw(st.sampled_from(["Pilot", "Navigator", "AOC_Commander", "Engineer"]))
    return Personnel(
        entity_id=f"person-{suffix}",
        name=f"Person_{suffix}",
        role=role,
        duty_station=draw(valid_duty_station),
        credential=draw(credential_strategy()),
        readiness_state=draw(st.sampled_from(list(ReadinessState))),
    )


# === Helper Functions ===


class EventCollector:
    """Collects all events published to the EventBus for assertion."""

    def __init__(self, event_bus: EventBus):
        self.events: list[SimulationEvent] = []
        event_bus.subscribe_all(self._collect, "test_collector")

    def _collect(self, event):
        self.events.append(event)

    def of_type(self, event_type):
        return [e for e in self.events if isinstance(e, event_type)]


def _make_config_mock(roster, decision_points=None):
    """Create a minimal mock config for PersonnelModule initialization."""

    class MockRouteGraph:
        nodes = [type("Node", (), {"name": p.duty_station})() for p in roster]

    class MockConfig:
        route_graph = MockRouteGraph()
        c2_messages = []
        authorization_chain = None
        cross_domain_gateway = None
        personnel_parameters = PersonnelConfig(
            roster=roster,
            decision_points=decision_points or [],
        )

    return MockConfig()


def _trigger_extraction(module, drop_zone_id, timestamp=0.0):
    """Trigger an extraction ready event on the module."""
    extraction_event = ExtractionStartEvent(
        entity_id=f"extraction_{drop_zone_id}",
        timestamp=timestamp,
        event_type="extraction_start",
        source_module="airdrop",
        drop_zone_id=drop_zone_id,
    )
    module._handle_extraction_ready(extraction_event)


# === Property 26: Loadmaster Decision Gates Extraction ===
# Feature: personnel-module, Property 26: Loadmaster Decision Gates Extraction


class TestLoadmasterApprovePublishesConfirmedEvent:
    """Property 26 (APPROVE path): READY Loadmaster at drop zone with APPROVE outcome
    publishes CargoReleaseConfirmedEvent and extraction proceeds.

    **Validates: Requirements 7.1, 7.2**
    """

    @given(
        drop_zone=valid_drop_zone,
        deliberation_duration=positive_duration,
        timeout=positive_timeout,
        extra_personnel_count=st.integers(min_value=0, max_value=3),
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_approve_publishes_cargo_release_confirmed(
        self, drop_zone, deliberation_duration, timeout, extra_personnel_count, data
    ):
        """A READY Loadmaster at the drop zone with APPROVE outcome SHALL publish
        a cargo-release-confirmed event containing loadmaster name and drop zone node."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        # Generate a READY Loadmaster at the drop zone
        loadmaster = data.draw(
            loadmaster_at_drop_zone(drop_zone=drop_zone, readiness=ReadinessState.READY, name_suffix=0)
        )

        # Optionally add extra non-Loadmaster personnel
        roster = [loadmaster]
        for i in range(extra_personnel_count):
            person = data.draw(non_loadmaster_personnel(name_suffix=i + 1))
            # Ensure unique duty stations are represented in route graph
            roster.append(person)

        decision_points = [
            DecisionPointConfig(
                decision_id="loadmaster_release",
                assigned_role="Loadmaster",
                decision_type=DecisionType.APPROVE_DENY,
                deliberation_duration=deliberation_duration,
                timeout=timeout,
            )
        ]

        config = _make_config_mock(roster=roster, decision_points=decision_points)
        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Default outcome is APPROVE (no override)
        _trigger_extraction(module, drop_zone)

        # Run simulation past deliberation
        env.run(until=deliberation_duration + 1.0)

        # Verify CargoReleaseConfirmedEvent was published
        confirmed_events = collector.of_type(CargoReleaseConfirmedEvent)
        assert len(confirmed_events) == 1
        assert confirmed_events[0].loadmaster_name == loadmaster.name
        assert confirmed_events[0].drop_zone_node == drop_zone

        # Verify NO CargoReleaseDeniedEvent
        denied_events = collector.of_type(CargoReleaseDeniedEvent)
        assert len(denied_events) == 0

        # Verify DecisionRenderedEvent with APPROVE outcome
        rendered_events = collector.of_type(DecisionRenderedEvent)
        assert len(rendered_events) == 1
        assert rendered_events[0].outcome == "APPROVE"
        assert rendered_events[0].personnel_name == loadmaster.name


class TestLoadmasterDenyPublishesDeniedEvent:
    """Property 26 (DENY path): READY Loadmaster at drop zone with DENY outcome
    publishes CargoReleaseDeniedEvent and extraction does NOT initiate.

    **Validates: Requirements 7.1, 7.3**
    """

    @given(
        drop_zone=valid_drop_zone,
        deliberation_duration=positive_duration,
        timeout=positive_timeout,
        extra_personnel_count=st.integers(min_value=0, max_value=3),
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_deny_publishes_cargo_release_denied(
        self, drop_zone, deliberation_duration, timeout, extra_personnel_count, data
    ):
        """A READY Loadmaster at the drop zone with DENY outcome SHALL publish
        a cargo-release-denied event and extraction does NOT initiate."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        # Generate a READY Loadmaster at the drop zone
        loadmaster = data.draw(
            loadmaster_at_drop_zone(drop_zone=drop_zone, readiness=ReadinessState.READY, name_suffix=0)
        )

        # Optionally add extra non-Loadmaster personnel
        roster = [loadmaster]
        for i in range(extra_personnel_count):
            person = data.draw(non_loadmaster_personnel(name_suffix=i + 1))
            roster.append(person)

        decision_points = [
            DecisionPointConfig(
                decision_id="loadmaster_release",
                assigned_role="Loadmaster",
                decision_type=DecisionType.APPROVE_DENY,
                deliberation_duration=deliberation_duration,
                timeout=timeout,
            )
        ]

        config = _make_config_mock(roster=roster, decision_points=decision_points)
        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Set outcome to DENY
        module.set_loadmaster_outcome(DecisionOutcome.DENY)

        _trigger_extraction(module, drop_zone)

        # Run simulation past deliberation
        env.run(until=deliberation_duration + 1.0)

        # Verify CargoReleaseDeniedEvent was published
        denied_events = collector.of_type(CargoReleaseDeniedEvent)
        assert len(denied_events) == 1
        assert denied_events[0].loadmaster_name == loadmaster.name
        assert denied_events[0].drop_zone_node == drop_zone

        # Verify NO CargoReleaseConfirmedEvent (extraction does NOT initiate)
        confirmed_events = collector.of_type(CargoReleaseConfirmedEvent)
        assert len(confirmed_events) == 0

        # Verify DecisionRenderedEvent with DENY outcome
        rendered_events = collector.of_type(DecisionRenderedEvent)
        assert len(rendered_events) == 1
        assert rendered_events[0].outcome == "DENY"


class TestNoReadyLoadmasterDefersExtraction:
    """Property 26 (no READY path): When no READY Loadmaster exists at the drop zone,
    extraction is blocked with a decision-deferred event until one becomes READY
    or timeout expires.

    **Validates: Requirements 7.1, 7.4, 7.5**
    """

    @given(
        drop_zone=valid_drop_zone,
        deliberation_duration=positive_duration,
        timeout=positive_timeout,
        readiness=non_ready_state,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_no_ready_loadmaster_publishes_deferred_and_timeout(
        self, drop_zone, deliberation_duration, timeout, readiness, data
    ):
        """When no READY Loadmaster at drop zone, SHALL publish decision-deferred event.
        If timeout expires without READY Loadmaster, SHALL publish decision-timeout event
        and extraction does NOT initiate."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        # Generate a non-READY Loadmaster at the drop zone
        loadmaster = data.draw(
            loadmaster_at_drop_zone(drop_zone=drop_zone, readiness=readiness, name_suffix=0)
        )

        roster = [loadmaster]
        decision_points = [
            DecisionPointConfig(
                decision_id="loadmaster_release",
                assigned_role="Loadmaster",
                decision_type=DecisionType.APPROVE_DENY,
                deliberation_duration=deliberation_duration,
                timeout=timeout,
            )
        ]

        config = _make_config_mock(roster=roster, decision_points=decision_points)
        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        _trigger_extraction(module, drop_zone)

        # Run simulation past timeout
        env.run(until=timeout + 1.0)

        # Verify DecisionDeferredEvent was published (extraction blocked)
        deferred_events = collector.of_type(DecisionDeferredEvent)
        assert len(deferred_events) >= 1
        assert deferred_events[0].assigned_role == "Loadmaster"

        # Verify DecisionTimeoutEvent was published (timeout expired)
        timeout_events = collector.of_type(DecisionTimeoutEvent)
        assert len(timeout_events) == 1
        assert timeout_events[0].assigned_role == "Loadmaster"

        # Verify NO cargo release confirmed event (extraction does NOT initiate)
        confirmed_events = collector.of_type(CargoReleaseConfirmedEvent)
        assert len(confirmed_events) == 0

        # Verify NO cargo release denied event (no decision was rendered)
        denied_events = collector.of_type(CargoReleaseDeniedEvent)
        assert len(denied_events) == 0

    @given(
        drop_zone=valid_drop_zone,
        deliberation_duration=positive_duration,
        timeout=positive_timeout,
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_loadmaster_at_wrong_station_defers(
        self, drop_zone, deliberation_duration, timeout, data
    ):
        """A READY Loadmaster at a DIFFERENT duty station does NOT satisfy the
        drop zone requirement — extraction is blocked."""
        env = simpy.Environment()
        event_bus = EventBus()
        collector = EventCollector(event_bus)

        # Pick a station that is NOT the drop zone
        other_stations = [s for s in ["Hickam_AFB", "Luzon_DZ", "Kadena_DZ", "Clark_DZ", "Manila_DZ", "Subic_DZ"] if s != drop_zone]
        wrong_station = data.draw(st.sampled_from(other_stations))

        loadmaster = Personnel(
            entity_id="loadmaster-wrong",
            name="Loadmaster_Wrong_Station",
            role="Loadmaster",
            duty_station=wrong_station,
            credential=AuthenticationCredential(
                credential_type=CredentialType.CAC_PKI,
                clearance_level=ClearanceLevel.SECRET,
                authorized_enclaves=["NIPR"],
            ),
            readiness_state=ReadinessState.READY,
        )

        roster = [loadmaster]
        decision_points = [
            DecisionPointConfig(
                decision_id="loadmaster_release",
                assigned_role="Loadmaster",
                decision_type=DecisionType.APPROVE_DENY,
                deliberation_duration=deliberation_duration,
                timeout=timeout,
            )
        ]

        config = _make_config_mock(roster=roster, decision_points=decision_points)
        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        _trigger_extraction(module, drop_zone)

        # Run simulation past timeout
        env.run(until=timeout + 1.0)

        # Should defer because Loadmaster is not at the drop zone
        deferred_events = collector.of_type(DecisionDeferredEvent)
        assert len(deferred_events) >= 1

        # Should timeout since Loadmaster never at correct station
        timeout_events = collector.of_type(DecisionTimeoutEvent)
        assert len(timeout_events) == 1

        # Extraction does NOT initiate
        confirmed_events = collector.of_type(CargoReleaseConfirmedEvent)
        assert len(confirmed_events) == 0
