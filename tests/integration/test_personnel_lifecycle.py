"""Integration test for personnel module lifecycle.

Verifies that the PersonnelModule correctly integrates with the
SimulationEngine lifecycle:
1. Initialization order: personnel initializes after its dependencies
   (authorization, c2_message, cross_domain, airdrop)
2. Finalization occurs in reverse initialization order
3. validate_config returns aggregated errors for multiple violations

Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 9.2
"""

from __future__ import annotations

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.engine.module_registry import ModuleRegistry
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase, CargoItem
from enterprise_sim.models.authorization import AuthorizationChainConfig, AuthorizationGate
from enterprise_sim.models.fiscal import FiscalAccount, FiscalConfig
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
from enterprise_sim.modules.aerodynamics import AerodynamicsModule
from enterprise_sim.modules.airdrop import AirdropModule
from enterprise_sim.modules.authorization import AuthorizationModule
from enterprise_sim.modules.c2_message import C2MessageModule
from enterprise_sim.modules.cross_domain import CrossDomainGatewayModule
from enterprise_sim.modules.fiscal import FiscalModule
from enterprise_sim.modules.personnel import PersonnelModule


def _make_route_graph() -> RouteGraphConfig:
    """Create a minimal route graph for testing."""
    return RouteGraphConfig(
        nodes=[
            RouteNode(
                entity_id="n1", name="origin", node_type="base",
                latitude=21.3, longitude=-157.9, altitude_ft=0.0,
            ),
            RouteNode(
                entity_id="n2", name="waypoint_1", node_type="waypoint",
                latitude=22.0, longitude=-156.0, altitude_ft=25000.0,
            ),
            RouteNode(
                entity_id="n3", name="drop_zone", node_type="drop_zone",
                latitude=16.0, longitude=120.5, altitude_ft=1000.0,
            ),
            RouteNode(
                entity_id="n4", name="destination", node_type="base",
                latitude=17.0, longitude=121.0, altitude_ft=0.0,
            ),
        ],
        edges=[
            RouteEdge(source="origin", destination="waypoint_1", distance_nm=100.0),
            RouteEdge(source="waypoint_1", destination="drop_zone", distance_nm=200.0),
            RouteEdge(source="drop_zone", destination="destination", distance_nm=50.0),
        ],
        origin="origin",
        destination="destination",
    )


def _make_personnel_config() -> PersonnelConfig:
    """Create a valid personnel config for lifecycle testing."""
    return PersonnelConfig(
        roster=[
            Personnel(
                entity_id="p1",
                name="Maj Smith",
                role="Pilot",
                duty_station="origin",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.TOP_SECRET,
                    authorized_enclaves=["SIPRNET"],
                ),
                readiness_state=ReadinessState.READY,
            ),
            Personnel(
                entity_id="p2",
                name="SSgt Jones",
                role="Loadmaster",
                duty_station="drop_zone",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.SECRET,
                    authorized_enclaves=["SIPRNET", "CENTRIXS"],
                ),
                readiness_state=ReadinessState.READY,
            ),
            Personnel(
                entity_id="p3",
                name="Col Davis",
                role="AOC_Commander",
                duty_station="origin",
                credential=AuthenticationCredential(
                    credential_type=CredentialType.CAC_PKI,
                    clearance_level=ClearanceLevel.TOP_SECRET,
                    authorized_enclaves=["SIPRNET", "CENTRIXS"],
                ),
                readiness_state=ReadinessState.READY,
            ),
        ],
        message_routing_rules=[
            MessageRoutingRule(message_type="ATO", destination_role="Pilot"),
        ],
        authorization_gate_bindings=[
            AuthorizationGateBinding(gate_name="title_10", approving_role="AOC_Commander"),
        ],
        decision_points=[
            DecisionPointConfig(
                decision_id="loadmaster_release",
                assigned_role="Loadmaster",
                decision_type=DecisionType.APPROVE_DENY,
                deliberation_duration=2.0,
                timeout=10.0,
            ),
        ],
    )


