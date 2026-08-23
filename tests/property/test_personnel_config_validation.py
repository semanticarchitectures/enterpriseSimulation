"""Property tests for Personnel Module configuration validation.

**Property 4: Role Validation Against Configuration**
**Property 5: Duty Station Cross-Reference Validation**
**Property 20: Authorization Gate Binding Validation**
**Property 29: Configuration Validation Error Aggregation**
**Validates: Requirements 1.4, 1.7, 1.8, 5.1, 5.5, 9.2**

Property 4: For any Personnel entry whose role value is not in the set of recognized
roles defined in the MissionConfiguration, validation SHALL reject the entry and
identify the personnel name and unrecognized role.

Property 5: For any Personnel entry whose duty_station does not match a node name in
the RouteGraph, validation SHALL reject the entry and identify the personnel name and
unrecognized duty_station.

Property 20: For any AuthorizationGateBinding that references a gate name not defined
in the authorization_chain configuration or a role not present in the roster,
validation SHALL reject the binding.

Property 29: For any personnel configuration with multiple constraint violations,
validation SHALL return all errors found, not just the first encountered.
"""

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.authorization import AuthorizationChainConfig, AuthorizationGate
from enterprise_sim.models.messaging import C2MessageType
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    AuthorizationGateBinding,
    ClearanceLevel,
    CredentialType,
    DecisionPointConfig,
    DecisionType,
    MessageRoutingRule,
    Personnel,
    PersonnelConfig,
    ReadinessState,
)
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.models.security import (
    CrossDomainGatewayConfig,
    RoutingRule,
    SecurityEnclave,
)
from enterprise_sim.modules.personnel import PersonnelModule


# === Reusable Helpers ===

# Known valid roles used across the roster
KNOWN_ROLES = ["Pilot", "Loadmaster", "Navigator", "AOC_Commander", "Engineer"]

# Known valid node names for route graph
KNOWN_NODES = ["Hickam_AFB", "Luzon_DZ", "Kadena_AB", "Clark_AB", "Manila"]

# Known valid gate names for authorization chain
KNOWN_GATES = ["DIPCLEAR", "EXORD", "Title10"]

# Known valid enclave names
KNOWN_ENCLAVES = ["NIPR", "SIPR"]


def _make_route_graph(node_names: list[str] | None = None) -> RouteGraphConfig:
    """Create a minimal valid RouteGraphConfig with given node names."""
    if node_names is None:
        node_names = KNOWN_NODES
    nodes = [
        RouteNode(entity_id=f"node-{i}", name=name)
        for i, name in enumerate(node_names)
    ]
    # Create edges forming a chain: node0 -> node1 -> ... -> nodeN
    edges = [
        RouteEdge(source=node_names[i], destination=node_names[i + 1], distance_nm=100.0)
        for i in range(len(node_names) - 1)
    ]
    return RouteGraphConfig(
        nodes=nodes,
        edges=edges,
        origin=node_names[0],
        destination=node_names[-1],
    )


def _make_aircraft() -> AircraftConfig:
    """Create a minimal valid AircraftConfig."""
    return AircraftConfig(
        entity_id="test-aircraft",
        aircraft_type="C-130J",
        lift_to_drag_ratio=15.0,
        specific_fuel_consumption=0.5,
        initial_fuel_weight=10000.0,
        max_fuel_capacity=12000.0,
    )


def _make_authorization_chain(gate_names: list[str] | None = None) -> AuthorizationChainConfig:
    """Create a minimal valid AuthorizationChainConfig with given gate names."""
    if gate_names is None:
        gate_names = KNOWN_GATES
    gates = [
        AuthorizationGate(
            entity_id=f"gate-{i}",
            gate_name=name,
            duration=5.0,
            timestamp=0.0,
            start_time=0.0,
        )
        for i, name in enumerate(gate_names)
    ]
    return AuthorizationChainConfig(
        chain_id="test-chain",
        chain_type="sequential",
        gates=gates,
    )


def _make_cross_domain_gateway(
    enclave_names: list[str] | None = None,
) -> CrossDomainGatewayConfig:
    """Create a minimal valid CrossDomainGatewayConfig."""
    if enclave_names is None:
        enclave_names = KNOWN_ENCLAVES
    enclaves = [
        SecurityEnclave(
            entity_id=f"enclave-{i}",
            name=name,
            classification_level="SECRET",
        )
        for i, name in enumerate(enclave_names)
    ]
    routing_rules = [
        RoutingRule(
            source_enclave=enclave_names[0],
            destination_enclave=enclave_names[1],
            permitted_classifications=["SECRET"],
        )
    ]
    return CrossDomainGatewayConfig(
        sanitization_latency=1.0,
        enclaves=enclaves,
        routing_rules=routing_rules,
    )


