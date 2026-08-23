"""SecurityEnclave, GatewayConfig, RoutingRule models."""

from pydantic import BaseModel, Field

from enterprise_sim.models.base import Continuant


class SecurityEnclave(Continuant):
    """A security domain/enclave.

    Represents a classified network environment (e.g., SIPRNET, CENTRIXS)
    with a defined classification level.
    """

    entity_type: str = "security_enclave"
    name: str
    classification_level: str  # e.g., "UNCLASSIFIED", "SECRET", "TOP_SECRET"


class RoutingRule(BaseModel):
    """Permit/deny rule for cross-domain message transfer.

    Defines which message classifications are permitted to flow
    from one enclave to another.
    """

    source_enclave: str
    destination_enclave: str
    permitted_classifications: list[str]


class CrossDomainGatewayConfig(BaseModel):
    """Configuration for a cross-domain gateway.

    Requires at least 2 enclaves and a non-negative sanitization latency.
    """

    sanitization_latency: float = Field(..., ge=0)
    enclaves: list[SecurityEnclave] = Field(..., min_length=2)
    routing_rules: list[RoutingRule]
