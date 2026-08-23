"""Integration test for the decision-airdrop flow.

Exercises the full flow from ExtractionStartEvent through the PersonnelModule
to the cargo release events. Tests the Loadmaster decision point gating
extraction with APPROVE, DENY, and deferred (no READY Loadmaster) outcomes.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
"""

from __future__ import annotations

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
    MissionBlockedEvent,
)
from enterprise_sim.modules.personnel import PersonnelModule


# === Helpers ===


def _make_credential() -> AuthenticationCredential:
    """Create a default credential for test personnel."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=ClearanceLevel.SECRET,
        authorized_enclaves=["NIPR"],
    )


def _make_loadmaster(
    name: str = "SGT_Smith",
    duty_station: str = "Luzon_DZ",
    readiness: ReadinessState = ReadinessState.READY,
) -> Personnel:
    """Create a Loadmaster personnel member for tests."""
    return Personnel(
        entity_id=f"person_{name}",
        name=name,
        role="Loadmaster",
        duty_station=duty_station,
        credential=_make_credential(),
        readiness_state=readiness,
    )


def _make_pilot(
    name: str = "CPT_Jones",
    duty_station: str = "Hickam_AFB",
) -> Personnel:
    """Create a Pilot personnel member (non-Loadmaster) for roster diversity."""
    return Personnel(
        entity_id=f"person_{name}",
        name=name,
        role="Pilot",
        duty_station=duty_station,
        credential=_make_credential(),
        readiness_state=ReadinessState.READY,
    )


def _make_config(
    roster: list[Personnel],
    decision_points: list[DecisionPointConfig] | None = None,
):
    """Create a realistic mock config with route graph for PersonnelModule.

    Includes multiple nodes in the route graph and a multi-person roster
    to exercise full integration paths.
    """
    # Build route graph nodes from all unique duty stations in roster
    all_stations = {p.duty_station for p in roster}
    # Always include the drop zone
    all_stations.add("Luzon_DZ")

    class MockNode:
        def __init__(self, name: str):
            self.name = name

    class MockRouteGraph:
        nodes = [MockNode(s) for s in all_stations]

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


def _make_extraction_event(drop_zone_id: str = "Luzon_DZ") -> ExtractionStartEvent:
    """Create an ExtractionStartEvent simulating the Airdrop module trigger."""
    return ExtractionStartEvent(
        entity_id="extraction_integration_1",
        timestamp=0.0,
        event_type="extraction_start",
        source_module="airdrop",
        drop_zone_id=drop_zone_id,
    )


def _collect_events(event_bus: EventBus) -> list[SimulationEvent]:
    """Subscribe a wildcard collector and return the shared event list."""
    events: list[SimulationEvent] = []
    event_bus.subscribe_all(
        handler=lambda e: events.append(e),
        subscriber_id="integration_test_collector",
    )
    return events


# === Integration Tests ===


class TestLoadmasterApproveEnablesExtraction:
    """Integration: Loadmaster APPROVE enables extraction (Req 7.1, 7.2)."""

    def test_full_flow_approve_produces_cargo_release_confirmed(self):
        """End-to-end: ExtractionStartEvent → PersonnelModule → CargoReleaseConfirmedEvent.

        Verifies:
        - The PersonnelModule receives the extraction event via the EventBus
        - A READY Loadmaster at the drop zone is selected for the decision point
        - Deliberation consumes the configured duration in simulation time
        - CargoReleaseConfirmedEvent is published with correct loadmaster name and drop zone
        - DecisionRenderedEvent is published with APPROVE outcome
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Realistic roster: Loadmaster at drop zone + Pilot elsewhere
        loadmaster = _make_loadmaster(name="SGT_Smith", duty_station="Luzon_DZ")
        pilot = _make_pilot(name="CPT_Jones", duty_station="Hickam_AFB")

        config = _make_config(
            roster=[pilot, loadmaster],  # Loadmaster is second in roster order
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=3.0,
                    timeout=15.0,
                )
            ],
        )

        # Initialize the PersonnelModule (full lifecycle)
        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # Collect all events published to the bus
        published = _collect_events(event_bus)

        # Simulate the Airdrop module publishing ExtractionStartEvent
        extraction_event = _make_extraction_event(drop_zone_id="Luzon_DZ")
        event_bus.publish(extraction_event)

        # Advance simulation past deliberation duration (3.0)
        env.run(until=10.0)

        # Verify CargoReleaseConfirmedEvent was published
        confirmed = [e for e in published if isinstance(e, CargoReleaseConfirmedEvent)]
        assert len(confirmed) == 1, (
            f"Expected exactly 1 CargoReleaseConfirmedEvent, got {len(confirmed)}"
        )
        assert confirmed[0].loadmaster_name == "SGT_Smith"
        assert confirmed[0].drop_zone_node == "Luzon_DZ"

        # Verify DecisionRenderedEvent with APPROVE outcome
        rendered = [e for e in published if isinstance(e, DecisionRenderedEvent)]
        assert len(rendered) == 1
        assert rendered[0].outcome == "APPROVE"
        assert rendered[0].personnel_name == "SGT_Smith"

        # Verify deliberation consumed correct simulation time
        assert rendered[0].timestamp == 3.0, (
            f"Deliberation should complete at t=3.0, got t={rendered[0].timestamp}"
        )

    def test_approve_with_multiple_loadmasters_selects_first_ready(self):
        """When multiple Loadmasters exist, the first READY one in roster order is selected.

        Validates Req 7.1 combined with the roster-order resolution from Req 3.2.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Two Loadmasters at the drop zone — first in roster should be selected
        loadmaster_1 = _make_loadmaster(name="SGT_Alpha", duty_station="Luzon_DZ")
        loadmaster_2 = _make_loadmaster(name="SGT_Beta", duty_station="Luzon_DZ")

        config = _make_config(
            roster=[loadmaster_1, loadmaster_2],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=10.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        published = _collect_events(event_bus)

        extraction_event = _make_extraction_event()
        event_bus.publish(extraction_event)
        env.run(until=5.0)

        confirmed = [e for e in published if isinstance(e, CargoReleaseConfirmedEvent)]
        assert len(confirmed) == 1
        # First Loadmaster in roster order should be selected
        assert confirmed[0].loadmaster_name == "SGT_Alpha"


class TestLoadmasterDenyBlocksExtraction:
    """Integration: Loadmaster DENY blocks extraction (Req 7.1, 7.3)."""

    def test_full_flow_deny_produces_cargo_release_denied(self):
        """End-to-end: ExtractionStartEvent → DENY → CargoReleaseDeniedEvent.

        Verifies:
        - CargoReleaseDeniedEvent is published when outcome is DENY
        - No CargoReleaseConfirmedEvent is published (extraction does NOT initiate)
        - MissionBlockedEvent is published identifying the blocking personnel
        """
        env = simpy.Environment()
        event_bus = EventBus()

        loadmaster = _make_loadmaster(name="SGT_Smith", duty_station="Luzon_DZ")
        pilot = _make_pilot(name="CPT_Jones", duty_station="Hickam_AFB")

        config = _make_config(
            roster=[loadmaster, pilot],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=10.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)
        # Override the decision outcome to DENY
        module.set_loadmaster_outcome(DecisionOutcome.DENY)

        published = _collect_events(event_bus)

        # Trigger extraction
        extraction_event = _make_extraction_event()
        event_bus.publish(extraction_event)
        env.run(until=5.0)

        # Verify CargoReleaseDeniedEvent was published
        denied = [e for e in published if isinstance(e, CargoReleaseDeniedEvent)]
        assert len(denied) == 1, (
            f"Expected exactly 1 CargoReleaseDeniedEvent, got {len(denied)}"
        )
        assert denied[0].loadmaster_name == "SGT_Smith"
        assert denied[0].drop_zone_node == "Luzon_DZ"

        # Verify NO CargoReleaseConfirmedEvent (extraction blocked)
        confirmed = [e for e in published if isinstance(e, CargoReleaseConfirmedEvent)]
        assert len(confirmed) == 0, (
            "No CargoReleaseConfirmedEvent should be published on DENY"
        )

        # Verify MissionBlockedEvent was published
        blocked = [e for e in published if isinstance(e, MissionBlockedEvent)]
        assert len(blocked) == 1
        assert blocked[0].blocking_personnel == "SGT_Smith"
        assert "denied" in blocked[0].reason.lower() or "deny" in blocked[0].reason.lower()

    def test_deny_decision_rendered_event_has_correct_outcome(self):
        """DecisionRenderedEvent shows DENY outcome on the rendered decision."""
        env = simpy.Environment()
        event_bus = EventBus()

        loadmaster = _make_loadmaster()
        config = _make_config(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=1.0,
                    timeout=10.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)
        module.set_loadmaster_outcome(DecisionOutcome.DENY)

        published = _collect_events(event_bus)

        event_bus.publish(_make_extraction_event())
        env.run(until=5.0)

        rendered = [e for e in published if isinstance(e, DecisionRenderedEvent)]
        assert len(rendered) == 1
        assert rendered[0].outcome == "DENY"


class TestNoReadyLoadmasterDefersExtraction:
    """Integration: No READY Loadmaster defers extraction (Req 7.4, 7.5)."""

    def test_unavailable_loadmaster_publishes_deferred_and_timeout(self):
        """End-to-end: No READY Loadmaster → DecisionDeferredEvent → DecisionTimeoutEvent.

        Verifies:
        - DecisionDeferredEvent is published immediately when no READY Loadmaster found
        - DecisionTimeoutEvent is published when timeout expires
        - No CargoReleaseConfirmedEvent is published (extraction does NOT initiate)
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Loadmaster is UNAVAILABLE at the drop zone
        loadmaster = _make_loadmaster(
            name="SGT_Smith",
            duty_station="Luzon_DZ",
            readiness=ReadinessState.UNAVAILABLE,
        )
        pilot = _make_pilot()

        config = _make_config(
            roster=[loadmaster, pilot],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=5.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        published = _collect_events(event_bus)

        extraction_event = _make_extraction_event()
        event_bus.publish(extraction_event)

        # Run past timeout (5.0)
        env.run(until=10.0)

        # Verify DecisionDeferredEvent was published
        deferred = [e for e in published if isinstance(e, DecisionDeferredEvent)]
        assert len(deferred) >= 1, (
            "Expected at least 1 DecisionDeferredEvent when no READY Loadmaster"
        )
        assert deferred[0].assigned_role == "Loadmaster"

        # Verify DecisionTimeoutEvent was published
        timeout_events = [e for e in published if isinstance(e, DecisionTimeoutEvent)]
        assert len(timeout_events) == 1, (
            f"Expected exactly 1 DecisionTimeoutEvent, got {len(timeout_events)}"
        )
        assert timeout_events[0].assigned_role == "Loadmaster"

        # Verify NO extraction proceeds (no cargo release confirmed)
        confirmed = [e for e in published if isinstance(e, CargoReleaseConfirmedEvent)]
        assert len(confirmed) == 0, (
            "Extraction must NOT initiate when Loadmaster decision times out"
        )

    def test_incapacitated_loadmaster_also_defers(self):
        """INCAPACITATED Loadmaster also triggers deferral (not just UNAVAILABLE)."""
        env = simpy.Environment()
        event_bus = EventBus()

        loadmaster = _make_loadmaster(
            readiness=ReadinessState.INCAPACITATED,
        )

        config = _make_config(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=4.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        published = _collect_events(event_bus)

        event_bus.publish(_make_extraction_event())
        env.run(until=8.0)

        deferred = [e for e in published if isinstance(e, DecisionDeferredEvent)]
        assert len(deferred) >= 1

        timeout_events = [e for e in published if isinstance(e, DecisionTimeoutEvent)]
        assert len(timeout_events) == 1

    def test_loadmaster_at_wrong_station_defers_extraction(self):
        """A READY Loadmaster at a different duty station does not satisfy the drop zone.

        The loadmaster must be stationed at the drop zone node to approve extraction.
        """
        env = simpy.Environment()
        event_bus = EventBus()

        # Loadmaster is READY but at a different station
        loadmaster = _make_loadmaster(
            name="SGT_Smith",
            duty_station="Hickam_AFB",
            readiness=ReadinessState.READY,
        )

        config = _make_config(
            roster=[loadmaster],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=4.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        published = _collect_events(event_bus)

        # Extraction at Luzon_DZ, but Loadmaster is at Hickam_AFB
        event_bus.publish(_make_extraction_event(drop_zone_id="Luzon_DZ"))
        env.run(until=8.0)

        # Should defer because no Loadmaster at the correct drop zone
        deferred = [e for e in published if isinstance(e, DecisionDeferredEvent)]
        assert len(deferred) >= 1

        # Should timeout
        timeout_events = [e for e in published if isinstance(e, DecisionTimeoutEvent)]
        assert len(timeout_events) == 1

        # No extraction should proceed
        confirmed = [e for e in published if isinstance(e, CargoReleaseConfirmedEvent)]
        assert len(confirmed) == 0

    def test_no_loadmaster_in_roster_defers_extraction(self):
        """If no personnel with Loadmaster role exists at all, extraction defers."""
        env = simpy.Environment()
        event_bus = EventBus()

        # Only a Pilot in the roster — no Loadmaster
        pilot = _make_pilot(name="CPT_Jones", duty_station="Luzon_DZ")

        config = _make_config(
            roster=[pilot],
            decision_points=[
                DecisionPointConfig(
                    decision_id="loadmaster_cargo_release",
                    assigned_role="Loadmaster",
                    decision_type=DecisionType.APPROVE_DENY,
                    deliberation_duration=2.0,
                    timeout=3.0,
                )
            ],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        published = _collect_events(event_bus)

        event_bus.publish(_make_extraction_event())
        env.run(until=8.0)

        deferred = [e for e in published if isinstance(e, DecisionDeferredEvent)]
        assert len(deferred) >= 1

        timeout_events = [e for e in published if isinstance(e, DecisionTimeoutEvent)]
        assert len(timeout_events) == 1

        confirmed = [e for e in published if isinstance(e, CargoReleaseConfirmedEvent)]
        assert len(confirmed) == 0
