"""Unit tests for airdrop models."""

import pytest
from pydantic import ValidationError

from enterprise_sim.models.airdrop import (
    AirdropConfig,
    AirdropPhase,
    AirdropState,
    CargoItem,
)
from enterprise_sim.models.base import Occurrent


class TestAirdropPhase:
    """Tests for the AirdropPhase enum."""

    def test_enum_values(self):
        assert AirdropPhase.EN_ROUTE == "en_route"
        assert AirdropPhase.SLOWDOWN == "slowdown"
        assert AirdropPhase.EXTRACTION == "extraction"
        assert AirdropPhase.ACCELERATION == "acceleration"

    def test_is_str_enum(self):
        assert isinstance(AirdropPhase.EN_ROUTE, str)

    def test_all_phases_present(self):
        phases = set(AirdropPhase)
        assert len(phases) == 4


class TestCargoItem:
    """Tests for the CargoItem model."""

    def test_create_cargo_item(self):
        item = CargoItem(item_id="cargo-1", weight=500.0, description="Supplies")
        assert item.item_id == "cargo-1"
        assert item.weight == 500.0
        assert item.description == "Supplies"

    def test_weight_must_be_positive(self):
        with pytest.raises(ValidationError) as exc_info:
            CargoItem(item_id="cargo-1", weight=0, description="Empty")
        assert "weight" in str(exc_info.value)

    def test_negative_weight_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CargoItem(item_id="cargo-1", weight=-10.0, description="Invalid")
        assert "weight" in str(exc_info.value)


class TestAirdropConfig:
    """Tests for the AirdropConfig model."""

    def _valid_config(self, **overrides):
        defaults = {
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
                CargoItem(item_id="c1", weight=100.0, description="Item A"),
            ],
        }
        defaults.update(overrides)
        return AirdropConfig(**defaults)

    def test_create_valid_config(self):
        config = self._valid_config()
        assert config.extraction_speed == 130.0
        assert config.drop_altitude_ft == 1000.0
        assert config.cargo_weight_limit == 5000.0
        assert len(config.phase_durations) == 4
        assert len(config.cargo_manifest) == 1

    def test_extraction_speed_must_be_positive(self):
        with pytest.raises(ValidationError) as exc_info:
            self._valid_config(extraction_speed=0)
        assert "extraction_speed" in str(exc_info.value)

    def test_drop_altitude_must_be_positive(self):
        with pytest.raises(ValidationError) as exc_info:
            self._valid_config(drop_altitude_ft=-100.0)
        assert "drop_altitude_ft" in str(exc_info.value)

    def test_cargo_weight_limit_must_be_positive(self):
        with pytest.raises(ValidationError) as exc_info:
            self._valid_config(cargo_weight_limit=0)
        assert "cargo_weight_limit" in str(exc_info.value)

    def test_phase_duration_zero_rejected(self):
        """Requirement 11.7: All phase durations must be positive."""
        with pytest.raises(ValidationError) as exc_info:
            self._valid_config(
                phase_durations={
                    AirdropPhase.EN_ROUTE: 10.0,
                    AirdropPhase.SLOWDOWN: 0.0,  # Invalid
                    AirdropPhase.EXTRACTION: 3.0,
                    AirdropPhase.ACCELERATION: 4.0,
                }
            )
        assert "positive" in str(exc_info.value).lower()

    def test_phase_duration_negative_rejected(self):
        """Requirement 11.7: All phase durations must be positive."""
        with pytest.raises(ValidationError) as exc_info:
            self._valid_config(
                phase_durations={
                    AirdropPhase.EN_ROUTE: 10.0,
                    AirdropPhase.SLOWDOWN: 5.0,
                    AirdropPhase.EXTRACTION: -1.0,  # Invalid
                    AirdropPhase.ACCELERATION: 4.0,
                }
            )
        assert "positive" in str(exc_info.value).lower()

    def test_empty_cargo_manifest_allowed(self):
        config = self._valid_config(cargo_manifest=[])
        assert config.cargo_manifest == []


class TestAirdropState:
    """Tests for the AirdropState model."""

    def test_create_airdrop_state(self):
        state = AirdropState(
            entity_id="as-1",
            timestamp=5.0,
            current_phase=AirdropPhase.EXTRACTION,
            drop_zone_id="dz-alpha",
        )
        assert state.entity_type == "airdrop_state"
        assert state.current_phase == AirdropPhase.EXTRACTION
        assert state.drop_zone_id == "dz-alpha"
        assert state.timestamp == 5.0

    def test_inherits_from_occurrent(self):
        state = AirdropState(
            entity_id="as-1",
            timestamp=0.0,
            current_phase=AirdropPhase.EN_ROUTE,
            drop_zone_id="dz-1",
        )
        assert isinstance(state, Occurrent)

    def test_timestamp_required(self):
        with pytest.raises(ValidationError) as exc_info:
            AirdropState(
                entity_id="as-1",
                current_phase=AirdropPhase.EN_ROUTE,
                drop_zone_id="dz-1",
            )
        assert "timestamp" in str(exc_info.value)

    def test_negative_timestamp_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            AirdropState(
                entity_id="as-1",
                timestamp=-1.0,
                current_phase=AirdropPhase.EN_ROUTE,
                drop_zone_id="dz-1",
            )
        assert "timestamp" in str(exc_info.value)
