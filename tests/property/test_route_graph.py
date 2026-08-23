"""Property tests for route graph configuration.

**Property 6: Route Graph Round-Trip**
**Property 7: Route Graph Dead-End Detection**
**Validates: Requirements 9.2, 9.3, 9.5**

Property 6: For any valid RouteGraphConfig, serializing then deserializing SHALL
produce a graph with identical node sets (names, types, coordinates) and edge sets
(source, destination, distance).

Property 7: For any directed graph where a non-destination node has no outbound edges,
validation SHALL return an error identifying that specific disconnected node.
"""

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from pydantic import ValidationError

from enterprise_sim.engine.exceptions import GraphValidationError
from enterprise_sim.models.route import RouteEdge, RouteGraphConfig, RouteNode


# === Hypothesis Strategies ===

# Strategy for valid node names: non-empty alphanumeric strings
node_name_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)

# Strategy for optional floats (latitude, longitude, altitude_ft)
optional_coord = st.one_of(
    st.none(),
    st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False),
)

optional_altitude = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=60000.0, allow_nan=False, allow_infinity=False),
)

# Strategy for optional node_type
optional_node_type = st.one_of(
    st.none(),
    st.sampled_from(["waypoint", "drop_zone", "base", "checkpoint"]),
)

# Strategy for positive distances
positive_distance = st.floats(min_value=0.1, max_value=10000.0, allow_nan=False, allow_infinity=False)

# Strategy for optional airspace classification
optional_airspace = st.one_of(
    st.none(),
    st.sampled_from(["A", "B", "C", "D", "E", "G"]),
)


@st.composite
def route_node_strategy(draw, name=None):
    """Generate a valid RouteNode with an optional specified name."""
    return RouteNode(
        entity_id=draw(st.text(alphabet="abcdefghijklmnop0123456789", min_size=1, max_size=10)),
        name=name if name is not None else draw(node_name_str),
        node_type=draw(optional_node_type),
        latitude=draw(optional_coord),
        longitude=draw(optional_coord),
        altitude_ft=draw(optional_altitude),
    )


@st.composite
def valid_route_graph_strategy(draw):
    """Generate a valid RouteGraphConfig with connected path and no dead-ends.

    Strategy:
    1. Generate a list of unique node names (minimum 2).
    2. Pick the first as origin, last as destination.
    3. Create a linear path from origin to destination (ensures connectivity).
    4. Optionally add extra edges (ensuring no dead-ends for non-destination nodes).
    """
    # Generate between 2 and 6 unique node names
    num_nodes = draw(st.integers(min_value=2, max_value=6))
    names = draw(
        st.lists(
            node_name_str,
            min_size=num_nodes,
            max_size=num_nodes,
            unique=True,
        )
    )

    origin = names[0]
    destination = names[-1]

    # Generate nodes with the specified names
    nodes = []
    for name in names:
        node = draw(route_node_strategy(name=name))
        nodes.append(node)

    # Create linear path: origin -> n1 -> n2 -> ... -> destination
    edges = []
    for i in range(len(names) - 1):
        edge = RouteEdge(
            source=names[i],
            destination=names[i + 1],
            distance_nm=draw(positive_distance),
            airspace_classification=draw(optional_airspace),
        )
        edges.append(edge)

    # Optionally add extra edges (non-destination nodes must have outbound edges,
    # which they already do from the linear path, so extra edges are safe)
    num_extra_edges = draw(st.integers(min_value=0, max_value=3))
    for _ in range(num_extra_edges):
        src_idx = draw(st.integers(min_value=0, max_value=len(names) - 2))  # non-destination
        dst_idx = draw(st.integers(min_value=0, max_value=len(names) - 1))
        if src_idx != dst_idx:
            edge = RouteEdge(
                source=names[src_idx],
                destination=names[dst_idx],
                distance_nm=draw(positive_distance),
                airspace_classification=draw(optional_airspace),
            )
            edges.append(edge)

    return RouteGraphConfig(
        nodes=nodes,
        edges=edges,
        origin=origin,
        destination=destination,
    )


