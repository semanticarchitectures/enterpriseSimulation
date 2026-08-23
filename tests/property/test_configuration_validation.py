"""Property tests for configuration validation.

**Property 4: Configuration Validation Error Aggregation**
**Property 20: Aircraft Parameter Validation**
**Validates: Requirements 2.6, 3.2, 3.4, 8.4, 8.6**

Property 4: For any Pydantic model construction attempt with multiple invalid field
values, the raised ValidationError SHALL identify all violating fields and their
specific constraint failures, not just the first encountered.

Property 20: For any aircraft parameter set where one or more values
(lift_to_drag_ratio, specific_fuel_consumption, initial_fuel_weight, max_fuel_capacity)
are non-positive, the Aerodynamics_Module SHALL reject the configuration and identify
the invalid parameter(s).
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from pydantic import ValidationError
import pytest

from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase, CargoItem
from enterprise_sim.models.authorization import AuthorizationGate
from enterprise_sim.models.fiscal import FiscalAccount


# === Reusable Strategies ===

non_empty_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=30,
)

positive_float = st.floats(min_value=0.01, max_value=1e9, allow_nan=False, allow_infinity=False)

non_positive_float = st.floats(
    min_value=-1e9, max_value=0.0, allow_nan=False, allow_infinity=False
)

non_negative_float = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)


# === Property 4: Configuration Validation Error Aggregation ===


class TestConfigurationValidationErrorAggregation:
    """Property 4: Configuration Validation Error Aggregation.

    **Validates: Requirements 2.6, 3.2, 3.4**

    For any Pydantic model construction attempt with multiple invalid field values,
    the raised ValidationError SHALL identify all violating fields and their specific
    constraint failures, not just the first encountered.
    """

    @given(
        invalid_fields=st.sets(
            st.sampled_from([
                "lift_to_drag_ratio",
                "specific_fuel_consumption",
                "initial_fuel_weight",
                "max_fuel_capacity",
            ]),
            min_size=2,
            max_size=4,
        ),
        invalid_value=non_positive_float,
    )
    @settings(max_examples=100)
    def test_aircraft_config_reports_all_invalid_fields(
        self, invalid_fields: set, invalid_value: float
    ):
        """AircraftConfig reports all invalid fields in a single ValidationError."""
        kwargs = {
            "entity_id": "test-aircraft",
            "aircraft_type": "C-130J",
            "lift_to_drag_ratio": 15.0,
            "specific_fuel_consumption": 0.5,
            "initial_fuel_weight": 10000.0,
            "max_fuel_capacity": 12000.0,
        }
        # Set chosen fields to invalid values
        for field in invalid_fields:
            kwargs[field] = invalid_value

        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(**kwargs)

        # Extract field names from the validation error
        error_fields = set()
        for error in exc_info.value.errors():
            # Pydantic V2 loc is a tuple like ('field_name',)
            if error["loc"]:
                error_fields.add(error["loc"][0])

        # All invalid fields must be reported
        for field in invalid_fields:
            assert field in error_fields, (
                f"Field '{field}' was invalid but not reported in ValidationError. "
                f"Reported fields: {error_fields}"
            )

    @given(
        invalid_fields=st.sets(
            st.sampled_from(["initial_allocation"]),
            min_size=1,
            max_size=1,
        ),
        current_balance_invalid=st.booleans(),
        invalid_value=non_positive_float,
        negative_balance=st.floats(
            min_value=-1e9, max_value=-0.01, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=100)
    def test_fiscal_account_reports_all_invalid_fields(
        self,
        invalid_fields: set,
        current_balance_invalid: bool,
        invalid_value: float,
        negative_balance: float,
    ):
        """FiscalAccount reports all invalid fields in a single ValidationError."""
        # At least initial_allocation is invalid; optionally also current_balance
        kwargs = {
            "entity_id": "test-fiscal",
            "account_id": "ACC-001",
            "initial_allocation": 10000.0,
            "current_balance": 5000.0,
            "warning_threshold_pct": 0.1,
        }

        kwargs["initial_allocation"] = invalid_value  # gt=0 violated

        expected_invalid = {"initial_allocation"}

        if current_balance_invalid:
            kwargs["current_balance"] = negative_balance  # ge=0 violated
            expected_invalid.add("current_balance")

        # Must have at least 2 invalid fields to test aggregation
        assume(len(expected_invalid) >= 2)

        with pytest.raises(ValidationError) as exc_info:
            FiscalAccount(**kwargs)

        error_fields = set()
        for error in exc_info.value.errors():
            if error["loc"]:
                error_fields.add(error["loc"][0])

        for field in expected_invalid:
            assert field in error_fields, (
                f"Field '{field}' was invalid but not reported in ValidationError. "
                f"Reported fields: {error_fields}"
            )

    @given(
        duration_invalid=st.booleans(),
        timeout_invalid=st.booleans(),
        invalid_duration=non_positive_float,
        invalid_timeout=non_positive_float,
    )
    @settings(max_examples=100)
    def test_authorization_gate_reports_all_invalid_fields(
        self,
        duration_invalid: bool,
        timeout_invalid: bool,
        invalid_duration: float,
        invalid_timeout: float,
    ):
        """AuthorizationGate reports all invalid fields in a single ValidationError."""
        # We need at least 2 invalid fields for aggregation testing
        assume(duration_invalid and timeout_invalid)

        kwargs = {
            "entity_id": "test-gate",
            "gate_name": "DIPCLEAR",
            "duration": 5.0,
            "timeout": 10.0,
            "timestamp": 0.0,
            "start_time": 0.0,
        }

        expected_invalid = set()

        if duration_invalid:
            kwargs["duration"] = invalid_duration
            expected_invalid.add("duration")

        if timeout_invalid:
            kwargs["timeout"] = invalid_timeout
            expected_invalid.add("timeout")

        assume(len(expected_invalid) >= 2)

        with pytest.raises(ValidationError) as exc_info:
            AuthorizationGate(**kwargs)

        error_fields = set()
        for error in exc_info.value.errors():
            if error["loc"]:
                error_fields.add(error["loc"][0])

        for field in expected_invalid:
            assert field in error_fields, (
                f"Field '{field}' was invalid but not reported in ValidationError. "
                f"Reported fields: {error_fields}"
            )

    @given(
        extraction_speed_invalid=st.booleans(),
        drop_altitude_invalid=st.booleans(),
        cargo_weight_limit_invalid=st.booleans(),
        invalid_value=non_positive_float,
    )
    @settings(max_examples=100)
    def test_airdrop_config_reports_all_invalid_fields(
        self,
        extraction_speed_invalid: bool,
        drop_altitude_invalid: bool,
        cargo_weight_limit_invalid: bool,
        invalid_value: float,
    ):
        """AirdropConfig reports all invalid gt=0 fields in a single ValidationError."""
        # Need at least 2 invalid to test aggregation
        assume(
            sum([extraction_speed_invalid, drop_altitude_invalid, cargo_weight_limit_invalid]) >= 2
        )

        kwargs = {
            "extraction_speed": 130.0,
            "drop_altitude_ft": 1000.0,
            "cargo_weight_limit": 5000.0,
            "phase_durations": {
                AirdropPhase.EN_ROUTE: 10.0,
                AirdropPhase.SLOWDOWN: 5.0,
                AirdropPhase.EXTRACTION: 3.0,
                AirdropPhase.ACCELERATION: 4.0,
            },
            "cargo_manifest": [
                CargoItem(item_id="C1", weight=100.0, description="Supplies")
            ],
        }

        expected_invalid = set()

        if extraction_speed_invalid:
            kwargs["extraction_speed"] = invalid_value
            expected_invalid.add("extraction_speed")

        if drop_altitude_invalid:
            kwargs["drop_altitude_ft"] = invalid_value
            expected_invalid.add("drop_altitude_ft")

        if cargo_weight_limit_invalid:
            kwargs["cargo_weight_limit"] = invalid_value
            expected_invalid.add("cargo_weight_limit")

        with pytest.raises(ValidationError) as exc_info:
            AirdropConfig(**kwargs)

        error_fields = set()
        for error in exc_info.value.errors():
            if error["loc"]:
                error_fields.add(error["loc"][0])

        for field in expected_invalid:
            assert field in error_fields, (
                f"Field '{field}' was invalid but not reported in ValidationError. "
                f"Reported fields: {error_fields}"
            )

    @given(
        invalid_value=non_positive_float,
    )
    @settings(max_examples=100)
    def test_validation_errors_contain_constraint_info(self, invalid_value: float):
        """Each validation error contains the constraint type that was violated."""
        kwargs = {
            "entity_id": "test-aircraft",
            "aircraft_type": "C-130J",
            "lift_to_drag_ratio": invalid_value,
            "specific_fuel_consumption": invalid_value,
            "initial_fuel_weight": invalid_value,
            "max_fuel_capacity": invalid_value,
        }

        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(**kwargs)

        for error in exc_info.value.errors():
            # Each error must identify the constraint type
            assert error["type"] is not None, "Error must have a type"
            assert "greater_than" in error["type"], (
                f"Expected 'greater_than' constraint type, got '{error['type']}'"
            )
            # Each error must have a location identifying the field
            assert len(error["loc"]) > 0, "Error must identify the field location"


# === Property 20: Aircraft Parameter Validation ===


class TestAircraftParameterValidation:
    """Property 20: Aircraft Parameter Validation.

    **Validates: Requirements 8.4, 8.6**

    For any aircraft parameter set where one or more values (lift_to_drag_ratio,
    specific_fuel_consumption, initial_fuel_weight, max_fuel_capacity) are non-positive,
    the Aerodynamics_Module SHALL reject the configuration and identify the invalid
    parameter(s).
    """

    @given(
        invalid_fields=st.sets(
            st.sampled_from([
                "lift_to_drag_ratio",
                "specific_fuel_consumption",
                "initial_fuel_weight",
                "max_fuel_capacity",
            ]),
            min_size=1,
            max_size=4,
        ),
        invalid_value=non_positive_float,
    )
    @settings(max_examples=200)
    def test_non_positive_aircraft_params_rejected(
        self, invalid_fields: set, invalid_value: float
    ):
        """AircraftConfig rejects any configuration with non-positive parameters."""
        kwargs = {
            "entity_id": "test-aircraft",
            "aircraft_type": "C-130J",
            "lift_to_drag_ratio": 15.0,
            "specific_fuel_consumption": 0.5,
            "initial_fuel_weight": 10000.0,
            "max_fuel_capacity": 12000.0,
        }

        for field in invalid_fields:
            kwargs[field] = invalid_value

        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(**kwargs)

        # Verify every invalid field is identified in the error
        error_fields = set()
        for error in exc_info.value.errors():
            if error["loc"]:
                error_fields.add(error["loc"][0])

        for field in invalid_fields:
            assert field in error_fields, (
                f"Invalid parameter '{field}' (value={invalid_value}) was not identified "
                f"in the ValidationError. Reported fields: {error_fields}"
            )

    @given(
        lift_to_drag=positive_float,
        sfc=positive_float,
        fuel_weight=positive_float,
        max_capacity=positive_float,
    )
    @settings(max_examples=100)
    def test_positive_aircraft_params_accepted(
        self, lift_to_drag: float, sfc: float, fuel_weight: float, max_capacity: float
    ):
        """AircraftConfig accepts any configuration with all positive parameters."""
        config = AircraftConfig(
            entity_id="test-aircraft",
            aircraft_type="C-130J",
            lift_to_drag_ratio=lift_to_drag,
            specific_fuel_consumption=sfc,
            initial_fuel_weight=fuel_weight,
            max_fuel_capacity=max_capacity,
        )

        assert config.lift_to_drag_ratio == lift_to_drag
        assert config.specific_fuel_consumption == sfc
        assert config.initial_fuel_weight == fuel_weight
        assert config.max_fuel_capacity == max_capacity

    @given(
        invalid_value=st.just(0.0),
        field=st.sampled_from([
            "lift_to_drag_ratio",
            "specific_fuel_consumption",
            "initial_fuel_weight",
            "max_fuel_capacity",
        ]),
    )
    @settings(max_examples=20)
    def test_zero_value_rejected_as_non_positive(self, invalid_value: float, field: str):
        """Zero is non-positive and must be rejected for gt=0 fields."""
        kwargs = {
            "entity_id": "test-aircraft",
            "aircraft_type": "C-130J",
            "lift_to_drag_ratio": 15.0,
            "specific_fuel_consumption": 0.5,
            "initial_fuel_weight": 10000.0,
            "max_fuel_capacity": 12000.0,
        }

        kwargs[field] = invalid_value

        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(**kwargs)

        error_fields = set()
        for error in exc_info.value.errors():
            if error["loc"]:
                error_fields.add(error["loc"][0])

        assert field in error_fields, (
            f"Zero value for '{field}' was not rejected. Reported fields: {error_fields}"
        )

    @given(
        invalid_value=st.floats(
            min_value=-1e9, max_value=-0.001, allow_nan=False, allow_infinity=False
        ),
        field=st.sampled_from([
            "lift_to_drag_ratio",
            "specific_fuel_consumption",
            "initial_fuel_weight",
            "max_fuel_capacity",
        ]),
    )
    @settings(max_examples=50)
    def test_negative_value_rejected(self, invalid_value: float, field: str):
        """Negative values must be rejected for gt=0 fields."""
        kwargs = {
            "entity_id": "test-aircraft",
            "aircraft_type": "C-130J",
            "lift_to_drag_ratio": 15.0,
            "specific_fuel_consumption": 0.5,
            "initial_fuel_weight": 10000.0,
            "max_fuel_capacity": 12000.0,
        }

        kwargs[field] = invalid_value

        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(**kwargs)

        error_fields = set()
        for error in exc_info.value.errors():
            if error["loc"]:
                error_fields.add(error["loc"][0])

        assert field in error_fields, (
            f"Negative value for '{field}' was not rejected. Reported fields: {error_fields}"
        )

    @given(
        invalid_fields=st.sets(
            st.sampled_from([
                "lift_to_drag_ratio",
                "specific_fuel_consumption",
                "initial_fuel_weight",
                "max_fuel_capacity",
            ]),
            min_size=2,
            max_size=4,
        ),
        invalid_values=st.lists(
            non_positive_float, min_size=4, max_size=4
        ),
    )
    @settings(max_examples=100)
    def test_multiple_invalid_params_all_reported(
        self, invalid_fields: set, invalid_values: list
    ):
        """When multiple aircraft params are invalid, ALL are reported in one error."""
        all_fields = [
            "lift_to_drag_ratio",
            "specific_fuel_consumption",
            "initial_fuel_weight",
            "max_fuel_capacity",
        ]
        kwargs = {
            "entity_id": "test-aircraft",
            "aircraft_type": "C-130J",
            "lift_to_drag_ratio": 15.0,
            "specific_fuel_consumption": 0.5,
            "initial_fuel_weight": 10000.0,
            "max_fuel_capacity": 12000.0,
        }

        # Assign invalid values to the chosen fields
        for i, field in enumerate(all_fields):
            if field in invalid_fields:
                kwargs[field] = invalid_values[i]

        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(**kwargs)

        error_fields = set()
        for error in exc_info.value.errors():
            if error["loc"]:
                error_fields.add(error["loc"][0])

        # Every invalid field must be reported
        for field in invalid_fields:
            assert field in error_fields, (
                f"Field '{field}' was invalid but not reported. "
                f"Reported: {error_fields}"
            )

        # The number of reported errors for our target fields should be >= number of invalid fields
        relevant_errors = error_fields & set(all_fields)
        assert len(relevant_errors) >= len(invalid_fields), (
            f"Expected at least {len(invalid_fields)} fields reported, "
            f"got {len(relevant_errors)}: {relevant_errors}"
        )
