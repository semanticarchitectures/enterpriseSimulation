"""Property test for mission configuration serialization round-trip.

**Property 2: Mission Configuration Round-Trip**
**Validates: Requirements 2.5**

For any valid MissionConfiguration object, serializing to JSON and parsing back
SHALL produce an object with field-by-field equality to the original, including
all nested entities and collection fields.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase, CargoItem
from enterprise_sim.models.authorization import AuthorizationChainConfig, AuthorizationGate
from enterprise_sim.models.fiscal import FiscalAccount, FiscalConfig
from enterprise_sim.models.messaging import C2MessageType
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode
from enterprise_sim.models.security import (
    CrossDomainGatewayConfig,
    RoutingRule,
    SecurityEnclave,
)


# === Reusable Strategies ===

non_empty_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)

positive_float = st.floats(min_value=0.1, max_value=1e6, allow_nan=False, allow_infinity=False)

non_negative_float = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)

optional_coord = st.one_of(
    st.none(),
    st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False),
)

optional_altitude = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=60000.0, allow_nan=False, allow_infinity=False),
)

optional_node_type = st.one_of(
    st.none(),
    st.sampled_from(["waypoint", "drop_zone", "base", "checkpoint"]),
)

optional_airspace = st.one_of(
    st.none(),
    st.sampled_from(["A", "B", "C", "D", "E", "G"]),
)


# === Domain Model Strategies ===


@st.composite
def route_node_strategy(draw, name=None):
    """Generate a valid RouteNode."""
    return RouteNode(
        entity_id=draw(non_empty_str),
        name=name if name is not None else draw(non_empty_str),
        node_type=draw(optional_node_type),
        latitude=draw(optional_coord),
        longitude=draw(optional_coord),
        altitude_ft=draw(optional_altitude),
    )


@st.composite
def valid_route_graph_strategy(draw):
    """Generate a valid RouteGraphConfig with connected path and no dead-ends.

    Strategy:
    1. Generate unique node names (min 2).
    2. First = origin, last = destination.
    3. Create linear path ensuring connectivity and no dead-ends.
    4. Optionally add extra edges from non-destination nodes.
    """
    num_nodes = draw(st.integers(min_value=2, max_value=5))
    names = draw(
        st.lists(non_empty_str, min_size=num_nodes, max_size=num_nodes, unique=True)
    )

    origin = names[0]
    destination = names[-1]

    nodes = []
    for name in names:
        node = draw(route_node_strategy(name=name))
        nodes.append(node)

    # Linear path: origin -> ... -> destination
    edges = []
    for i in range(len(names) - 1):
        edge = RouteEdge(
            source=names[i],
            destination=names[i + 1],
            distance_nm=draw(positive_float),
            airspace_classification=draw(optional_airspace),
        )
        edges.append(edge)

    # Optionally add extra edges from non-destination nodes
    num_extra = draw(st.integers(min_value=0, max_value=2))
    for _ in range(num_extra):
        src_idx = draw(st.integers(min_value=0, max_value=len(names) - 2))
        dst_idx = draw(st.integers(min_value=0, max_value=len(names) - 1))
        if src_idx != dst_idx:
            edges.append(
                RouteEdge(
                    source=names[src_idx],
                    destination=names[dst_idx],
                    distance_nm=draw(positive_float),
                    airspace_classification=draw(optional_airspace),
                )
            )

    return RouteGraphConfig(
        nodes=nodes,
        edges=edges,
        origin=origin,
        destination=destination,
    )


@st.composite
def aircraft_config_strategy(draw):
    """Generate a valid AircraftConfig."""
    return AircraftConfig(
        entity_id=draw(non_empty_str),
        aircraft_type=draw(non_empty_str),
        lift_to_drag_ratio=draw(positive_float),
        specific_fuel_consumption=draw(positive_float),
        initial_fuel_weight=draw(positive_float),
        max_fuel_capacity=draw(positive_float),
    )


@st.composite
def authorization_gate_strategy(draw):
    """Generate a valid AuthorizationGate."""
    return AuthorizationGate(
        entity_id=draw(non_empty_str),
        gate_name=draw(non_empty_str),
        duration=draw(positive_float),
        timeout=draw(st.one_of(st.none(), positive_float)),
        start_time=draw(non_negative_float),
        end_time=draw(st.one_of(st.none(), non_negative_float)),
        timestamp=draw(non_negative_float),
    )


@st.composite
def authorization_chain_config_strategy(draw):
    """Generate a valid AuthorizationChainConfig."""
    num_gates = draw(st.integers(min_value=1, max_value=4))
    gates = draw(st.lists(authorization_gate_strategy(), min_size=num_gates, max_size=num_gates))
    return AuthorizationChainConfig(
        chain_id=draw(non_empty_str),
        chain_type=draw(st.sampled_from(["sequential", "parallel"])),
        gates=gates,
    )


@st.composite
def fiscal_account_strategy(draw):
    """Generate a valid FiscalAccount."""
    initial = draw(positive_float)
    balance = draw(st.floats(min_value=0.0, max_value=initial, allow_nan=False, allow_infinity=False))
    return FiscalAccount(
        entity_id=draw(non_empty_str),
        account_id=draw(non_empty_str),
        initial_allocation=initial,
        current_balance=balance,
        warning_threshold_pct=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
    )


@st.composite
def fiscal_config_strategy(draw):
    """Generate a valid FiscalConfig."""
    num_accounts = draw(st.integers(min_value=1, max_value=3))
    accounts = draw(
        st.lists(fiscal_account_strategy(), min_size=num_accounts, max_size=num_accounts)
    )
    return FiscalConfig(
        accounts=accounts,
        cost_per_flying_hour=draw(positive_float),
        cost_per_fuel_unit=draw(positive_float),
    )


@st.composite
def c2_message_type_strategy(draw):
    """Generate a valid C2MessageType."""
    return C2MessageType(
        entity_id=draw(non_empty_str),
        name=draw(non_empty_str),
        source_node=draw(non_empty_str),
        destination_node=draw(non_empty_str),
        priority=draw(st.integers(min_value=1, max_value=10)),
        transmission_latency=draw(non_negative_float),
        classification=draw(st.one_of(st.none(), non_empty_str)),
    )


@st.composite
def security_enclave_strategy(draw):
    """Generate a valid SecurityEnclave."""
    return SecurityEnclave(
        entity_id=draw(non_empty_str),
        name=draw(non_empty_str),
        classification_level=draw(
            st.sampled_from(["UNCLASSIFIED", "SECRET", "TOP_SECRET"])
        ),
    )


@st.composite
def cross_domain_gateway_config_strategy(draw):
    """Generate a valid CrossDomainGatewayConfig."""
    num_enclaves = draw(st.integers(min_value=2, max_value=4))
    enclaves = draw(
        st.lists(security_enclave_strategy(), min_size=num_enclaves, max_size=num_enclaves)
    )
    enclave_names = [e.name for e in enclaves]

    num_rules = draw(st.integers(min_value=0, max_value=3))
    rules = []
    for _ in range(num_rules):
        src = draw(st.sampled_from(enclave_names))
        dst = draw(st.sampled_from(enclave_names))
        permitted = draw(
            st.lists(
                st.sampled_from(["UNCLASSIFIED", "SECRET", "TOP_SECRET"]),
                min_size=1,
                max_size=3,
            )
        )
        rules.append(
            RoutingRule(
                source_enclave=src,
                destination_enclave=dst,
                permitted_classifications=permitted,
            )
        )

    return CrossDomainGatewayConfig(
        sanitization_latency=draw(non_negative_float),
        enclaves=enclaves,
        routing_rules=rules,
    )


@st.composite
def cargo_item_strategy(draw):
    """Generate a valid CargoItem."""
    return CargoItem(
        item_id=draw(non_empty_str),
        weight=draw(positive_float),
        description=draw(non_empty_str),
    )


@st.composite
def airdrop_config_strategy(draw):
    """Generate a valid AirdropConfig."""
    phase_durations = {
        phase: draw(positive_float) for phase in AirdropPhase
    }
    num_cargo = draw(st.integers(min_value=1, max_value=4))
    cargo_manifest = draw(
        st.lists(cargo_item_strategy(), min_size=num_cargo, max_size=num_cargo)
    )
    return AirdropConfig(
        extraction_speed=draw(positive_float),
        drop_altitude_ft=draw(positive_float),
        cargo_weight_limit=draw(positive_float),
        phase_durations=phase_durations,
        cargo_manifest=cargo_manifest,
    )


@st.composite
def mission_configuration_strategy(draw):
    """Generate a valid MissionConfiguration with required fields and optionally populated optional fields.

    Required: mission_id, mission_name, route_graph, aircraft
    Optional: authorization_chain, fiscal_parameters, c2_messages,
              cross_domain_gateway, airdrop_parameters, active_modules
    """
    route_graph = draw(valid_route_graph_strategy())
    aircraft = draw(aircraft_config_strategy())

    # Optionally populate optional fields
    authorization_chain = draw(
        st.one_of(st.none(), authorization_chain_config_strategy())
    )
    fiscal_parameters = draw(st.one_of(st.none(), fiscal_config_strategy()))
    c2_messages = draw(
        st.one_of(
            st.none(),
            st.lists(c2_message_type_strategy(), min_size=1, max_size=3),
        )
    )
    cross_domain_gateway = draw(
        st.one_of(st.none(), cross_domain_gateway_config_strategy())
    )
    airdrop_parameters = draw(st.one_of(st.none(), airdrop_config_strategy()))
    active_modules = draw(
        st.lists(non_empty_str, min_size=0, max_size=4)
    )

    return MissionConfiguration(
        mission_id=draw(non_empty_str),
        mission_name=draw(non_empty_str),
        route_graph=route_graph,
        aircraft=aircraft,
        authorization_chain=authorization_chain,
        fiscal_parameters=fiscal_parameters,
        c2_messages=c2_messages,
        cross_domain_gateway=cross_domain_gateway,
        airdrop_parameters=airdrop_parameters,
        active_modules=active_modules,
    )


# === Property Tests ===


class TestMissionConfigurationRoundTrip:
    """Property 2: Mission Configuration Round-Trip.

    **Validates: Requirements 2.5**

    For any valid MissionConfiguration object, serializing to JSON and parsing back
    SHALL produce an object with field-by-field equality to the original, including
    all nested entities and collection fields.
    """

    @given(config=mission_configuration_strategy())
    @settings(max_examples=100)
    def test_mission_config_json_roundtrip(self, config: MissionConfiguration):
        """Serializing MissionConfiguration to JSON and back preserves all fields."""
        json_str = config.model_dump_json()
        restored = MissionConfiguration.model_validate_json(json_str)

        # Top-level fields
        assert restored.mission_id == config.mission_id
        assert restored.mission_name == config.mission_name
        assert restored.active_modules == config.active_modules

        # Route graph
        assert restored.route_graph == config.route_graph

        # Aircraft
        assert restored.aircraft == config.aircraft

        # Optional fields
        assert restored.authorization_chain == config.authorization_chain
        assert restored.fiscal_parameters == config.fiscal_parameters
        assert restored.c2_messages == config.c2_messages
        assert restored.cross_domain_gateway == config.cross_domain_gateway
        assert restored.airdrop_parameters == config.airdrop_parameters

    @given(config=mission_configuration_strategy())
    @settings(max_examples=100)
    def test_mission_config_dict_roundtrip(self, config: MissionConfiguration):
        """Serializing MissionConfiguration to dict and back preserves equality."""
        data = config.model_dump()
        restored = MissionConfiguration.model_validate(data)
        assert restored == config

    @given(config=mission_configuration_strategy())
    @settings(max_examples=100)
    def test_mission_config_full_equality(self, config: MissionConfiguration):
        """JSON round-trip produces full object equality."""
        json_str = config.model_dump_json()
        restored = MissionConfiguration.model_validate_json(json_str)
        assert restored == config
