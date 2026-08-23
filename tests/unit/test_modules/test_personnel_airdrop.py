"""Unit tests for Personnel module airdrop integration (loadmaster decision logic).

Tests the _handle_extraction_ready handler and the _loadmaster_decision_process
SimPy process for requirements 7.1, 7.2, 7.3, 7.4, 7.5.
"""

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


def _make_credential():
    """Create a default credential for tests."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=ClearanceLevel.SECRET,
        authorized_enclaves=["NIPR"],
    )


def _make_loadmaster(name: str = "SGT_Smith", duty_station: str = "Luzon_DZ",
                     readiness: ReadinessState = ReadinessState.READY) -> Personnel:
    """Create a Loadmaster personnel member."""
    return Personnel(
        entity_id=f"person_{name}",
        name=name,
        role="Loadmaster",
        duty_station=duty_station,
        credential=_make_credential(),
        readiness_state=readiness,
    )


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


class TestLoadmasterApprove:
    """Test that APPROVE outcome publishes CargoReleaseConfirmedEvent."""

    def test_loadmaster_approve_publishes_confirmed_event(self):
        """Req 7.2: APPROVE publishes cargo-release-confirmed, extraction proceeds."""
        env = simpy.Environment()
        event_bus = EventBus()

        loadmaster = _make_loadmaster()
        config = _make_config_mock(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=10.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Trigger extraction ready event
        extraction_event = ExtractionStartEvent(
            entity_id="extraction_1",
            timestamp=0.0,
            event_type="extraction_start",
            source_module="airdrop",
            drop_zone_id="Luzon_DZ",
        )
        module._handle_extraction_ready(extraction_event)

        # Run simulation past deliberation duration
        env.run(until=5.0)

        # Verify CargoReleaseConfirmedEvent was published
        confirmed_events = [
            e for e in published_events if isinstance(e, CargoReleaseConfirmedEvent)
        ]
        assert len(confirmed_events) == 1
        assert confirmed_events[0].loadmaster_name == "SGT_Smith"
        assert confirmed_events[0].drop_zone_node == "Luzon_DZ"

        # Verify DecisionRenderedEvent was published with APPROVE
        rendered_events = [
            e for e in published_events if isinstance(e, DecisionRenderedEvent)
        ]
        assert len(rendered_events) == 1
        assert rendered_events[0].outcome == "APPROVE"


class TestLoadmasterDeny:
    """Test that DENY outcome publishes CargoReleaseDeniedEvent."""

    def test_loadmaster_deny_publishes_denied_event(self):
        """Req 7.3: DENY publishes cargo-release-denied, extraction does NOT initiate."""
        env = simpy.Environment()
        event_bus = EventBus()

        loadmaster = _make_loadmaster()
        config = _make_config_mock(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=10.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)
        # Set outcome to DENY
        module.set_loadmaster_outcome(DecisionOutcome.DENY)

        # Collect published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Trigger extraction ready event
        extraction_event = ExtractionStartEvent(
            entity_id="extraction_1",
            timestamp=0.0,
            event_type="extraction_start",
            source_module="airdrop",
            drop_zone_id="Luzon_DZ",
        )
        module._handle_extraction_ready(extraction_event)

        # Run simulation past deliberation
        env.run(until=5.0)

        # Verify CargoReleaseDeniedEvent was published
        denied_events = [
            e for e in published_events if isinstance(e, CargoReleaseDeniedEvent)
        ]
        assert len(denied_events) == 1
        assert denied_events[0].loadmaster_name == "SGT_Smith"
        assert denied_events[0].drop_zone_node == "Luzon_DZ"

        # Verify no confirmed event
        confirmed_events = [
            e for e in published_events if isinstance(e, CargoReleaseConfirmedEvent)
        ]
        assert len(confirmed_events) == 0


class TestNoReadyLoadmaster:
    """Test behavior when no READY Loadmaster is available."""

    def test_no_ready_loadmaster_publishes_deferred_event(self):
        """Req 7.4: No READY Loadmaster publishes decision-deferred event."""
        env = simpy.Environment()
        event_bus = EventBus()

        # Loadmaster is UNAVAILABLE
        loadmaster = _make_loadmaster(readiness=ReadinessState.UNAVAILABLE)
        config = _make_config_mock(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=5.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Trigger extraction ready event
        extraction_event = ExtractionStartEvent(
            entity_id="extraction_1",
            timestamp=0.0,
            event_type="extraction_start",
            source_module="airdrop",
            drop_zone_id="Luzon_DZ",
        )
        module._handle_extraction_ready(extraction_event)

        # Run simulation past timeout
        env.run(until=10.0)

        # Verify DecisionDeferredEvent was published
        deferred_events = [
            e for e in published_events if isinstance(e, DecisionDeferredEvent)
        ]
        assert len(deferred_events) >= 1
        assert deferred_events[0].assigned_role == "Loadmaster"

    def test_timeout_publishes_timeout_event(self):
        """Req 7.5: Timeout publishes decision-timeout, extraction does NOT initiate."""
        env = simpy.Environment()
        event_bus = EventBus()

        # Loadmaster is UNAVAILABLE — will never become READY
        loadmaster = _make_loadmaster(readiness=ReadinessState.UNAVAILABLE)
        config = _make_config_mock(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=5.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Trigger extraction ready event
        extraction_event = ExtractionStartEvent(
            entity_id="extraction_1",
            timestamp=0.0,
            event_type="extraction_start",
            source_module="airdrop",
            drop_zone_id="Luzon_DZ",
        )
        module._handle_extraction_ready(extraction_event)

        # Run simulation past timeout
        env.run(until=10.0)

        # Verify DecisionTimeoutEvent was published
        timeout_events = [
            e for e in published_events if isinstance(e, DecisionTimeoutEvent)
        ]
        assert len(timeout_events) == 1
        assert timeout_events[0].decision_id == "loadmaster_release"
        assert timeout_events[0].assigned_role == "Loadmaster"

        # Verify NO cargo release confirmed event (extraction does NOT initiate)
        confirmed_events = [
            e for e in published_events if isinstance(e, CargoReleaseConfirmedEvent)
        ]
        assert len(confirmed_events) == 0


class TestLoadmasterAtWrongStation:
    """Test that Loadmaster must be at the drop zone node."""

    def test_loadmaster_at_different_station_defers(self):
        """Loadmaster at different duty station doesn't satisfy drop zone requirement."""
        env = simpy.Environment()
        event_bus = EventBus()

        # Loadmaster at wrong station
        loadmaster = _make_loadmaster(duty_station="Hickam_AFB")
        config = _make_config_mock(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=3.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Trigger extraction at a different drop zone
        extraction_event = ExtractionStartEvent(
            entity_id="extraction_1",
            timestamp=0.0,
            event_type="extraction_start",
            source_module="airdrop",
            drop_zone_id="Luzon_DZ",
        )
        module._handle_extraction_ready(extraction_event)

        # Run simulation
        env.run(until=10.0)

        # Should defer because Loadmaster is not at Luzon_DZ
        deferred_events = [
            e for e in published_events if isinstance(e, DecisionDeferredEvent)
        ]
        assert len(deferred_events) >= 1

        # Should timeout since Loadmaster never at correct station
        timeout_events = [
            e for e in published_events if isinstance(e, DecisionTimeoutEvent)
        ]
        assert len(timeout_events) == 1


class TestDynamicDecisionPoint:
    """Test that dynamic decision point is created when no config exists."""

    def test_no_configured_decision_point_uses_dynamic(self):
        """When no DecisionPointConfig for Loadmaster, creates dynamic one."""
        env = simpy.Environment()
        event_bus = EventBus()

        loadmaster = _make_loadmaster()
        # No decision points configured
        config = _make_config_mock(roster=[loadmaster], decision_points=[])

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect published events
        published_events: list[SimulationEvent] = []
        event_bus.subscribe_all(
            handler=lambda e: published_events.append(e),
            subscriber_id="test_collector",
        )

        # Trigger extraction
        extraction_event = ExtractionStartEvent(
            entity_id="extraction_1",
            timestamp=0.0,
            event_type="extraction_start",
            source_module="airdrop",
            drop_zone_id="Luzon_DZ",
        )
        module._handle_extraction_ready(extraction_event)

        # Run simulation
        env.run(until=5.0)

        # Should still produce confirmed event using dynamic decision point
        confirmed_events = [
            e for e in published_events if isinstance(e, CargoReleaseConfirmedEvent)
        ]
        assert len(confirmed_events) == 1
        assert confirmed_events[0].loadmaster_name == "SGT_Smith"
