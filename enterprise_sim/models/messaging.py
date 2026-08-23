"""C2 messaging models for Command and Control information flow simulation.

This module defines the data models for C2 message types (definitions) and
C2 message instances (runtime occurrences) used by the C2_Message_Module.
"""

from typing import Optional

from pydantic import Field

from enterprise_sim.models.base import InformationEntity, Occurrent


class C2MessageType(InformationEntity):
    """Definition of a C2 message type.

    Represents a configured message type from the Mission_Configuration,
    specifying routing and timing characteristics for a class of C2 messages.
    """

    entity_type: str = "c2_message_type"
    name: str
    source_node: str
    destination_node: str
    priority: int = Field(..., ge=1)
    transmission_latency: float = Field(..., ge=0)


class C2MessageInstance(Occurrent):
    """A specific instance of a C2 message in the simulation.

    Tracks the lifecycle of a single message from generation through
    transmission to delivery (or failure).
    """

    entity_type: str = "c2_message_instance"
    message_type: str
    generation_time: float = Field(..., ge=0)
    transmission_start_time: Optional[float] = None
    delivery_time: Optional[float] = None
    status: str = "generated"  # generated | in_transit | delivered | failed
