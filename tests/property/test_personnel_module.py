"""Property tests for the Personnel Module.

**Property 2: Roster Unique Name Validation**
**Property 3: Roster Size Validation**
**Validates: Requirements 1.2, 1.3, 1.5, 1.6**

Property 2: For any Personnel_Roster containing two or more Personnel entries with the
same name, validation SHALL reject the roster and return an error identifying the
duplicated name(s).

Property 3: For any Personnel_Roster with zero entries or more than 100 entries,
validation SHALL reject the roster. For any roster with 1 to 100 entries (all otherwise
valid), validation SHALL accept it.
"""

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from pydantic import ValidationError
import pytest

from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    ClearanceLevel,
    CredentialType,
    Personnel,
    PersonnelConfig,
    ReadinessState,
)


# === Hypothesis Strategies ===

# Strategy for valid personnel names (1-128 chars, not blank)
valid_name = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip() != "")

# Strategy for role strings
valid_role = st.sampled_from(["Pilot", "Loadmaster", "Navigator", "AOC_Commander", "Engineer"])

# Strategy for duty station strings
valid_duty_station = st.sampled_from(["Hickam_AFB", "Luzon_DZ", "Kadena_AB", "Clark_AB", "Manila"])

# Strategy for clearance levels
valid_clearance = st.sampled_from(list(ClearanceLevel))

# Strategy for enclave names (at least one required)
valid_enclaves = st.lists(
    st.sampled_from(["NIPR", "SIPR", "JWICS", "CENTRIXS"]),
    min_size=1,
    max_size=3,
    unique=True,
)


@st.composite
def valid_credential_strategy(draw):
    """Generate a valid AuthenticationCredential."""
    return AuthenticationCredential(
        credential_type=CredentialType.CAC_PKI,
        clearance_level=draw(valid_clearance),
        authorized_enclaves=draw(valid_enclaves),
    )


@st.composite
def valid_personnel_strategy(draw, name=None):
    """Generate a valid Personnel instance with optional fixed name."""
    return Personnel(
        entity_id=draw(st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip() != "")),
        name=name if name is not None else draw(valid_name),
        role=draw(valid_role),
        duty_station=draw(valid_duty_station),
        credential=draw(valid_credential_strategy()),
        readiness_state=ReadinessState.READY,
    )


@st.composite
def valid_personnel_with_unique_name(draw, index):
    """Generate a valid Personnel with a guaranteed unique name using index."""
    base_name = draw(valid_name)
    unique_name = f"{base_name}_{index}"
    return Personnel(
        entity_id=f"personnel-{index}-{draw(st.text(alphabet=st.characters(whitelist_categories=('L', 'N')), min_size=1, max_size=5).filter(lambda s: s.strip() != ''))}",
        name=unique_name,
        role=draw(valid_role),
        duty_station=draw(valid_duty_station),
        credential=draw(valid_credential_strategy()),
        readiness_state=ReadinessState.READY,
    )


@st.composite
def valid_roster_strategy(draw, size=None):
    """Generate a roster of valid Personnel with unique names."""
    n = size if size is not None else draw(st.integers(min_value=1, max_value=10))
    roster = []
    for i in range(n):
        p = draw(valid_personnel_with_unique_name(i))
        roster.append(p)
    return roster


# === Property 2: Roster Unique Name Validation ===
# Feature: personnel-module, Property 2: Roster Unique Name Validation