@st.composite
def graph_with_dead_end_strategy(draw):
    """Generate a graph with a deliberate dead-end non-destination node.

    Strategy:
    1. Create a valid linear path from origin to destination (at least 3 nodes).
    2. Add an extra node that has an inbound edge but NO outbound edges (dead-end).
    """
    # Need at least 3 nodes: origin, destination, and the dead-end node
    num_path_nodes = draw(st.integers(min_value=2, max_value=5))
    names = draw(
        st.lists(
            node_name_str,
            min_size=num_path_nodes + 1,  # +1 for the dead-end node
            max_size=num_path_nodes + 1,
            unique=True,
        )
    )

    path_names = names[:num_path_nodes]
    dead_end_name = names[num_path_nodes]

    origin = path_names[0]
    destination = path_names[-1]

    # Generate nodes
    nodes = []
    for name in names:
        node = draw(route_node_strategy(name=name))
        nodes.append(node)

    # Create linear path edges
    edges = []
    for i in range(len(path_names) - 1):
        edge = RouteEdge(
            source=path_names[i],
            destination=path_names[i + 1],
            distance_nm=draw(positive_distance),
            airspace_classification=draw(optional_airspace),
        )
        edges.append(edge)

    # Add an edge TO the dead-end node (so it exists in the graph but has no outbound)
    source_for_dead_end_idx = draw(st.integers(min_value=0, max_value=len(path_names) - 2))
    edge_to_dead_end = RouteEdge(
        source=path_names[source_for_dead_end_idx],
        destination=dead_end_name,
        distance_nm=draw(positive_distance),
        airspace_classification=draw(optional_airspace),
    )
    edges.append(edge_to_dead_end)

    return {
        "nodes": nodes,
        "edges": edges,
        "origin": origin,
        "destination": destination,
        "dead_end_name": dead_end_name,
    }


# === Property Tests ===


class TestRouteGraphRoundTrip:
    """Property 6: Route Graph Round-Trip.

    **Validates: Requirements 9.5**

    For any valid RouteGraphConfig, serializing then deserializing SHALL produce
    a graph with identical node sets and edge sets.
    """

    @given(graph=valid_route_graph_strategy())
    @settings(max_examples=100)
    def test_route_graph_json_roundtrip(self, graph: RouteGraphConfig):
        """Serializing to JSON and deserializing produces identical graph."""
        json_str = graph.model_dump_json()
        restored = RouteGraphConfig.model_validate_json(json_str)

        # Verify node sets are identical
        assert len(restored.nodes) == len(graph.nodes)
        for orig_node, restored_node in zip(graph.nodes, restored.nodes):
            assert restored_node.name == orig_node.name
            assert restored_node.node_type == orig_node.node_type
            assert restored_node.latitude == orig_node.latitude
            assert restored_node.longitude == orig_node.longitude
            assert restored_node.altitude_ft == orig_node.altitude_ft

        # Verify edge sets are identical
        assert len(restored.edges) == len(graph.edges)
        for orig_edge, restored_edge in zip(graph.edges, restored.edges):
            assert restored_edge.source == orig_edge.source
            assert restored_edge.destination == orig_edge.destination
            assert restored_edge.distance_nm == orig_edge.distance_nm
            assert restored_edge.airspace_classification == orig_edge.airspace_classification

        # Verify origin and destination preserved
        assert restored.origin == graph.origin
        assert restored.destination == graph.destination

    @given(graph=valid_route_graph_strategy())
    @settings(max_examples=100)
    def test_route_graph_dict_roundtrip(self, graph: RouteGraphConfig):
        """Serializing to dict and deserializing produces identical graph."""
        data = graph.model_dump()
        restored = RouteGraphConfig.model_validate(data)

        # Full equality check
        assert restored == graph


class TestRouteGraphDeadEndDetection:
    """Property 7: Route Graph Dead-End Detection.

    **Validates: Requirements 9.2, 9.3**

    For any directed graph where a non-destination node has no outbound edges,
    validation SHALL return an error identifying that specific disconnected node.
    """

    @given(graph_data=graph_with_dead_end_strategy())
    @settings(max_examples=100)
    def test_dead_end_node_detected(self, graph_data: dict):
        """A non-destination node with no outbound edges raises GraphValidationError."""
        with pytest.raises(GraphValidationError) as exc_info:
            RouteGraphConfig(
                nodes=graph_data["nodes"],
                edges=graph_data["edges"],
                origin=graph_data["origin"],
                destination=graph_data["destination"],
            )

        # The error must identify the dead-end node
        assert exc_info.value.node_name == graph_data["dead_end_name"]
        assert "dead-end" in exc_info.value.reason.lower() or "no outbound" in exc_info.value.reason.lower()

    @given(graph_data=graph_with_dead_end_strategy())
    @settings(max_examples=100)
    def test_dead_end_error_contains_node_name(self, graph_data: dict):
        """The error message identifies the specific disconnected node by name."""
        with pytest.raises(GraphValidationError) as exc_info:
            RouteGraphConfig(
                nodes=graph_data["nodes"],
                edges=graph_data["edges"],
                origin=graph_data["origin"],
                destination=graph_data["destination"],
            )

        # The node_name attribute must be the dead-end node
        assert exc_info.value.node_name == graph_data["dead_end_name"]
        # The string representation should contain the node name
        assert graph_data["dead_end_name"] in str(exc_info.value)