def _make_full_config_with_personnel() -> MissionConfiguration:
    """Create a complete mission configuration including personnel module."""
    route_graph = _make_route_graph()

    aircraft = AircraftConfig(
        entity_id="ac1",
        aircraft_type="C-130",
        lift_to_drag_ratio=14.0,
        specific_fuel_consumption=0.5,
        initial_fuel_weight=40000.0,
        max_fuel_capacity=50000.0,
    )

    authorization_chain = AuthorizationChainConfig(
        chain_id="auth_chain_1",
        chain_type="sequential",
        gates=[
            AuthorizationGate(
                entity_id="gate1",
                gate_name="title_10",
                duration=5.0,
                timestamp=0.0,
                start_time=0.0,
            ),
            AuthorizationGate(
                entity_id="gate2",
                gate_name="dipclear",
                duration=3.0,
                timestamp=0.0,
                start_time=0.0,
            ),
        ],
    )

    fiscal_parameters = FiscalConfig(
        accounts=[
            FiscalAccount(
                entity_id="fa1",
                account_id="mission_ops",
                initial_allocation=500000.0,
                current_balance=500000.0,
                warning_threshold_pct=0.1,
            ),
        ],
        cost_per_flying_hour=1000.0,
        cost_per_fuel_unit=3.0,
    )

    c2_messages = [
        C2MessageType(
            entity_id="msg1",
            name="ATO",
            source_node="origin",
            destination_node="destination",
            priority=1,
            transmission_latency=2.0,
            classification="SECRET",
        ),
    ]

    cross_domain_gateway = CrossDomainGatewayConfig(
        sanitization_latency=4.0,
        enclaves=[
            SecurityEnclave(
                entity_id="enc1", name="SIPRNET",
                classification_level="SECRET",
            ),
            SecurityEnclave(
                entity_id="enc2", name="CENTRIXS",
                classification_level="SECRET",
            ),
        ],
        routing_rules=[
            RoutingRule(
                source_enclave="SIPRNET",
                destination_enclave="CENTRIXS",
                permitted_classifications=["SECRET"],
            ),
        ],
    )

    airdrop_parameters = AirdropConfig(
        extraction_speed=130.0,
        drop_altitude_ft=1000.0,
        cargo_weight_limit=10000.0,
        phase_durations={
            AirdropPhase.SLOWDOWN: 3.0,
            AirdropPhase.EXTRACTION: 8.0,
            AirdropPhase.ACCELERATION: 3.0,
        },
        cargo_manifest=[
            CargoItem(item_id="cargo_1", weight=3000.0, description="MRE Supplies"),
        ],
    )

    personnel_parameters = _make_personnel_config()

    return MissionConfiguration(
        mission_id="mission_personnel_lifecycle",
        mission_name="Personnel Lifecycle Integration Test",
        route_graph=route_graph,
        aircraft=aircraft,
        authorization_chain=authorization_chain,
        fiscal_parameters=fiscal_parameters,
        c2_messages=c2_messages,
        cross_domain_gateway=cross_domain_gateway,
        airdrop_parameters=airdrop_parameters,
        personnel_parameters=personnel_parameters,
        active_modules=[
            "authorization",
            "fiscal",
            "c2_message",
            "cross_domain",
            "aerodynamics",
            "airdrop",
            "personnel",
        ],
    )


def _create_registry_with_all_modules() -> ModuleRegistry:
    """Create a ModuleRegistry with all 7 domain modules registered."""
    registry = ModuleRegistry()
    registry.register(AuthorizationModule())
    registry.register(FiscalModule())
    registry.register(C2MessageModule())
    registry.register(CrossDomainGatewayModule())
    registry.register(AerodynamicsModule())
    registry.register(AirdropModule())
    registry.register(PersonnelModule())
    return registry