class TestRosterUniqueNameValidation:
    """Property 2: Roster Unique Name Validation.

    **Validates: Requirements 1.2, 1.3, 1.5**

    For any Personnel_Roster containing two or more Personnel entries with the same
    name, validation SHALL reject the roster and return an error identifying the
    duplicated name(s).
    """

    @given(
        roster=valid_roster_strategy(size=None),
        dup_index=st.integers(min_value=0, max_value=9),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_duplicate_names_rejected(self, roster, dup_index):
        """A roster with duplicate names SHALL be rejected with a ValidationError."""
        # Ensure we have at least 2 entries to create a duplicate
        if len(roster) < 2:
            roster = roster + [roster[0].model_copy(
                update={"entity_id": "dup-entity", "name": roster[0].name}
            )]
        else:
            # Pick a source entry and duplicate its name in another entry
            source_idx = dup_index % len(roster)
            target_idx = (source_idx + 1) % len(roster)
            duplicated_name = roster[source_idx].name
            roster[target_idx] = roster[target_idx].model_copy(
                update={"name": duplicated_name}
            )
            duplicated_name = roster[source_idx].name

        with pytest.raises(ValidationError) as exc_info:
            PersonnelConfig(roster=roster)

        # The error should identify the duplicate name
        error_text = str(exc_info.value)
        assert "Duplicate personnel names" in error_text or "duplicate" in error_text.lower()

    @given(
        base_roster=valid_roster_strategy(size=None),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_unique_names_accepted(self, base_roster):
        """A roster with all unique names SHALL be accepted."""
        # The strategy already generates unique names via index
        config = PersonnelConfig(roster=base_roster)
        assert len(config.roster) == len(base_roster)

    @given(
        roster=valid_roster_strategy(size=5),
        dup_name=valid_name,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_duplicates_identified(self, roster, dup_name):
        """When multiple entries share the same name, the error identifies them."""
        # Set multiple entries to same name
        for i in range(min(3, len(roster))):
            roster[i] = roster[i].model_copy(update={"name": dup_name})

        with pytest.raises(ValidationError) as exc_info:
            PersonnelConfig(roster=roster)

        error_text = str(exc_info.value)
        assert dup_name in error_text or "Duplicate" in error_text


# === Property 3: Roster Size Validation ===
# Feature: personnel-module, Property 3: Roster Size Validation


class TestRosterSizeValidation:
    """Property 3: Roster Size Validation.

    **Validates: Requirements 1.2, 1.6**

    For any Personnel_Roster with zero entries or more than 100 entries,
    validation SHALL reject the roster. For any roster with 1 to 100 entries
    (all otherwise valid), validation SHALL accept it.
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(data=st.data())
    def test_empty_roster_rejected(self, data):
        """A roster with zero entries SHALL be rejected."""
        with pytest.raises(ValidationError):
            PersonnelConfig(roster=[])

    @given(
        extra_count=st.integers(min_value=101, max_value=110),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_oversized_roster_rejected(self, extra_count):
        """A roster with more than 100 entries SHALL be rejected."""
        # Generate a roster with extra_count entries (101+)
        roster = []
        for i in range(extra_count):
            roster.append(
                Personnel(
                    entity_id=f"personnel-{i}",
                    name=f"Person_{i}",
                    role="Pilot",
                    duty_station="Hickam_AFB",
                    credential=AuthenticationCredential(
                        credential_type=CredentialType.CAC_PKI,
                        clearance_level=ClearanceLevel.SECRET,
                        authorized_enclaves=["SIPR"],
                    ),
                    readiness_state=ReadinessState.READY,
                )
            )

        with pytest.raises(ValidationError) as exc_info:
            PersonnelConfig(roster=roster)

        # Error should relate to list length constraint
        error_text = str(exc_info.value)
        assert "too_long" in error_text or "max_length" in error_text or "100" in error_text or "list" in error_text.lower()

    @given(
        size=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_valid_size_roster_accepted(self, size):
        """A roster with 1 to 100 valid entries SHALL be accepted."""
        roster = []
        for i in range(size):
            roster.append(
                Personnel(
                    entity_id=f"personnel-{i}",
                    name=f"Person_{i}",
                    role="Pilot",
                    duty_station="Hickam_AFB",
                    credential=AuthenticationCredential(
                        credential_type=CredentialType.CAC_PKI,
                        clearance_level=ClearanceLevel.SECRET,
                        authorized_enclaves=["SIPR"],
                    ),
                    readiness_state=ReadinessState.READY,
                )
            )

        config = PersonnelConfig(roster=roster)
        assert len(config.roster) == size
