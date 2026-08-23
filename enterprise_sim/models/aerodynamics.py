"""AircraftConfig and FuelBurnResult models for aerodynamic computation.

AircraftConfig holds aircraft-specific parameters required for fuel-burn
calculations (e.g., Breguet range equation). FuelBurnResult captures the
outcome of a fuel-burn computation for a single flight leg.
"""

from pydantic import Field

from enterprise_sim.models.base import Continuant, Occurrent


class AircraftConfig(Continuant):
    """Aircraft parameters for aerodynamic computation.

    Validates: Requirement 8.4 — accepts aircraft-specific parameters
    (lift-to-drag ratio, specific fuel consumption, initial fuel weight,
    maximum fuel capacity) and validates that each is a positive numeric value.
    """

    entity_type: str = "aircraft"
    aircraft_type: str
    lift_to_drag_ratio: float = Field(..., gt=0)
    specific_fuel_consumption: float = Field(..., gt=0)
    initial_fuel_weight: float = Field(..., gt=0)
    max_fuel_capacity: float = Field(..., gt=0)


class FuelBurnResult(Occurrent):
    """Result of fuel-burn computation for one leg.

    Captures fuel consumed on a specific leg and the remaining fuel
    after that leg's consumption is applied.
    """

    entity_type: str = "fuel_burn_result"
    leg_source: str
    leg_destination: str
    fuel_consumed: float = Field(..., ge=0)
    remaining_fuel: float = Field(..., ge=0)
