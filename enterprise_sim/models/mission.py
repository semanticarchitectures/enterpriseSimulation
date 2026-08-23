"""MissionConfiguration schema.

Top-level declarative mission specification that drives a simulation run.
References all domain-specific configuration models and defines which
modules to activate.
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.airdrop import AirdropConfig
from enterprise_sim.models.authorization import AuthorizationChainConfig
from enterprise_sim.models.fiscal import FiscalConfig
from enterprise_sim.models.messaging import C2MessageType
from enterprise_sim.models.personnel import PersonnelConfig
from enterprise_sim.models.route import RouteGraphConfig
from enterprise_sim.models.security import CrossDomainGatewayConfig


class MissionConfiguration(BaseModel):
    """Top-level declarative mission specification.

    Assembles all domain configuration fragments into a single validated
    object that the SimulationEngine uses to initialize modules and
    drive a simulation run.
    """

    mission_id: str
    mission_name: str
    route_graph: RouteGraphConfig
    aircraft: AircraftConfig
    authorization_chain: Optional[AuthorizationChainConfig] = None
    fiscal_parameters: Optional[FiscalConfig] = None
    c2_messages: Optional[list[C2MessageType]] = None
    cross_domain_gateway: Optional[CrossDomainGatewayConfig] = None
    airdrop_parameters: Optional[AirdropConfig] = None
    personnel_parameters: Optional[PersonnelConfig] = None
    active_modules: list[str] = Field(
        default_factory=list,
        description="Module type identifiers to activate for this mission",
    )

    @model_validator(mode="after")
    def check_personnel_section_presence(self) -> "MissionConfiguration":
        """Validate that personnel_parameters is present when personnel module is active."""
        if "personnel" in self.active_modules and self.personnel_parameters is None:
            raise ValueError(
                "Personnel configuration section is required when 'personnel' is listed in active_modules"
            )
        return self
