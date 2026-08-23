"""Integration tests for credential-based access control via PersonnelModule.

Exercises the `check_cross_domain_access` method on PersonnelModule with a
realistic setup including personnel with various credentials and a cross-domain
gateway with enclaves at different classification levels.

Requirements: 6.2, 6.3, 6.4, 6.5
"""

import simpy
import pytest

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    ClearanceLevel,
    CredentialType,
    Personnel,
    PersonnelConfig,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    AccessDeniedEvent,
    AuthenticationSuccessEvent,
    ClearanceInsufficientEvent,
)
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.models.security import (
    CrossDomainGatewayConfig,
    RoutingRule,
    SecurityEnclave,
)
from enterprise_sim.modules.personnel import PersonnelModule


# === Test Fixtures ===


def _make_route_graph():
    """Create a basic route graph for testing."""
    return RouteGraphConfig(
        nodes=[
            RouteNode(
                entity_id="n1", name="base", node_type="base",
                latitude=0.0, longitude=0.0,
            ),
            RouteNode(
                entity_id="n2", name="forward_post", node_type="waypoint",
                latitude=1.0, longitude=1.0,
            ),
        ],
        edges=[
            RouteEdge(source="base", destination="forward_post", distance_nm=50.0),
        ],
        origin="base",
        destination="forward_post",
    )


def _make_aircraft_config():
    """Create a minimal aircraft config."""
    return AircraftConfig(
        entity_id="ac1",
        aircraft_type="C-130",
        lift_to_drag_ratio=10.0,
        specific_fuel_consumption=0.5,
        initial_fuel_weight=10000.0,
        max_fuel_capacity=12000.0,
    )


def _make_cross_domain_gateway():
    """Create a cross-domain gateway with enclaves at different classification levels.

    Enclaves:
    - NIPRNET: UNCLASSIFIED
    - SIPRNET: SECRET
    - JWICS: TOP_SECRET
    """
    return CrossDomainGatewayConfig(
        sanitization_latency=3.0,
        enclaves=[
            SecurityEnclave(
                entity_id="e1", name="NIPRNET",
                classification_level="UNCLASSIFIED",
            ),
            SecurityEnclave(
                entity_id="e2", name="SIPRNET",
                classification_level="SECRET",
            ),
            SecurityEnclave(
                entity_id="e3", name="JWICS",
                classification_level="TOP_SECRET",
            ),
        ],
        routing_rules=[
            RoutingRule(
                source_enclave="NIPRNET",
                destination_enclave="SIPRNET",
                permitted_classifications=["SECRET"],
            ),
            RoutingRule(
                source_enclave="SIPRNET",
                destination_enclave="JWICS",
                permitted_classifications=["TOP_SECRET"],
            ),
        ],
    )


def _make_personnel_config():
    """Create a personnel roster with members at various clearance levels.

    Personnel:
    - Maj. Collins: TOP_SECRET clearance, authorized for NIPRNET, SIPRNET, JWICS
    - Sgt. Davis: SECRET clearance, authorized for NIPRNET, SIPRNET only
    - Cpl. Evans: UNCLASSIFIED clearance, authorized for NIPRNET only
    """
    return PersonnelConfig(
        roster=[
            Personnel(
                entity_id="p1",
                name="Collins",
                role="Pilot",
                duty_station="base",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.TOP_SECRET,
                    authorized_enclaves=["NIPRNET", "SIPRNET", "JWICS"],
                ),
                readiness_state=ReadinessState.READY,
            ),
            Personnel(
                entity_id="p2",
                name="Davis",
                role="Loadmaster",
                duty_station="base",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.SECRET,
                    authorized_enclaves=["NIPRNET", "SIPRNET"],
                ),
                readiness_state=ReadinessState.READY,
            ),
            Personnel(
                entity_id="p3",
                name="Evans",
                role="Loadmaster",
                duty_station="forward_post",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.UNCLASSIFIED,
                    authorized_enclaves=["NIPRNET"],
                ),
                readiness_state=ReadinessState.READY,
            ),
        ],
    )


def _create_initialized_personnel_module():
    """Create and initialize a PersonnelModule with a realistic config.

    Returns (module, event_bus, captured_events list).
    """
    env = simpy.Environment()
    event_bus = EventBus()
    captured_events = []
    event_bus.subscribe_all(lambda e: captured_events.append(e), "test_capture")

    config = MissionConfiguration(
        mission_id="credential_test",
        mission_name="Credential Access Control Test",
        route_graph=_make_route_graph(),
        aircraft=_make_aircraft_config(),
        cross_domain_gateway=_make_cross_domain_gateway(),
        personnel_parameters=_make_personnel_config(),
        active_modules=["personnel"],
    )

    module = PersonnelModule()
    module.initialize(env, event_bus, config)

    return module, event_bus, captured_events


