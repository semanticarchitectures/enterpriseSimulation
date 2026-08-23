"""AuthorizationGate and AuthorizationChain models."""

from typing import Optional

from pydantic import BaseModel, Field

from enterprise_sim.models.base import Process


class AuthorizationGate(Process):
    """A single authorization gate with processing duration.

    Models an interagency legal authorization step (e.g., Title 10, DIPCLEAR, EXORD)
    that must be completed before dependent processes can proceed.
    """

    entity_type: str = "authorization_gate"
    gate_name: str
    duration: float = Field(..., gt=0, description="Processing time in sim units")
    timeout: Optional[float] = Field(None, gt=0)
    status: str = "pending"  # pending | active | completed | failed | timed_out


class AuthorizationChainConfig(BaseModel):
    """Configuration for an authorization chain.

    Defines a sequence or set of authorization gates to be processed either
    sequentially (each gate depends on prior gate completion) or in parallel
    (gates processed concurrently).
    """

    chain_id: str
    chain_type: str = Field(..., pattern="^(sequential|parallel)$")
    gates: list[AuthorizationGate] = Field(..., min_length=1)
