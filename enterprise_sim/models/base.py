"""Ontology-aligned base classes for the data model layer.

This module defines the class hierarchy inspired by Basic Formal Ontology (BFO)
and Common Core Ontologies (CCO), structured to support future formal ontology
alignment without requiring OWL/RDF tooling.

Hierarchy:
    Entity (root)
    ├── Continuant (persists through time)
    │   ├── InformationEntity (messages, documents, configurations)
    │   ├── Agent (performers, roles)
    │   └── SpatialRegion (geographic locations)
    └── Occurrent (unfolds in time)
        └── Process (start_time, end_time)

All classes are Pydantic BaseModel subclasses with zero SimPy imports.
"""

from pydantic import BaseModel, Field
from typing import Optional


# === BFO-aligned root hierarchy ===


class Entity(BaseModel):
    """Root of the ontology hierarchy. Corresponds to BFO:Entity."""

    entity_id: str = Field(..., description="Unique identifier")
    entity_type: str = Field(..., description="Discriminator for subclass")


class Continuant(Entity):
    """Entities that persist through time and maintain identity.

    Corresponds to BFO:Continuant. Includes objects, roles, qualities.
    """

    entity_type: str = "continuant"


class Occurrent(Entity):
    """Entities that unfold in time (processes, events).

    Corresponds to BFO:Occurrent.
    """

    entity_type: str = "occurrent"
    timestamp: float = Field(..., ge=0, description="Simulation time")


# === CCO/UAF-aligned domain specializations ===


class InformationEntity(Continuant):
    """Information content entities (messages, documents, configurations).

    Corresponds to CCO:InformationContentEntity.
    """

    entity_type: str = "information_entity"
    classification: Optional[str] = None


class Agent(Continuant):
    """Agents that bear roles and participate in processes.

    Corresponds to CCO:Agent / UAF:PerformerRole.
    """

    entity_type: str = "agent"


class SpatialRegion(Continuant):
    """Geographic or spatial locations.

    Corresponds to BFO:SpatialRegion.
    """

    entity_type: str = "spatial_region"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_ft: Optional[float] = None


class Process(Occurrent):
    """A process that unfolds over time with a start and end.

    Corresponds to BFO:Process.
    """

    entity_type: str = "process"
    start_time: float = Field(..., ge=0)
    end_time: Optional[float] = Field(None, ge=0)
