"""Unit tests for aerodynamics data models.

Validates Requirement 8.4: aircraft-specific parameters are accepted and
validated as positive numeric values.
"""

import pytest
from pydantic import ValidationError

from enterprise_sim.models.aerodynamics import AircraftConfig, FuelBurnResult


class TestAircraftConfig:
    """Tests for AircraftConfig model."""

    def test_valid_aircraft_config(self):
        """A valid AircraftConfig should be created without errors."""
        config = AircraftConfig(
            entity_id="ac-001",
            aircraft_type="C-17",
            lift_to_drag_ratio=17.5,
            specific_fuel_consumption=0.6,
            initial_fuel_weight=50000.0,
            max_fuel_capacity=60000.0,
        )
        assert config.entity_type == "aircraft"
        assert config.aircraft_type == "C-17"
        assert config.lift_to_drag_ratio == 17.5
        assert config.specific_fuel_consumption == 0.6
        assert config.initial_fuel_weight == 50000.0
        assert config.max_fuel_capacity == 60000.0

    def test_entity_type_defaults_to_aircraft(self):
        """entity_type should default to 'aircraft'."""
        config = AircraftConfig(
            entity_id="ac-002",
            aircraft_type="C-130",
            lift_to_drag_ratio=15.0,
            specific_fuel_consumption=0.7,
            initial_fuel_weight=30000.0,
            max_fuel_capacity=40000.0,
        )
        assert config.entity_type == "aircraft"

    def test_inherits_from_continuant(self):
        """AircraftConfig should inherit from Continuant."""
        from enterprise_sim.models.base import Continuant

        config = AircraftConfig(
            entity_id="ac-003",
            aircraft_type="C-5",
            lift_to_drag_ratio=20.0,
            specific_fuel_consumption=0.5,
            initial_fuel_weight=100000.0,
            max_fuel_capacity=120000.0,
        )
        assert isinstance(config, Continuant)

    def test_lift_to_drag_ratio_must_be_positive(self):
        """lift_to_drag_ratio must be > 0."""
        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(
                entity_id="ac-err",
                aircraft_type="C-17",
                lift_to_drag_ratio=0,
                specific_fuel_consumption=0.6,
                initial_fuel_weight=50000.0,
                max_fuel_capacity=60000.0,
            )
        assert "lift_to_drag_ratio" in str(exc_info.value)

    def test_lift_to_drag_ratio_rejects_negative(self):
        """lift_to_drag_ratio must reject negative values."""
        with pytest.raises(ValidationError):
            AircraftConfig(
                entity_id="ac-err",
                aircraft_type="C-17",
                lift_to_drag_ratio=-5.0,
                specific_fuel_consumption=0.6,
                initial_fuel_weight=50000.0,
                max_fuel_capacity=60000.0,
            )

    def test_specific_fuel_consumption_must_be_positive(self):
        """specific_fuel_consumption must be > 0."""
        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(
                entity_id="ac-err",
                aircraft_type="C-17",
                lift_to_drag_ratio=17.5,
                specific_fuel_consumption=0,
                initial_fuel_weight=50000.0,
                max_fuel_capacity=60000.0,
            )
        assert "specific_fuel_consumption" in str(exc_info.value)

    def test_initial_fuel_weight_must_be_positive(self):
        """initial_fuel_weight must be > 0."""
        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(
                entity_id="ac-err",
                aircraft_type="C-17",
                lift_to_drag_ratio=17.5,
                specific_fuel_consumption=0.6,
                initial_fuel_weight=0,
                max_fuel_capacity=60000.0,
            )
        assert "initial_fuel_weight" in str(exc_info.value)

    def test_max_fuel_capacity_must_be_positive(self):
        """max_fuel_capacity must be > 0."""
        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(
                entity_id="ac-err",
                aircraft_type="C-17",
                lift_to_drag_ratio=17.5,
                specific_fuel_consumption=0.6,
                initial_fuel_weight=50000.0,
                max_fuel_capacity=0,
            )
        assert "max_fuel_capacity" in str(exc_info.value)

    def test_multiple_invalid_fields_reported(self):
        """All invalid fields should be reported in a single ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AircraftConfig(
                entity_id="ac-err",
                aircraft_type="C-17",
                lift_to_drag_ratio=-1.0,
                specific_fuel_consumption=-1.0,
                initial_fuel_weight=-1.0,
                max_fuel_capacity=-1.0,
            )
        errors = exc_info.value.errors()
        error_fields = {e["loc"][0] for e in errors}
        assert "lift_to_drag_ratio" in error_fields
        assert "specific_fuel_consumption" in error_fields
        assert "initial_fuel_weight" in error_fields
        assert "max_fuel_capacity" in error_fields

    def test_serialization_roundtrip(self):
        """AircraftConfig should serialize to JSON and back without loss."""
        config = AircraftConfig(
            entity_id="ac-rt",
            aircraft_type="C-17",
            lift_to_drag_ratio=17.5,
            specific_fuel_consumption=0.6,
            initial_fuel_weight=50000.0,
            max_fuel_capacity=60000.0,
        )
        json_str = config.model_dump_json()
        restored = AircraftConfig.model_validate_json(json_str)
        assert restored == config


class TestFuelBurnResult:
    """Tests for FuelBurnResult model."""

    def test_valid_fuel_burn_result(self):
        """A valid FuelBurnResult should be created without errors."""
        result = FuelBurnResult(
            entity_id="fbr-001",
            timestamp=100.0,
            leg_source="BASE_A",
            leg_destination="WP_1",
            fuel_consumed=5000.0,
            remaining_fuel=45000.0,
        )
        assert result.entity_type == "fuel_burn_result"
        assert result.leg_source == "BASE_A"
        assert result.leg_destination == "WP_1"
        assert result.fuel_consumed == 5000.0
        assert result.remaining_fuel == 45000.0

    def test_entity_type_defaults_to_fuel_burn_result(self):
        """entity_type should default to 'fuel_burn_result'."""
        result = FuelBurnResult(
            entity_id="fbr-002",
            timestamp=50.0,
            leg_source="WP_1",
            leg_destination="WP_2",
            fuel_consumed=3000.0,
            remaining_fuel=42000.0,
        )
        assert result.entity_type == "fuel_burn_result"

    def test_inherits_from_occurrent(self):
        """FuelBurnResult should inherit from Occurrent."""
        from enterprise_sim.models.base import Occurrent

        result = FuelBurnResult(
            entity_id="fbr-003",
            timestamp=200.0,
            leg_source="WP_2",
            leg_destination="DZ_1",
            fuel_consumed=7000.0,
            remaining_fuel=35000.0,
        )
        assert isinstance(result, Occurrent)

    def test_fuel_consumed_allows_zero(self):
        """fuel_consumed should allow zero (ge=0)."""
        result = FuelBurnResult(
            entity_id="fbr-zero",
            timestamp=0.0,
            leg_source="A",
            leg_destination="B",
            fuel_consumed=0.0,
            remaining_fuel=50000.0,
        )
        assert result.fuel_consumed == 0.0

    def test_remaining_fuel_allows_zero(self):
        """remaining_fuel should allow zero (ge=0)."""
        result = FuelBurnResult(
            entity_id="fbr-empty",
            timestamp=300.0,
            leg_source="WP_3",
            leg_destination="DEST",
            fuel_consumed=50000.0,
            remaining_fuel=0.0,
        )
        assert result.remaining_fuel == 0.0

    def test_fuel_consumed_rejects_negative(self):
        """fuel_consumed must be >= 0."""
        with pytest.raises(ValidationError) as exc_info:
            FuelBurnResult(
                entity_id="fbr-err",
                timestamp=100.0,
                leg_source="A",
                leg_destination="B",
                fuel_consumed=-1.0,
                remaining_fuel=50000.0,
            )
        assert "fuel_consumed" in str(exc_info.value)

    def test_remaining_fuel_rejects_negative(self):
        """remaining_fuel must be >= 0."""
        with pytest.raises(ValidationError) as exc_info:
            FuelBurnResult(
                entity_id="fbr-err",
                timestamp=100.0,
                leg_source="A",
                leg_destination="B",
                fuel_consumed=5000.0,
                remaining_fuel=-1.0,
            )
        assert "remaining_fuel" in str(exc_info.value)

    def test_timestamp_required_and_nonnegative(self):
        """timestamp (from Occurrent) must be >= 0."""
        with pytest.raises(ValidationError):
            FuelBurnResult(
                entity_id="fbr-err",
                timestamp=-1.0,
                leg_source="A",
                leg_destination="B",
                fuel_consumed=5000.0,
                remaining_fuel=45000.0,
            )

    def test_serialization_roundtrip(self):
        """FuelBurnResult should serialize to JSON and back without loss."""
        result = FuelBurnResult(
            entity_id="fbr-rt",
            timestamp=150.0,
            leg_source="BASE",
            leg_destination="DROP_ZONE",
            fuel_consumed=8000.0,
            remaining_fuel=42000.0,
        )
        json_str = result.model_dump_json()
        restored = FuelBurnResult.model_validate_json(json_str)
        assert restored == result