def _make_personnel(
    name: str,
    role: str = "Pilot",
    duty_station: str = "Hickam_AFB",
    enclaves: list[str] | None = None,
) -> Personnel:
    """Create a valid Personnel entry."""
    if enclaves is None:
        enclaves = ["NIPR"]
    return Personnel(
        entity_id=f"personnel-{name}",
        name=name,
        role=role,
        duty_station=duty_station,
        credential=AuthenticationCredential(
            credential_type=CredentialType.CAC_PKI,
            clearance_level=ClearanceLevel.SECRET,
            authorized_enclaves=enclaves,
        ),
        readiness_state=ReadinessState.READY,
    )


def _make_mission_config(
    personnel_config: PersonnelConfig,
    route_graph: RouteGraphConfig | None = None,
    authorization_chain: AuthorizationChainConfig | None = None,
    cross_domain_gateway: CrossDomainGatewayConfig | None = None,
    c2_messages: list[C2MessageType] | None = None,
) -> MissionConfiguration:
    """Create a MissionConfiguration with the given personnel section."""
    if route_graph is None:
        route_graph = _make_route_graph()

    config = MissionConfiguration(
        mission_id="test-mission",
        mission_name="Test Mission",
        route_graph=route_graph,
        aircraft=_make_aircraft(),
        authorization_chain=authorization_chain,
        cross_domain_gateway=cross_domain_gateway,
        c2_messages=c2_messages,
        personnel_parameters=personnel_config,
        active_modules=["personnel"],
    )
    return config


# === Hypothesis Strategies ===

# Strategy for generating invalid role names (not in KNOWN_ROLES)
invalid_role_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "" and s not in KNOWN_ROLES)

# Strategy for generating invalid duty station names (not in KNOWN_NODES)
invalid_duty_station_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "" and s not in KNOWN_NODES)

# Strategy for generating invalid gate names (not in KNOWN_GATES)
invalid_gate_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "" and s not in KNOWN_GATES)


# === Property 4: Role Validation Against Configuration ===
# Feature: personnel-module, Property 4: Role Validation Against Configuration