# === Tests ===


class TestAuthenticationSuccessAllowsCrossDomainTransit:
    """Test that authentication success allows cross-domain transit.

    Requirements: 6.2, 6.4
    """

    def test_authorized_enclave_with_sufficient_clearance_returns_true(self):
        """Collins (TOP_SECRET) accessing SIPRNET (SECRET) → access granted."""
        module, event_bus, events = _create_initialized_personnel_module()

        result = module.check_cross_domain_access("Collins", "SIPRNET", timestamp=10.0)

        assert result is True

    def test_authentication_success_event_published(self):
        """Successful authentication publishes AuthenticationSuccessEvent."""
        module, event_bus, events = _create_initialized_personnel_module()

        module.check_cross_domain_access("Collins", "SIPRNET", timestamp=10.0)

        success_events = [e for e in events if isinstance(e, AuthenticationSuccessEvent)]
        assert len(success_events) == 1
        assert success_events[0].personnel_name == "Collins"
        assert success_events[0].enclave_name == "SIPRNET"

    def test_top_secret_personnel_accesses_all_authorized_enclaves(self):
        """Collins (TOP_SECRET, authorized for all) can access any enclave."""
        module, event_bus, events = _create_initialized_personnel_module()

        # Access NIPRNET (UNCLASSIFIED)
        assert module.check_cross_domain_access("Collins", "NIPRNET", timestamp=1.0) is True
        # Access SIPRNET (SECRET)
        assert module.check_cross_domain_access("Collins", "SIPRNET", timestamp=2.0) is True
        # Access JWICS (TOP_SECRET)
        assert module.check_cross_domain_access("Collins", "JWICS", timestamp=3.0) is True

        success_events = [e for e in events if isinstance(e, AuthenticationSuccessEvent)]
        assert len(success_events) == 3

    def test_secret_clearance_accesses_secret_enclave(self):
        """Davis (SECRET) accessing SIPRNET (SECRET) → access granted."""
        module, event_bus, events = _create_initialized_personnel_module()

        result = module.check_cross_domain_access("Davis", "SIPRNET", timestamp=5.0)

        assert result is True
        success_events = [e for e in events if isinstance(e, AuthenticationSuccessEvent)]
        assert len(success_events) == 1
        assert success_events[0].personnel_name == "Davis"


class TestAccessDeniedBlocksAndDiscardsMessage:
    """Test that access denied blocks and discards message.

    Requirements: 6.2, 6.3
    """

    def test_unauthorized_enclave_returns_false(self):
        """Evans (authorized for NIPRNET only) accessing SIPRNET → denied."""
        module, event_bus, events = _create_initialized_personnel_module()

        result = module.check_cross_domain_access("Evans", "SIPRNET", timestamp=10.0)

        assert result is False

    def test_access_denied_event_published(self):
        """Unauthorized enclave access publishes AccessDeniedEvent."""
        module, event_bus, events = _create_initialized_personnel_module()

        module.check_cross_domain_access("Evans", "SIPRNET", timestamp=10.0)

        denied_events = [e for e in events if isinstance(e, AccessDeniedEvent)]
        assert len(denied_events) == 1
        assert denied_events[0].personnel_name == "Evans"
        assert denied_events[0].credential_type == "CAC_PKI"
        assert denied_events[0].denied_enclave == "SIPRNET"

    def test_no_success_event_on_denied_access(self):
        """No AuthenticationSuccessEvent when access is denied."""
        module, event_bus, events = _create_initialized_personnel_module()

        module.check_cross_domain_access("Evans", "SIPRNET", timestamp=10.0)

        success_events = [e for e in events if isinstance(e, AuthenticationSuccessEvent)]
        assert len(success_events) == 0

    def test_davis_unauthorized_for_jwics(self):
        """Davis (authorized for NIPRNET, SIPRNET) accessing JWICS → denied."""
        module, event_bus, events = _create_initialized_personnel_module()

        result = module.check_cross_domain_access("Davis", "JWICS", timestamp=15.0)

        assert result is False
        denied_events = [e for e in events if isinstance(e, AccessDeniedEvent)]
        assert len(denied_events) == 1
        assert denied_events[0].denied_enclave == "JWICS"


