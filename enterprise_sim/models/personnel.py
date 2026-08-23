"""Personnel data models for the Personnel Module.

Defines personnel entities, credentials, readiness states, and clearance levels
used to model named human actors in the simulation.
"""

from enum import Enum, IntEnum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from enterprise_sim.models.base import Agent


class ReadinessState(str, Enum):
    """Personnel availability status."""

    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    INCAPACITATED = "INCAPACITATED"


class ClearanceLevel(IntEnum):
    """Security clearance levels with natural ordering."""

    UNCLASSIFIED = 0
    SECRET = 1
    TOP_SECRET = 2


class CredentialType(str, Enum):
    """Authentication mechanism types."""

    CAC_PKI = "CAC_PKI"


class AuthenticationCredential(BaseModel):
    """Personnel authentication credential for enclave access."""

    credential_type: CredentialType
    clearance_level: ClearanceLevel
    authorized_enclaves: list[str] = Field(..., min_length=1)


class Personnel(Agent):
    """A named individual with role, duty station, credential, and readiness."""

    entity_type: str = "personnel"
    name: str = Field(..., min_length=1, max_length=128)
    role: str
    duty_station: str
    credential: AuthenticationCredential
    readiness_state: ReadinessState = ReadinessState.READY

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Personnel name must not be blank")
        return v


class DecisionType(str, Enum):
    """Types of decisions personnel can make."""

    GO_NO_GO = "GO_NO_GO"
    APPROVE_DENY = "APPROVE_DENY"
    VERIFY_REJECT = "VERIFY_REJECT"


class DecisionOutcome(str, Enum):
    """Possible outcomes of a decision point evaluation."""

    APPROVE = "APPROVE"
    DENY = "DENY"
    DEFER = "DEFER"


class DecisionPointConfig(BaseModel):
    """Configuration for a single decision point."""

    decision_id: str = Field(..., min_length=1)
    assigned_role: str
    decision_type: DecisionType
    deliberation_duration: float = Field(..., gt=0)
    timeout: Optional[float] = Field(None, gt=0)


class MessageRoutingRule(BaseModel):
    """Maps a C2 message type to a responsible Personnel Role."""

    message_type: str
    destination_role: str


class AuthorizationGateBinding(BaseModel):
    """Maps an authorization gate to the Personnel Role that approves it."""

    gate_name: str
    approving_role: str


class PersonnelConfig(BaseModel):
    """Personnel section of the MissionConfiguration."""

    roster: list[Personnel] = Field(..., min_length=1, max_length=100)
    message_routing_rules: list[MessageRoutingRule] = Field(default_factory=list)
    authorization_gate_bindings: list[AuthorizationGateBinding] = Field(
        default_factory=list
    )
    decision_points: list[DecisionPointConfig] = Field(default_factory=list)

    @field_validator("roster")
    @classmethod
    def unique_names(cls, v: list[Personnel]) -> list[Personnel]:
        names = [p.name for p in v]
        duplicates = [n for n in names if names.count(n) > 1]
        if duplicates:
            raise ValueError(f"Duplicate personnel names: {set(duplicates)}")
        return v

    @field_validator("decision_points")
    @classmethod
    def unique_decision_ids(cls, v: list[DecisionPointConfig]) -> list[DecisionPointConfig]:
        ids = [dp.decision_id for dp in v]
        duplicates = [d for d in ids if ids.count(d) > 1]
        if duplicates:
            raise ValueError(f"Duplicate decision_id: {set(duplicates)}")
        return v