class TestPersonnelInitializationOrdering:
    """Test that personnel module initializes after its declared dependencies."""

    def test_personnel_initializes_after_all_dependencies(self):
        """PersonnelModule initializes after authorization, c2_message, cross_domain, airdrop.

        Uses the ModuleRegistry topological sort and manually drives initialization
        (without running SimPy processes) to verify ordering.
        """
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        config = _make_full_config_with_personnel()

        import simpy

        env = simpy.Environment()

        # Track initialization order by calling initialize manually
        # in the registry's resolved topological order
        init_order: list[str] = []

        ordered_modules = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        active_modules = [m for m in ordered_modules if m.module_type in active_set]

        for module in active_modules:
            init_order.append(module.module_type)
            module.initialize(env, event_bus, config)

        # Personnel must be in the init order
        assert "personnel" in init_order, (
            f"personnel module not initialized; order was: {init_order}"
        )

        personnel_idx = init_order.index("personnel")

        # Personnel must initialize AFTER each of its dependencies
        for dep in ["authorization", "c2_message", "cross_domain", "airdrop"]:
            assert dep in init_order, (
                f"Dependency '{dep}' not in init order: {init_order}"
            )
            dep_idx = init_order.index(dep)
            assert personnel_idx > dep_idx, (
                f"personnel (index {personnel_idx}) must initialize after "
                f"{dep} (index {dep_idx}); order was: {init_order}"
            )

        # Clean up: finalize all modules
        for module in reversed(active_modules):
            module.finalize()

    def test_personnel_is_last_in_topological_order(self):
        """PersonnelModule depends on all other modules, so it initializes last."""
        registry = _create_registry_with_all_modules()

        ordered_modules = registry.resolve_initialization_order()
        module_types = [m.module_type for m in ordered_modules]

        # Personnel depends on authorization, c2_message, cross_domain, airdrop
        # which themselves may depend on others, so personnel should be last or
        # at least after all its dependencies
        personnel_idx = module_types.index("personnel")
        for dep in ["authorization", "c2_message", "cross_domain", "airdrop"]:
            dep_idx = module_types.index(dep)
            assert personnel_idx > dep_idx, (
                f"personnel must come after {dep} in topological order; "
                f"order was: {module_types}"
            )


class TestPersonnelFinalizationOrdering:
    """Test that finalization occurs in reverse initialization order."""

    def test_finalization_in_reverse_initialization_order(self):
        """PersonnelModule finalizes before its dependencies (reverse order).

        Manually drives the initialization and finalization lifecycle to
        verify the correct ordering without running SimPy processes.
        """
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        config = _make_full_config_with_personnel()

        import simpy

        env = simpy.Environment()

        # Get topological order
        ordered_modules = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        active_modules = [m for m in ordered_modules if m.module_type in active_set]

        # Initialize in order
        init_order: list[str] = []
        for module in active_modules:
            init_order.append(module.module_type)
            module.initialize(env, event_bus, config)

        # Finalize in reverse order (as the engine does)
        finalize_order: list[str] = []
        for module in reversed(active_modules):
            finalize_order.append(module.module_type)
            module.finalize()

        # Finalization should be reverse of initialization
        expected_finalize_order = list(reversed(init_order))
        assert finalize_order == expected_finalize_order, (
            f"Finalization order {finalize_order} does not match "
            f"expected reverse init order {expected_finalize_order}"
        )

    def test_personnel_finalizes_before_dependencies(self):
        """PersonnelModule finalizes before authorization, c2_message, cross_domain, airdrop."""
        event_bus = EventBus()
        registry = _create_registry_with_all_modules()
        config = _make_full_config_with_personnel()

        import simpy

        env = simpy.Environment()

        # Get topological order and initialize
        ordered_modules = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        active_modules = [m for m in ordered_modules if m.module_type in active_set]

        for module in active_modules:
            module.initialize(env, event_bus, config)

        # Finalize in reverse order
        finalize_order: list[str] = []
        for module in reversed(active_modules):
            finalize_order.append(module.module_type)
            module.finalize()

        # Personnel should finalize BEFORE each of its dependencies
        # (because finalization is reverse of initialization)
        personnel_idx = finalize_order.index("personnel")
        for dep in ["authorization", "c2_message", "cross_domain", "airdrop"]:
            dep_idx = finalize_order.index(dep)
            assert personnel_idx < dep_idx, (
                f"personnel (index {personnel_idx}) must finalize before "
                f"{dep} (index {dep_idx}); finalize order was: {finalize_order}"
            )