class TestRoleValidationAgainstConfiguration:
    """Property 4: Role Validation Against Configuration.

    **Validates: Requirements 1.4, 1.7**

    For any Personnel entry whose role value is not in the set of recognized roles
    defined in the MissionConfiguration, validation SHALL reject the entry and
    identify the personnel name and unrecognized role.
    """

    @given(
        invalid_role=invalid_role_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_decision_point_with_unrecognized_role_rejected(self, invalid_role: str):
        """Decision point referencing a role not in roster SHALL be rejected."""
        roster = [
            _make_personnel("Person_1", role="Pilot", duty_station="Hickam_AFB"),
            _make_personnel("Person_2", role="Loadmaster", duty_station="Luzon_DZ"),
        ]

        # Ensure invalid_role is truly not in the roster
        roster_roles = {p.role for p in roster}
        assume(invalid_role not in roster_roles)

        decision_point = DecisionPointConfig(
            decision_id="dp-test",
            assigned_role=invalid_role,
            decision_type=DecisionType.GO_NO_GO,
            deliberation_duration=5.0,
        )

        personnel_config = PersonnelConfig(
            roster=roster,
            decision_points=[decision_point],
        )

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Should have an error about the unknown role
        assert len(errors) > 0, "Expected validation errors for unrecognized role"
        role_errors = [e for e in errors if invalid_role in e]
        assert len(role_errors) > 0, (
            f"Expected error identifying unrecognized role '{invalid_role}'. "
            f"Got errors: {errors}"
        )

    @given(
        invalid_role=invalid_role_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_routing_rule_with_unrecognized_role_rejected(self, invalid_role: str):
        """Routing rule referencing a role not in the roster SHALL be rejected."""
        # Create a roster with known roles only
        roster = [
            _make_personnel("Person_1", role="Pilot", duty_station="Hickam_AFB"),
            _make_personnel("Person_2", role="Loadmaster", duty_station="Luzon_DZ"),
        ]

        # Create a routing rule referencing the invalid role
        routing_rule = MessageRoutingRule(
            message_type="OpOrder",
            destination_role=invalid_role,
        )

        # Ensure invalid_role is not in roster_roles
        roster_roles = {p.role for p in roster}
        assume(invalid_role not in roster_roles)

        personnel_config = PersonnelConfig(
            roster=roster,
            message_routing_rules=[routing_rule],
        )

        # Create C2 messages so the message_type is valid
        c2_messages = [
            C2MessageType(
                entity_id="msg-1",
                name="OpOrder",
                source_node="Hickam_AFB",
                destination_node="Luzon_DZ",
                priority=1,
                transmission_latency=1.0,
            )
        ]

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
            c2_messages=c2_messages,
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Should have an error about the unknown role
        assert len(errors) > 0, "Expected validation errors for unrecognized role"
        role_errors = [e for e in errors if invalid_role in e]
        assert len(role_errors) > 0, (
            f"Expected error identifying unrecognized role '{invalid_role}'. "
            f"Got errors: {errors}"
        )

    @given(
        valid_role=st.sampled_from(KNOWN_ROLES[:2]),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_routing_rule_with_recognized_role_accepted(self, valid_role: str):
        """Routing rule referencing a role present in the roster SHALL be accepted."""
        roster = [
            _make_personnel("Person_1", role="Pilot", duty_station="Hickam_AFB"),
            _make_personnel("Person_2", role="Loadmaster", duty_station="Luzon_DZ"),
        ]

        routing_rule = MessageRoutingRule(
            message_type="OpOrder",
            destination_role=valid_role,
        )

        personnel_config = PersonnelConfig(
            roster=roster,
            message_routing_rules=[routing_rule],
        )

        c2_messages = [
            C2MessageType(
                entity_id="msg-1",
                name="OpOrder",
                source_node="Hickam_AFB",
                destination_node="Luzon_DZ",
                priority=1,
                transmission_latency=1.0,
            )
        ]

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
            c2_messages=c2_messages,
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Should have no role-related errors
        role_errors = [e for e in errors if "unknown role" in e.lower() or "unrecognized role" in e.lower()]
        assert len(role_errors) == 0, (
            f"Valid role '{valid_role}' should not produce role errors. Got: {role_errors}"
        )


# === Property 5: Duty Station Cross-Reference Validation ===
# Feature: personnel-module, Property 5: Duty Station Cross-Reference Validation


class TestDutyStationCrossReferenceValidation:
    """Property 5: Duty Station Cross-Reference Validation.

    **Validates: Requirements 1.8**

    For any Personnel entry whose duty_station does not match a node name in the
    RouteGraph, validation SHALL reject the entry and identify the personnel name
    and unrecognized duty_station.
    """

    @given(
        invalid_station=invalid_duty_station_strategy,
        person_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=30,
        ).filter(lambda s: s.strip() != ""),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_unknown_duty_station_rejected(self, invalid_station: str, person_name: str):
        """Personnel with duty_station not in route graph SHALL be rejected."""
        assume(person_name != "ValidPerson_1")

        # Create roster with one valid and one invalid duty station
        valid_person = _make_personnel(
            "ValidPerson_1", role="Pilot", duty_station="Hickam_AFB"
        )
        invalid_person = _make_personnel(
            person_name, role="Pilot", duty_station=invalid_station
        )

        personnel_config = PersonnelConfig(
            roster=[valid_person, invalid_person],
        )

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Should reject with error identifying name and station
        assert len(errors) > 0, "Expected validation errors for unknown duty_station"

        station_errors = [e for e in errors if invalid_station in e and person_name in e]
        assert len(station_errors) > 0, (
            f"Expected error identifying personnel '{person_name}' and "
            f"unknown duty_station '{invalid_station}'. Got errors: {errors}"
        )

    @given(
        valid_station=st.sampled_from(KNOWN_NODES),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_valid_duty_station_accepted(self, valid_station: str):
        """Personnel with duty_station matching a route graph node SHALL be accepted."""
        person = _make_personnel("TestPerson", role="Pilot", duty_station=valid_station)

        personnel_config = PersonnelConfig(roster=[person])

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Should have no duty station errors
        station_errors = [e for e in errors if "duty_station" in e]
        assert len(station_errors) == 0, (
            f"Valid duty_station '{valid_station}' should not produce errors. "
            f"Got: {station_errors}"
        )

    @given(
        invalid_stations=st.lists(
            invalid_duty_station_strategy,
            min_size=2,
            max_size=5,
            unique=True,
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_invalid_duty_stations_all_reported(self, invalid_stations: list):
        """All personnel with invalid duty stations SHALL be reported."""
        roster = []
        for i, station in enumerate(invalid_stations):
            roster.append(
                _make_personnel(f"Person_{i}", role="Pilot", duty_station=station)
            )

        personnel_config = PersonnelConfig(roster=roster)

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Each invalid station should produce an error
        for i, station in enumerate(invalid_stations):
            matching = [e for e in errors if station in e]
            assert len(matching) > 0, (
                f"Expected error for duty_station '{station}' but none found. "
                f"All errors: {errors}"
            )


# === Property 20: Authorization Gate Binding Validation ===
# Feature: personnel-module, Property 20: Authorization Gate Binding Validation


class TestAuthorizationGateBindingValidation:
    """Property 20: Authorization Gate Binding Validation.

    **Validates: Requirements 5.1, 5.5**

    For any AuthorizationGateBinding that references a gate name not defined in the
    authorization_chain configuration or a role not present in the roster,
    validation SHALL reject the binding.
    """

    @given(
        invalid_gate=invalid_gate_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_binding_with_unknown_gate_rejected(self, invalid_gate: str):
        """Gate binding referencing unknown gate name SHALL be rejected."""
        roster = [
            _make_personnel("Person_1", role="Pilot", duty_station="Hickam_AFB"),
        ]

        binding = AuthorizationGateBinding(
            gate_name=invalid_gate,
            approving_role="Pilot",
        )

        personnel_config = PersonnelConfig(
            roster=roster,
            authorization_gate_bindings=[binding],
        )

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        gate_errors = [e for e in errors if invalid_gate in e]
        assert len(gate_errors) > 0, (
            f"Expected error for unknown gate '{invalid_gate}'. Got: {errors}"
        )

    @given(
        invalid_role=invalid_role_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_binding_with_unknown_role_rejected(self, invalid_role: str):
        """Gate binding referencing unknown role SHALL be rejected."""
        roster = [
            _make_personnel("Person_1", role="Pilot", duty_station="Hickam_AFB"),
        ]

        # Ensure the invalid_role is not in the roster
        roster_roles = {p.role for p in roster}
        assume(invalid_role not in roster_roles)

        binding = AuthorizationGateBinding(
            gate_name="DIPCLEAR",  # Valid gate name
            approving_role=invalid_role,
        )

        personnel_config = PersonnelConfig(
            roster=roster,
            authorization_gate_bindings=[binding],
        )

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        role_errors = [e for e in errors if invalid_role in e]
        assert len(role_errors) > 0, (
            f"Expected error for unknown role '{invalid_role}'. Got: {errors}"
        )

    @given(
        invalid_gate=invalid_gate_strategy,
        invalid_role=invalid_role_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_binding_with_both_unknown_gate_and_role_rejected(
        self, invalid_gate: str, invalid_role: str
    ):
        """Gate binding with BOTH unknown gate and role SHALL report both errors."""
        roster = [
            _make_personnel("Person_1", role="Pilot", duty_station="Hickam_AFB"),
        ]

        roster_roles = {p.role for p in roster}
        assume(invalid_role not in roster_roles)

        binding = AuthorizationGateBinding(
            gate_name=invalid_gate,
            approving_role=invalid_role,
        )

        personnel_config = PersonnelConfig(
            roster=roster,
            authorization_gate_bindings=[binding],
        )

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        gate_errors = [e for e in errors if invalid_gate in e]
        role_errors = [e for e in errors if invalid_role in e]

        assert len(gate_errors) > 0, (
            f"Expected error for unknown gate '{invalid_gate}'. Got: {errors}"
        )
        assert len(role_errors) > 0, (
            f"Expected error for unknown role '{invalid_role}'. Got: {errors}"
        )

    @given(
        valid_gate=st.sampled_from(KNOWN_GATES),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_binding_with_valid_gate_and_role_accepted(self, valid_gate: str):
        """Gate binding with valid gate and role SHALL be accepted."""
        roster = [
            _make_personnel("Person_1", role="Pilot", duty_station="Hickam_AFB"),
        ]

        binding = AuthorizationGateBinding(
            gate_name=valid_gate,
            approving_role="Pilot",  # Exists in roster
        )

        personnel_config = PersonnelConfig(
            roster=roster,
            authorization_gate_bindings=[binding],
        )

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        gate_binding_errors = [
            e for e in errors if "gate binding" in e.lower() or "Gate binding" in e
        ]
        assert len(gate_binding_errors) == 0, (
            f"Valid gate binding should not produce errors. Got: {gate_binding_errors}"
        )


# === Property 29: Configuration Validation Error Aggregation ===
# Feature: personnel-module, Property 29: Configuration Validation Error Aggregation


class TestConfigurationValidationErrorAggregation:
    """Property 29: Configuration Validation Error Aggregation.

    **Validates: Requirements 9.2**

    For any personnel configuration with multiple constraint violations,
    validation SHALL return all errors found, not just the first encountered.
    """

    @given(
        invalid_station=invalid_duty_station_strategy,
        invalid_gate=invalid_gate_strategy,
        invalid_role=invalid_role_strategy,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_violations_all_reported(
        self, invalid_station: str, invalid_gate: str, invalid_role: str
    ):
        """Config with multiple violation types SHALL report ALL errors."""
        # Ensure the invalid_role is truly not in the roster roles
        assume(invalid_role not in {"Pilot", "Loadmaster"})

        # Create roster with one valid person and one with invalid duty station
        roster = [
            _make_personnel("ValidPerson", role="Pilot", duty_station="Hickam_AFB"),
            _make_personnel("BadStation", role="Loadmaster", duty_station=invalid_station),
        ]

        # Create gate binding with invalid gate and invalid role
        binding = AuthorizationGateBinding(
            gate_name=invalid_gate,
            approving_role=invalid_role,
        )

        personnel_config = PersonnelConfig(
            roster=roster,
            authorization_gate_bindings=[binding],
        )

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Should have at least 3 distinct errors:
        # 1. Invalid duty station for BadStation
        # 2. Invalid gate name in binding
        # 3. Invalid role in binding
        assert len(errors) >= 3, (
            f"Expected at least 3 errors for multiple violations, got {len(errors)}: {errors}"
        )

        # Verify each specific violation is reported
        station_errors = [e for e in errors if invalid_station in e]
        gate_errors = [e for e in errors if invalid_gate in e]
        role_errors = [e for e in errors if invalid_role in e]

        assert len(station_errors) > 0, (
            f"Invalid duty_station '{invalid_station}' not reported. Errors: {errors}"
        )
        assert len(gate_errors) > 0, (
            f"Invalid gate '{invalid_gate}' not reported. Errors: {errors}"
        )
        assert len(role_errors) > 0, (
            f"Invalid role '{invalid_role}' not reported. Errors: {errors}"
        )

    @given(
        num_invalid_stations=st.integers(min_value=2, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_duty_station_errors_all_reported(self, num_invalid_stations: int):
        """Multiple invalid duty stations SHALL all be reported, not just the first."""
        # Create roster with multiple invalid duty stations
        roster = []
        invalid_stations = [f"BadNode_{i}" for i in range(num_invalid_stations)]

        for i, station in enumerate(invalid_stations):
            roster.append(
                _make_personnel(f"Person_{i}", role="Pilot", duty_station=station)
            )

        personnel_config = PersonnelConfig(roster=roster)

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Each invalid station should appear in at least one error
        for station in invalid_stations:
            matching = [e for e in errors if station in e]
            assert len(matching) > 0, (
                f"Invalid duty_station '{station}' not reported. All errors: {errors}"
            )

        # The total number of duty station errors should equal the number of invalid stations
        duty_station_errors = [e for e in errors if "duty_station" in e]
        assert len(duty_station_errors) >= num_invalid_stations, (
            f"Expected at least {num_invalid_stations} duty_station errors, "
            f"got {len(duty_station_errors)}: {duty_station_errors}"
        )

    @given(
        invalid_station=invalid_duty_station_strategy,
        invalid_enclave=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip() != "" and s not in ["NIPR", "SIPR"]),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_cross_category_errors_aggregated(
        self, invalid_station: str, invalid_enclave: str
    ):
        """Errors from different categories (duty station + enclave) all reported."""
        roster = [
            _make_personnel(
                "PersonBadStation",
                role="Pilot",
                duty_station=invalid_station,
                enclaves=[invalid_enclave],
            ),
        ]

        personnel_config = PersonnelConfig(roster=roster)

        mission_config = _make_mission_config(
            personnel_config=personnel_config,
            authorization_chain=_make_authorization_chain(),
            cross_domain_gateway=_make_cross_domain_gateway(),
        )

        module = PersonnelModule()
        errors = module.validate_config(mission_config)

        # Should have errors for both duty_station AND enclave
        station_errors = [e for e in errors if "duty_station" in e]
        enclave_errors = [e for e in errors if "enclave" in e.lower()]

        assert len(station_errors) > 0, (
            f"Expected duty_station error for '{invalid_station}'. Errors: {errors}"
        )
        assert len(enclave_errors) > 0, (
            f"Expected enclave error for '{invalid_enclave}'. Errors: {errors}"
        )

        # Crucially: both categories of errors are present (aggregation, not fail-fast)
        assert len(errors) >= 2, (
            f"Expected at least 2 errors (cross-category), got {len(errors)}: {errors}"
        )
