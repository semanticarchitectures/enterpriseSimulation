"""Property tests for personnel data model round-trip serialization.

# Feature: personnel-module, Property 1: Personnel Configuration Round-Trip

**Validates: Requirements 1.9, 9.4**

For any valid PersonnelConfig object (including roster, message routing rules,
authorization gate bindings, and decision points), serializing to JSON and
deserializing SHALL produce an object with field-by-field equality to the original.
"""

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

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


# === Hypothesis Strategies ===

# Strategy for valid personnel names: 1-128 chars, non-blank
personnel_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=64,
).filter(lambda s: s.strip() != "")

# Strategy for non-empty identifier strings (roles, stations, enclaves, etc.)
identifier_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=32,
)

# Strategy for positive floats (deliberation_duration)
positive_float_st = st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False)

# Strategy for optional positive floats (timeout)
optional_positive_float_st = st.one_of(
    st.none(),
    st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def credential_strategy(draw):
    """Generate a valid AuthenticationCredential."""
    return AuthenticationCredential(
        credential_type=draw(st.sampled_from(CredentialType)),
        clearance_level=draw(st.sampled_from(ClearanceLevel)),
        authorized_enclaves=draw(
            st.lists(identifier_st, min_size=1, max_size=5)
        ),
    )


@st.composite
def personnel_strategy(draw, name=None):
    """Generate a valid Personnel instance."""
    return Personnel(
        entity_id=draw(identifier_st),
        name=name if name is not None else draw(personnel_name_st),
        role=draw(identifier_st),
        duty_station=draw(identifier_st),
        credential=draw(credential_strategy()),
        readiness_state=draw(st.sampled_from(ReadinessState)),
    )


@st.composite
def message_routing_rule_strategy(draw):
    """Generate a valid MessageRoutingRule."""
    return MessageRoutingRule(
        message_type=draw(identifier_st),
        destination_role=draw(identifier_st),
    )


@st.composite
def authorization_gate_binding_strategy(draw):
    """Generate a valid AuthorizationGateBinding."""
    return AuthorizationGateBinding(
        gate_name=draw(identifier_st),
        approving_role=draw(identifier_st),
    )


@st.composite
def decision_point_config_strategy(draw):
    """Generate a valid DecisionPointConfig."""
    return DecisionPointConfig(
        decision_id=draw(identifier_st),
        assigned_role=draw(identifier_st),
        decision_type=draw(st.sampled_from(DecisionType)),
        deliberation_duration=draw(positive_float_st),
        timeout=draw(optional_positive_float_st),
    )


@st.composite
def personnel_config_strategy(draw):
    """Generate a valid PersonnelConfig with unique personnel names and decision IDs."""
    # Generate 1-5 personnel with unique names
    num_personnel = draw(st.integers(min_value=1, max_value=5))
    names = draw(
        st.lists(
            personnel_name_st,
            min_size=num_personnel,
            max_size=num_personnel,
            unique=True,
        )
    )
    roster = []
    for name in names:
        p = draw(personnel_strategy(name=name))
        roster.append(p)

    # Generate decision points with unique decision_ids
    num_dps = draw(st.integers(min_value=0, max_value=3))
    decision_ids = draw(
        st.lists(
            identifier_st,
            min_size=num_dps,
            max_size=num_dps,
            unique=True,
        )
    )
    decision_points = []
    for dp_id in decision_ids:
        dp = draw(decision_point_config_strategy())
        decision_points.append(DecisionPointConfig(
            decision_id=dp_id,
            assigned_role=dp.assigned_role,
            decision_type=dp.decision_type,
            deliberation_duration=dp.deliberation_duration,
            timeout=dp.timeout,
        ))

    return PersonnelConfig(
        roster=roster,
        message_routing_rules=draw(
            st.lists(message_routing_rule_strategy(), min_size=0, max_size=3)
        ),
        authorization_gate_bindings=draw(
            st.lists(authorization_gate_binding_strategy(), min_size=0, max_size=3)
        ),
        decision_points=decision_points,
    )


# === Property Tests ===


class TestPersonnelConfigRoundTrip:
    """Property 1: Personnel Configuration Round-Trip.

    **Validates: Requirements 1.9, 9.4**

    For any valid PersonnelConfig object (including roster, message routing rules,
    authorization gate bindings, and decision points), serializing to JSON and
    deserializing SHALL produce an object with field-by-field equality to the original.
    """

    @given(config=personnel_config_strategy())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_personnel_config_roundtrip(self, config: PersonnelConfig):
        """PersonnelConfig JSON serialization round-trip preserves all fields."""
        json_str = config.model_dump_json()
        restored = PersonnelConfig.model_validate_json(json_str)
        assert restored == config