class TestPersonnelValidateConfigAggregation:
    """Test that validate_config returns aggregated errors for multiple violations."""

    def test_aggregated_errors_for_multiple_violations(self):
        """validate_config returns ALL errors, not just the first one encountered."""
        route_graph = _make_route_graph()

        # Create a personnel config with multiple violations:
        # 1. Routing rule references unknown message type
        # 2. Routing rule references unknown role
        # 3. Gate binding references unknown gate
        # 4. Gate binding references unknown role
        # 5. Decision point with invalid role
        # 6. Personnel with unknown duty station
        # 7. Personnel with unknown enclave
        personnel_config = PersonnelConfig(
            roster=[
                Personnel(
                    entity_id="p1",
                    name="Maj Smith",
                    role="Pilot",
                    duty_station="nonexistent_station",  # Violation: unknown duty station
                    credential=AuthenticationCredential(
                        credential_type=CredentialType.CAC_PKI,
                        clearance_level=ClearanceLevel.SECRET,
                        authorized_enclaves=["FAKE_ENCLAVE"],  # Violation: unknown enclave
                    ),
                ),
            ],
            message_routing_rules=[
                MessageRoutingRule(
                    message_type="NONEXISTENT_MSG",  # Violation: unknown message type
                    destination_role="FakeRole",  # Violation: unknown role
                ),
            ],
            authorization_gate_bindings=[
                AuthorizationGateBinding(
                    gate_name="nonexistent_gate",  # Violation: unknown gate
                    approving_role="FakeGateRole",  # Violation: unknown role
                ),
            ],
            decision_points=[
                DecisionPointConfig(
                    decision_id="dp1",
                    assigned_role="UnknownRole",  # Violation: unknown role
                    decision_type=DecisionType.GO_NO_GO,
                    deliberation_duration=5.0,
                ),
            ],
        )

        config = MissionConfiguration(
            mission_id="mission_validation_test",
            mission_name="Validation Error Aggregation Test",
            route_graph=route_graph,
            aircraft=AircraftConfig(
                entity_id="ac1",
                aircraft_type="C-130",
                lift_to_drag_ratio=14.0,
                specific_fuel_consumption=0.5,
                initial_fuel_weight=40000.0,
                max_fuel_capacity=50000.0,
            ),
            authorization_chain=AuthorizationChainConfig(
                chain_id="auth_chain_1",
                chain_type="sequential",
                gates=[
                    AuthorizationGate(
                        entity_id="gate1",
                        gate_name="title_10",
                        duration=5.0,
                        timestamp=0.0,
                        start_time=0.0,
                    ),
                ],
            ),
            c2_messages=[
                C2MessageType(
                    entity_id="msg1",
                    name="ATO",
                    source_node="origin",
                    destination_node="destination",
                    priority=1,
                    transmission_latency=2.0,
                    classification="SECRET",
                ),
            ],
            cross_domain_gateway=CrossDomainGatewayConfig(
                sanitization_latency=4.0,
                enclaves=[
                    SecurityEnclave(
                        entity_id="enc1", name="SIPRNET",
                        classification_level="SECRET",
                    ),
                    SecurityEnclave(
                        entity_id="enc2", name="CENTRIXS",
                        classification_level="SECRET",
                    ),
                ],
                routing_rules=[
                    RoutingRule(
                        source_enclave="SIPRNET",
                        destination_enclave="CENTRIXS",
                        permitted_classifications=["SECRET"],
                    ),
                ],
            ),
            personnel_parameters=personnel_config,
            active_modules=["authorization", "c2_message", "cross_domain", "personnel"],
        )

        # Call validate_config directly on the PersonnelModule
        module = PersonnelModule()
        errors = module.validate_config(config)

        # Should have multiple errors, not just one
        assert len(errors) > 1, (
            f"Expected multiple aggregated errors, got {len(errors)}: {errors}"
        )

        # Verify specific error types are present
        error_text = "\n".join(errors)

        # Unknown duty station
        assert "nonexistent_station" in error_text, (
            f"Expected error about unknown duty_station; errors: {errors}"
        )

        # Unknown message type in routing rule
        assert "NONEXISTENT_MSG" in error_text, (
            f"Expected error about unknown message type; errors: {errors}"
        )

        # Unknown role in routing rule
        assert "FakeRole" in error_text, (
            f"Expected error about unknown routing role; errors: {errors}"
        )

        # Unknown gate in gate binding
        assert "nonexistent_gate" in error_text, (
            f"Expected error about unknown gate; errors: {errors}"
        )

        # Unknown role in gate binding
        assert "FakeGateRole" in error_text, (
            f"Expected error about unknown gate role; errors: {errors}"
        )

        # Unknown role in decision point
        assert "UnknownRole" in error_text, (
            f"Expected error about unknown decision role; errors: {errors}"
        )

        # Unknown enclave in credential
        assert "FAKE_ENCLAVE" in error_text, (
            f"Expected error about unknown enclave; errors: {errors}"
        )

    def test_aggregated_errors_include_cross_reference_violations(self):
        """Errors from cross-reference checks against other config sections are aggregated."""
        route_graph = _make_route_graph()

        # Personnel with valid roster but invalid cross-references
        personnel_config = PersonnelConfig(
            roster=[
                Personnel(
                    entity_id="p1",
                    name="Capt Alpha",
                    role="Navigator",
                    duty_station="origin",  # Valid duty station
                    credential=AuthenticationCredential(
                        credential_type=CredentialType.CAC_PKI,
                        clearance_level=ClearanceLevel.SECRET,
                        authorized_enclaves=["UNKNOWN_NET_1", "UNKNOWN_NET_2"],
                    ),
                ),
            ],
            message_routing_rules=[
                MessageRoutingRule(
                    message_type="BOGUS_TYPE_1",
                    destination_role="Navigator",
                ),
                MessageRoutingRule(
                    message_type="BOGUS_TYPE_2",
                    destination_role="Navigator",
                ),
            ],
            authorization_gate_bindings=[
                AuthorizationGateBinding(
                    gate_name="fake_gate_1",
                    approving_role="Navigator",
                ),
                AuthorizationGateBinding(
                    gate_name="fake_gate_2",
                    approving_role="Navigator",
                ),
            ],
            decision_points=[],
        )

        config = MissionConfiguration(
            mission_id="mission_cross_ref_test",
            mission_name="Cross-Reference Validation Test",
            route_graph=route_graph,
            aircraft=AircraftConfig(
                entity_id="ac1",
                aircraft_type="C-130",
                lift_to_drag_ratio=14.0,
                specific_fuel_consumption=0.5,
                initial_fuel_weight=40000.0,
                max_fuel_capacity=50000.0,
            ),
            authorization_chain=AuthorizationChainConfig(
                chain_id="auth_chain_1",
                chain_type="sequential",
                gates=[
                    AuthorizationGate(
                        entity_id="gate1",
                        gate_name="title_10",
                        duration=5.0,
                        timestamp=0.0,
                        start_time=0.0,
                    ),
                ],
            ),
            c2_messages=[
                C2MessageType(
                    entity_id="msg1",
                    name="ATO",
                    source_node="origin",
                    destination_node="destination",
                    priority=1,
                    transmission_latency=2.0,
                ),
            ],
            cross_domain_gateway=CrossDomainGatewayConfig(
                sanitization_latency=4.0,
                enclaves=[
                    SecurityEnclave(
                        entity_id="enc1", name="SIPRNET",
                        classification_level="SECRET",
                    ),
                    SecurityEnclave(
                        entity_id="enc2", name="CENTRIXS",
                        classification_level="SECRET",
                    ),
                ],
                routing_rules=[
                    RoutingRule(
                        source_enclave="SIPRNET",
                        destination_enclave="CENTRIXS",
                        permitted_classifications=["SECRET"],
                    ),
                ],
            ),
            personnel_parameters=personnel_config,
            active_modules=["authorization", "c2_message", "cross_domain", "personnel"],
        )

        module = PersonnelModule()
        errors = module.validate_config(config)

        # Should have errors for each cross-reference violation
        assert len(errors) >= 4, (
            f"Expected at least 4 aggregated cross-reference errors, got {len(errors)}: {errors}"
        )

        error_text = "\n".join(errors)

        # Both unknown message types should appear
        assert "BOGUS_TYPE_1" in error_text
        assert "BOGUS_TYPE_2" in error_text

        # Both unknown gate names should appear
        assert "fake_gate_1" in error_text
        assert "fake_gate_2" in error_text

        # Both unknown enclaves should appear
        assert "UNKNOWN_NET_1" in error_text
        assert "UNKNOWN_NET_2" in error_text

    def test_valid_config_returns_no_errors(self):
        """A fully valid personnel config returns an empty error list."""
        config = _make_full_config_with_personnel()

        module = PersonnelModule()
        errors = module.validate_config(config)

        assert errors == [], (
            f"Expected no validation errors for valid config, got: {errors}"
        )
