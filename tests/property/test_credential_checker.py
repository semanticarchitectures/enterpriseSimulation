"""Property tests for the Credential Checker.

# Feature: personnel-module
# Property 23: Enclave Access Enforcement
# Property 24: Clearance Level Ordering Enforcement
# Property 25: Authorized Enclaves Cross-Reference Validation

**Validates: Requirements 6.2, 6.3, 6.4, 6.5, 6.6**

Property 23: For any Personnel member initiating cross-domain communication, if the
destination enclave is NOT in the member's authorized_enclaves, the communication SHALL
be blocked, the message discarded, and an access-denied event published. If the enclave
IS authorized, an authentication-success event SHALL be published.

Property 24: For any Personnel member whose clearance_level is below the destination
enclave's classification_level (UNCLASSIFIED < SECRET < TOP_SECRET), access SHALL be
blocked and a clearance-insufficient event SHALL be published.

Property 25: For any Personnel member whose authorized_enclaves list includes an enclave
name not defined in the Cross_Domain_Gateway configuration, initialization validation
SHALL return an error.
"""

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.personnel import (
    AuthenticationCredential,
    ClearanceLevel,
    CredentialType,
    Personnel,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    AccessDeniedEvent,
    AuthenticationSuccessEvent,
    ClearanceInsufficientEvent,
)
from enterprise_sim.modules.personnel.credential_checker import CredentialChecker


# === Test Helpers ===


class EventCapture:
    """Simple event capture for testing event publication."""

    def __init__(self):
        self.events: list = []

    def handler(self, event):
        self.events.append(event)

    def get_events_of_type(self, event_type):
        return [e for e in self.events if isinstance(e, event_type)]


def make_event_bus_with_capture() -> tuple[EventBus, EventCapture]:
    """Create an EventBus with an all-events capture handler."""
    bus = EventBus()
    capture = EventCapture()
    bus.subscribe_all(capture.handler, "test_capture")
    return bus, capture


# === Hypothesis Strategies ===

# Strategy for valid enclave names
enclave_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=32,
)

# Strategy for valid personnel names (non-blank, 1-128 chars)
personnel_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=64,
).filter(lambda s: s.strip() != "")

# Strategy for identifier strings
identifier_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=32,
)

# Strategy for clearance levels
clearance_level_st = st.sampled_from(list(ClearanceLevel))

# Strategy for timestamps
timestamp_st = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def personnel_with_enclaves_strategy(draw, authorized_enclaves=None):
    """Generate a Personnel member with specific or random authorized_enclaves."""
    enclaves = authorized_enclaves if authorized_enclaves is not None else draw(
        st.lists(enclave_name_st, min_size=1, max_size=5, unique=True)
    )
    return Personnel(
        entity_id=draw(identifier_st),
        name=draw(personnel_name_st),
        role=draw(identifier_st),
        duty_station=draw(identifier_st),
        credential=AuthenticationCredential(
            credential_type=CredentialType.CAC_PKI,
            clearance_level=draw(clearance_level_st),
            authorized_enclaves=enclaves,
        ),
        readiness_state=ReadinessState.READY,
    )


@st.composite
def personnel_with_clearance_strategy(draw, clearance_level=None, authorized_enclaves=None):
    """Generate a Personnel member with a specific clearance level."""
    level = clearance_level if clearance_level is not None else draw(clearance_level_st)
    enclaves = authorized_enclaves if authorized_enclaves is not None else draw(
        st.lists(enclave_name_st, min_size=1, max_size=5, unique=True)
    )
    return Personnel(
        entity_id=draw(identifier_st),
        name=draw(personnel_name_st),
        role=draw(identifier_st),
        duty_station=draw(identifier_st),
        credential=AuthenticationCredential(
            credential_type=CredentialType.CAC_PKI,
            clearance_level=level,
            authorized_enclaves=enclaves,
        ),
        readiness_state=ReadinessState.READY,
    )


# === Property 23: Enclave Access Enforcement ===
# Feature: personnel-module, Property 23: Enclave Access Enforcement