class TestClearanceInsufficientBlocksAccess:
    """Test that insufficient clearance blocks access.

    Requirements: 6.5
    """

    def test_unclassified_clearance_accessing_secret_enclave_blocked(self):
        """Evans (UNCLASSIFIED, authorized for NIPRNET) accessing NIPRNET (UNCLASSIFIED)
        works, but if we give Evans authorization for SIPRNET, clearance would block."""
        # For this test, we need a scenario where enclave IS in authorized_enclaves
        # but clearance is insufficient. Evans only has NIPRNET authorized, so we
        # build a custom config with Evans authorized for SIPRNET but with UNCLASSIFIED clearance.
        env = simpy.Environment()
        event_bus = EventBus()
        captured_events = []
        event_bus.subscribe_all(lambda e: captured_events.append(e), "test_capture")

        personnel_config = PersonnelConfig(
            roster=[
                Personnel(
                    entity_id="p1",
                    name="LowClearanceOp",
                    role="Pilot",
                    duty_station="base",
                    credential=AuthenticationCredential(
                        credential_type=CredentialType.CAC_PKI,
                        clearance_level=ClearanceLevel.UNCLASSIFIED,
                        # Authorized for SIPRNET but clearance is UNCLASSIFIED
                        authorized_enclaves=["NIPRNET", "SIPRNET"],
                    ),
                    readiness_state=ReadinessState.READY,
                ),
            ],
        )

        config = MissionConfiguration(
            mission_id="clearance_test",
            mission_name="Clearance Insufficient Test",
            route_graph=_make_route_graph(),
            aircraft=_make_aircraft_config(),
            cross_domain_gateway=_make_cross_domain_gateway(),
            personnel_parameters=personnel_config,
            active_modules=["personnel"],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # SIPRNET is SECRET classification; LowClearanceOp has UNCLASSIFIED clearance
        result = module.check_cross_domain_access("LowClearanceOp", "SIPRNET", timestamp=20.0)

        assert result is False

    def test_clearance_insufficient_event_published(self):
        """ClearanceInsufficientEvent published when clearance < enclave classification."""
        env = simpy.Environment()
        event_bus = EventBus()
        captured_events = []
        event_bus.subscribe_all(lambda e: captured_events.append(e), "test_capture")

        personnel_config = PersonnelConfig(
            roster=[
                Personnel(
                    entity_id="p1",
                    name="SecretOp",
                    role="Pilot",
                    duty_station="base",
                    credential=AuthenticationCredential(
                        credential_type=CredentialType.CAC_PKI,
                        clearance_level=ClearanceLevel.SECRET,
                        # Authorized for JWICS but clearance is only SECRET
                        authorized_enclaves=["NIPRNET", "SIPRNET", "JWICS"],
                    ),
                    readiness_state=ReadinessState.READY,
                ),
            ],
        )

        config = MissionConfiguration(
            mission_id="clearance_test",
            mission_name="Clearance Insufficient Event Test",
            route_graph=_make_route_graph(),
            aircraft=_make_aircraft_config(),
            cross_domain_gateway=_make_cross_domain_gateway(),
            personnel_parameters=personnel_config,
            active_modules=["personnel"],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        # JWICS is TOP_SECRET; SecretOp has SECRET clearance
        result = module.check_cross_domain_access("SecretOp", "JWICS", timestamp=25.0)

        assert result is False
        insufficient_events = [
            e for e in captured_events if isinstance(e, ClearanceInsufficientEvent)
        ]
        assert len(insufficient_events) == 1
        event = insufficient_events[0]
        assert event.personnel_name == "SecretOp"
        assert event.personnel_clearance == "SECRET"
        assert event.enclave_name == "JWICS"
        assert event.required_classification == "TOP_SECRET"

    def test_no_success_event_on_insufficient_clearance(self):
        """No AuthenticationSuccessEvent when clearance is insufficient."""
        env = simpy.Environment()
        event_bus = EventBus()
        captured_events = []
        event_bus.subscribe_all(lambda e: captured_events.append(e), "test_capture")

        personnel_config = PersonnelConfig(
            roster=[
                Personnel(
                    entity_id="p1",
                    name="SecretOp",
                    role="Pilot",
                    duty_station="base",
                    credential=AuthenticationCredential(
                        credential_type=CredentialType.CAC_PKI,
                        clearance_level=ClearanceLevel.SECRET,
                        authorized_enclaves=["NIPRNET", "SIPRNET", "JWICS"],
                    ),
                    readiness_state=ReadinessState.READY,
                ),
            ],
        )

        config = MissionConfiguration(
            mission_id="clearance_test",
            mission_name="No Success Event Test",
            route_graph=_make_route_graph(),
            aircraft=_make_aircraft_config(),
            cross_domain_gateway=_make_cross_domain_gateway(),
            personnel_parameters=personnel_config,
            active_modules=["personnel"],
        )

        module = PersonnelModule()
        module.initialize(env, event_bus, config)

        module.check_cross_domain_access("SecretOp", "JWICS", timestamp=30.0)

        success_events = [
            e for e in captured_events if isinstance(e, AuthenticationSuccessEvent)
        ]
        assert len(success_events) == 0
