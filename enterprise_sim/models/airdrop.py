"""AirdropParameters, CargoManifest, AirdropState models."""

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from enterprise_sim.models.base import Occurrent


class AirdropPhase(str, Enum):
    """Phases of an airdrop operation in sequence."""

    EN_ROUTE = "en_route"
    SLOWDOWN = "slowdown"
    EXTRACTION = "extraction"
    ACCELERATION = "acceleration"


class CargoItem(BaseModel):
    """A single cargo item in the manifest."""

    item_id: str
    weight: float = Field(..., gt=0)
    description: str


class AirdropConfig(BaseModel):
    """Airdrop parameters from mission configuration."""

    extraction_speed: float = Field(..., gt=0)
    drop_altitude_ft: float = Field(..., gt=0)
    cargo_weight_limit: float = Field(..., gt=0)
    phase_durations: dict[AirdropPhase, float]
    cargo_manifest: list[CargoItem]

    @model_validator(mode="after")
    def validate_phase_durations_positive(self) -> "AirdropConfig":
        """Ensure all phase durations are positive (> 0)."""
        for phase, duration in self.phase_durations.items():
            if duration <= 0:
                raise ValueError(
                    f"Phase duration for '{phase.value}' must be positive, got {duration}"
                )
        return self


class AirdropState(Occurrent):
    """Current state of aircraft during airdrop operations."""

    entity_type: str = "airdrop_state"
    current_phase: AirdropPhase
    drop_zone_id: str