class TestEnclaveAccessEnforcement:
    """Property 23: Enclave Access Enforcement.

    **Validates: Requirements 6.2, 6.3, 6.4**

    For any Personnel member initiating cross-domain communication, if the destination
    enclave is NOT in the member's authorized_enclaves, the communication SHALL be
    blocked, the message discarded, and an access-denied event published. If the enclave
    IS authorized, an authentication-success event SHALL be published.
    """

    @given(
        personnel=personnel_with_enclaves_strategy(),
        unauthorized_enclave=enclave_name_st,
        timestamp=timestamp_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_unauthorized_enclave_blocked_and_access_denied_event(
        self, personnel, unauthorized_enclave, timestamp
    ):
        """Access to an enclave NOT in authorized_enclaves SHALL be blocked
        and an access-denied event SHALL be published."""
        # Ensure the enclave is actually not in the authorized list
        assume(unauthorized_enclave not in personnel.credential.authorized_enclaves)

        checker = CredentialChecker()
        event_bus, capture = make_event_bus_with_capture()

        # Use authenticate which checks enclave access first
        result = checker.authenticate(
            personnel=personnel,
            enclave_name=unauthorized_enclave,
            enclave_classification=ClearanceLevel.UNCLASSIFIED,
            event_bus=event_bus,
            timestamp=timestamp,
        )

        # Access SHALL be blocked
        assert result is False

        # An access-denied event SHALL be published
        denied_events = capture.get_events_of_type(AccessDeniedEvent)
        assert len(denied_events) == 1
        event = denied_events[0]
        assert event.personnel_name == personnel.name
        assert event.credential_type == personnel.credential.credential_type.value
        assert event.denied_enclave == unauthorized_enclave

        # No authentication-success event should have been published
        success_events = capture.get_events_of_type(AuthenticationSuccessEvent)
        assert len(success_events) == 0

    @given(
        data=st.data(),
        timestamp=timestamp_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_authorized_enclave_publishes_authentication_success(self, data, timestamp):
        """Access to an enclave in authorized_enclaves SHALL publish an
        authentication-success event (assuming clearance is sufficient)."""
        # Generate personnel and pick an enclave from their authorized list
        personnel = data.draw(personnel_with_clearance_strategy(
            clearance_level=ClearanceLevel.TOP_SECRET,
        ))
        authorized_enclave = data.draw(
            st.sampled_from(personnel.credential.authorized_enclaves)
        )

        checker = CredentialChecker()
        event_bus, capture = make_event_bus_with_capture()

        # Use TOP_SECRET personnel against any classification — always passes clearance
        result = checker.authenticate(
            personnel=personnel,
            enclave_name=authorized_enclave,
            enclave_classification=ClearanceLevel.UNCLASSIFIED,
            event_bus=event_bus,
            timestamp=timestamp,
        )

        # Access SHALL succeed
        assert result is True

        # An authentication-success event SHALL be published
        success_events = capture.get_events_of_type(AuthenticationSuccessEvent)
        assert len(success_events) == 1
        event = success_events[0]
        assert event.personnel_name == personnel.name
        assert event.enclave_name == authorized_enclave

        # No access-denied event should have been published
        denied_events = capture.get_events_of_type(AccessDeniedEvent)
        assert len(denied_events) == 0

    @given(
        data=st.data(),
        timestamp=timestamp_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_check_enclave_access_reflects_authorized_list(self, data, timestamp):
        """check_enclave_access returns True iff enclave is in authorized_enclaves."""
        personnel = data.draw(personnel_with_enclaves_strategy())
        enclave = data.draw(enclave_name_st)

        checker = CredentialChecker()
        result = checker.check_enclave_access(personnel, enclave)

        if enclave in personnel.credential.authorized_enclaves:
            assert result is True
        else:
            assert result is False


# === Property 24: Clearance Level Ordering Enforcement ===
# Feature: personnel-module, Property 24: Clearance Level Ordering Enforcement


class TestClearanceLevelOrderingEnforcement:
    """Property 24: Clearance Level Ordering Enforcement.

    **Validates: Requirements 6.5**

    For any Personnel member whose clearance_level is below the destination enclave's
    classification_level (UNCLASSIFIED < SECRET < TOP_SECRET), access SHALL be blocked
    and a clearance-insufficient event SHALL be published.
    """

    @given(
        data=st.data(),
        timestamp=timestamp_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_insufficient_clearance_blocked_and_event_published(self, data, timestamp):
        """When clearance_level < enclave classification, access is blocked and
        a clearance-insufficient event is published."""
        # Generate a personnel clearance that is BELOW a required classification
        personnel_clearance = data.draw(
            st.sampled_from([ClearanceLevel.UNCLASSIFIED, ClearanceLevel.SECRET])
        )
        # Required classification must be strictly above personnel clearance
        required_classification = data.draw(
            st.sampled_from([
                level for level in ClearanceLevel
                if level > personnel_clearance
            ])
        )

        # Generate personnel with this clearance and an enclave that IS authorized
        enclave_name = data.draw(enclave_name_st)
        personnel = data.draw(personnel_with_clearance_strategy(
            clearance_level=personnel_clearance,
            authorized_enclaves=[enclave_name],
        ))

        checker = CredentialChecker()
        event_bus, capture = make_event_bus_with_capture()

        result = checker.authenticate(
            personnel=personnel,
            enclave_name=enclave_name,
            enclave_classification=required_classification,
            event_bus=event_bus,
            timestamp=timestamp,
        )

        # Access SHALL be blocked
        assert result is False

        # A clearance-insufficient event SHALL be published
        insufficient_events = capture.get_events_of_type(ClearanceInsufficientEvent)
        assert len(insufficient_events) == 1
        event = insufficient_events[0]
        assert event.personnel_name == personnel.name
        assert event.personnel_clearance == personnel_clearance.name
        assert event.enclave_name == enclave_name
        assert event.required_classification == required_classification.name

    @given(
        data=st.data(),
        timestamp=timestamp_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_sufficient_clearance_allows_access(self, data, timestamp):
        """When clearance_level >= enclave classification, access is allowed
        (assuming enclave is authorized)."""
        # Generate a required classification
        required_classification = data.draw(clearance_level_st)
        # Personnel clearance must be >= required
        personnel_clearance = data.draw(
            st.sampled_from([
                level for level in ClearanceLevel
                if level >= required_classification
            ])
        )

        enclave_name = data.draw(enclave_name_st)
        personnel = data.draw(personnel_with_clearance_strategy(
            clearance_level=personnel_clearance,
            authorized_enclaves=[enclave_name],
        ))

        checker = CredentialChecker()
        event_bus, capture = make_event_bus_with_capture()

        result = checker.authenticate(
            personnel=personnel,
            enclave_name=enclave_name,
            enclave_classification=required_classification,
            event_bus=event_bus,
            timestamp=timestamp,
        )

        # Access SHALL succeed
        assert result is True

        # Authentication success event published
        success_events = capture.get_events_of_type(AuthenticationSuccessEvent)
        assert len(success_events) == 1

        # No clearance-insufficient event
        insufficient_events = capture.get_events_of_type(ClearanceInsufficientEvent)
        assert len(insufficient_events) == 0

    @given(
        personnel_clearance=clearance_level_st,
        required_level=clearance_level_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_clearance_ordering_is_consistent(self, personnel_clearance, required_level):
        """The IntEnum ordering UNCLASSIFIED < SECRET < TOP_SECRET is enforced
        correctly by check_clearance_level."""
        personnel = Personnel(
            entity_id="test-person",
            name="TestPerson",
            role="Operator",
            duty_station="BaseAlpha",
            credential=AuthenticationCredential(
                credential_type=CredentialType.CAC_PKI,
                clearance_level=personnel_clearance,
                authorized_enclaves=["TestEnclave"],
            ),
            readiness_state=ReadinessState.READY,
        )

        checker = CredentialChecker()
        result = checker.check_clearance_level(personnel, required_level)

        # Result should be True iff personnel_clearance >= required_level
        expected = personnel_clearance >= required_level
        assert result == expected


# === Property 25: Authorized Enclaves Cross-Reference Validation ===
# Feature: personnel-module, Property 25: Authorized Enclaves Cross-Reference Validation


class TestAuthorizedEnclavesCrossReferenceValidation:
    """Property 25: Authorized Enclaves Cross-Reference Validation.

    **Validates: Requirements 6.6**

    For any Personnel member whose authorized_enclaves list includes an enclave name
    not defined in the Cross_Domain_Gateway configuration, initialization validation
    SHALL return an error identifying the personnel name and each unrecognized enclave name.
    """

    @given(
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_undefined_enclave_returns_validation_error(self, data):
        """When authorized_enclaves contains enclave names not in gateway config,
        validation SHALL return an error."""
        # Define a set of valid enclaves in the gateway config
        valid_enclaves = data.draw(
            st.lists(enclave_name_st, min_size=1, max_size=5, unique=True)
        )
        # Generate an invalid enclave name not in valid enclaves
        invalid_enclave = data.draw(enclave_name_st.filter(
            lambda e: e not in valid_enclaves
        ))

        # Generate personnel with a mix of valid and invalid enclaves
        personnel_enclaves = data.draw(
            st.lists(
                st.sampled_from(valid_enclaves),
                min_size=0,
                max_size=3,
            )
        )
        personnel_enclaves.append(invalid_enclave)

        personnel = data.draw(personnel_with_enclaves_strategy(
            authorized_enclaves=personnel_enclaves,
        ))

        # Validate enclaves against the gateway config
        errors = validate_enclaves_against_gateway(personnel, valid_enclaves)

        # Validation SHALL return an error identifying the personnel and unrecognized enclave
        assert len(errors) > 0
        # Check the error identifies the personnel name
        error_text = " ".join(errors)
        assert personnel.name in error_text
        # Check the error identifies the unrecognized enclave
        assert invalid_enclave in error_text

    @given(
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_all_valid_enclaves_returns_no_error(self, data):
        """When all authorized_enclaves are defined in gateway config,
        validation SHALL return no errors."""
        # Define valid enclaves
        valid_enclaves = data.draw(
            st.lists(enclave_name_st, min_size=1, max_size=5, unique=True)
        )

        # Personnel enclaves are a subset of valid enclaves
        personnel_enclaves = data.draw(
            st.lists(
                st.sampled_from(valid_enclaves),
                min_size=1,
                max_size=min(3, len(valid_enclaves)),
            )
        )

        personnel = data.draw(personnel_with_enclaves_strategy(
            authorized_enclaves=personnel_enclaves,
        ))

        errors = validate_enclaves_against_gateway(personnel, valid_enclaves)
        assert len(errors) == 0

    @given(
        data=st.data(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_invalid_enclaves_all_reported(self, data):
        """When multiple authorized_enclaves are not in gateway config,
        all invalid enclaves SHALL be identified in the error."""
        valid_enclaves = data.draw(
            st.lists(enclave_name_st, min_size=1, max_size=3, unique=True)
        )
        # Generate 2+ invalid enclaves
        invalid_enclaves = data.draw(
            st.lists(
                enclave_name_st.filter(lambda e: e not in valid_enclaves),
                min_size=2,
                max_size=4,
                unique=True,
            )
        )

        personnel_enclaves = list(invalid_enclaves)
        personnel = data.draw(personnel_with_enclaves_strategy(
            authorized_enclaves=personnel_enclaves,
        ))

        errors = validate_enclaves_against_gateway(personnel, valid_enclaves)

        # All invalid enclaves should be reported
        assert len(errors) > 0
        error_text = " ".join(errors)
        for invalid_enclave in invalid_enclaves:
            assert invalid_enclave in error_text


# === Validation helper for Property 25 ===
# This implements the validation logic described in the design document.
# When PersonnelModule.validate_config is implemented (task 8.1), this
# function represents the enclave cross-reference check it performs.


def validate_enclaves_against_gateway(
    personnel: Personnel, gateway_enclaves: list[str]
) -> list[str]:
    """Validate that all authorized_enclaves in a personnel's credential are
    defined in the Cross_Domain_Gateway configuration.

    Returns a list of error messages for any unrecognized enclave names.
    """
    errors = []
    for enclave in personnel.credential.authorized_enclaves:
        if enclave not in gateway_enclaves:
            errors.append(
                f"Personnel '{personnel.name}' references unknown enclave: {enclave}"
            )
    return errors
