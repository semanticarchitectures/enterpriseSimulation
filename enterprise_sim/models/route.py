"""RouteGraph, RouteNode, RouteEdge models.

Defines a directed graph of spatial nodes representing waypoints and destinations
through which a mission traverses. Includes structural validation ensuring
graph connectivity and reachability from origin to destination.
"""

from collections import deque
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from enterprise_sim.engine.exceptions import GraphValidationError
from enterprise_sim.models.base import SpatialRegion


class RouteNode(SpatialRegion):
    """A waypoint in the mission route graph."""

    entity_type: str = "route_node"
    name: str = Field(..., min_length=1)
    node_type: Optional[str] = None  # e.g., "waypoint", "drop_zone", "base"


class RouteEdge(BaseModel):
    """A directed edge between route nodes."""

    source: str
    destination: str
    distance_nm: float = Field(..., gt=0, description="Distance in nautical miles")
    airspace_classification: Optional[str] = None


class RouteGraphConfig(BaseModel):
    """Directed graph of spatial nodes.

    Validates structural integrity: unique node names, origin/destination existence,
    path reachability from origin to destination, and no non-destination dead-end nodes.
    """

    nodes: list[RouteNode] = Field(..., min_length=2)
    edges: list[RouteEdge] = Field(..., min_length=1)
    origin: str
    destination: str

    @model_validator(mode="after")
    def validate_graph_structure(self) -> "RouteGraphConfig":
        """Validate the structural integrity of the route graph.

        Checks:
        1. All node names are unique
        2. Origin and destination nodes exist in the node list
        3. A directed path exists from origin to destination (BFS)
        4. No non-destination node has zero outbound edges (dead-end detection)
        """
        node_names = [node.name for node in self.nodes]

        # 1. Check unique node names
        seen: set[str] = set()
        for name in node_names:
            if name in seen:
                raise GraphValidationError(
                    node_name=name,
                    reason="Duplicate node name",
                )
            seen.add(name)

        node_name_set = seen

        # 2. Verify origin and destination exist
        if self.origin not in node_name_set:
            raise GraphValidationError(
                node_name=self.origin,
                reason="Origin node not found in node list",
            )
        if self.destination not in node_name_set:
            raise GraphValidationError(
                node_name=self.destination,
                reason="Destination node not found in node list",
            )

        # 3. Build adjacency list and verify path from origin to destination
        adjacency: dict[str, list[str]] = {name: [] for name in node_name_set}
        for edge in self.edges:
            if edge.source in adjacency:
                adjacency[edge.source].append(edge.destination)

        # BFS from origin to destination
        visited: set[str] = set()
        queue: deque[str] = deque([self.origin])
        visited.add(self.origin)

        while queue:
            current = queue.popleft()
            if current == self.destination:
                break
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        if self.destination not in visited:
            raise GraphValidationError(
                node_name=self.origin,
                reason=f"No path exists from origin '{self.origin}' to destination '{self.destination}'",
            )

        # 4. Dead-end detection: no non-destination node should have zero outbound edges
        for name in node_name_set:
            if name == self.destination:
                continue
            if len(adjacency[name]) == 0:
                raise GraphValidationError(
                    node_name=name,
                    reason="Non-destination node has no outbound edges (dead-end)",
                )

        return self
