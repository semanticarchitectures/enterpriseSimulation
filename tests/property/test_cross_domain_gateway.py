"""Property tests for Cross-Domain Gateway routing rule enforcement.

**Property 17: Cross-Domain Gateway Routing Rule Enforcement**
**Validates: Requirements 7.2, 7.3**

For any message, source enclave, destination enclave, and set of routing rules,
the gateway SHALL permit transfer if and only if the message classification appears
in the permitted_classifications for that (source, destination) enclave pair. When
denied, the gateway SHALL publish a classification-violation event and discard the
message.
"""

from __future__ import annotations

import simpy
from hypothesis import given, settings
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import (
    ClassificationViolationEvent,
    SanitizationCompleteEvent,
)
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteGraphConfig, RouteNode, RouteEdge
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.models.security import (
    CrossDomainGatewayConfig,
    RoutingRule,
    SecurityEnclave,
)
from enterprise_sim.modules.cross_domain import CrossDomainGatewayModule


# === Hypothesis Strategies ===

# Enclave name strategy - simple alphanumeric names
enclave_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=15,
)

# Classification level strategy
classification_st = st.sampled_from([
    "UNCLASSIFIED",
    "CONFIDENTIAL",
    "SECRET",
    "TOP_SECRET",
    "SCI",
])

# Non-negative float for sanitization latency
sanitization_latency_st = st.floats(
    min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
)

# Message ID strategy
message_id_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)


@st.composite
def routing_rules_and_query_st(draw):
    """Generate a set of routing rules and a query (source, dest, classification).

    Returns a tuple:
    - list of enclave names (at least 2)
    - list of RoutingRule objects
    - source enclave name
    - destination enclave name
    - message classification
    - sanitization_latency
    - whether the query should be permitted (ground truth)
    """
    # Generate at least 2 unique enclave names
    enclave_names = draw(
        st.lists(enclave_name_st, min_size=2, max_size=6, unique=True)
    )

    # Generate routing rules: for some (source, dest) pairs, define permitted classifications
    rules = []
    pairs_with_rules = draw(
        st.lists(
            st.tuples(
                st.sampled_from(enclave_names),
                st.sampled_from(enclave_names),
            ),
            min_size=0,
            max_size=10,
        )
    )

    # Deduplicate pairs (keep last rule for each pair)
    seen_pairs: dict[tuple[str, str], list[str]] = {}
    for src, dst in pairs_with_rules:
        permitted = draw(
            st.lists(classification_st, min_size=0, max_size=5, unique=True)
        )
        seen_pairs[(src, dst)] = permitted

    for (src, dst), permitted in seen_pairs.items():
        rules.append(
            RoutingRule(
                source_enclave=src,
                destination_enclave=dst,
                permitted_classifications=permitted,
            )
        )

    # Pick a query: source, dest, and classification
    source = draw(st.sampled_from(enclave_names))
    dest = draw(st.sampled_from(enclave_names))
    msg_classification = draw(classification_st)

    # Determine ground truth: permitted iff (source, dest) has a rule
    # AND msg_classification is in that rule's permitted_classifications
    key = (source, dest)
    if key in seen_pairs:
        expected_permitted = msg_classification in seen_pairs[key]
    else:
        expected_permitted = False

    sanitization_latency = draw(sanitization_latency_st)

    return (
        enclave_names,
        rules,
        source,
        dest,
        msg_classification,
        sanitization_latency,
        expected_permitted,
    )


@st.composite
def permitted_routing_scenario_st(draw):
    """Generate a routing scenario guaranteed to be PERMITTED.

    Ensures the message classification is in the permitted_classifications
    for the chosen (source, dest) pair.
    """
    enclave_names = draw(
        st.lists(enclave_name_st, min_size=2, max_size=6, unique=True)
    )

    # Choose source and dest from enclaves
    source = draw(st.sampled_from(enclave_names))
    dest = draw(st.sampled_from(enclave_names))

    # Choose a classification that will be permitted
    msg_classification = draw(classification_st)

    # Build a rule for this (source, dest) that includes msg_classification
    additional_permitted = draw(
        st.lists(classification_st, min_size=0, max_size=4, unique=True)
    )
    all_permitted = list(set([msg_classification] + additional_permitted))

    # Optionally add other rules for other pairs
    other_rules = []
    other_pairs = draw(
        st.lists(
            st.tuples(
                st.sampled_from(enclave_names),
                st.sampled_from(enclave_names),
            ),
            min_size=0,
            max_size=5,
        )
    )
    for src, dst in other_pairs:
        if (src, dst) != (source, dest):
            permitted = draw(
                st.lists(classification_st, min_size=0, max_size=3, unique=True)
            )
            other_rules.append(
                RoutingRule(
                    source_enclave=src,
                    destination_enclave=dst,
                    permitted_classifications=permitted,
                )
            )

    rules = [
        RoutingRule(
            source_enclave=source,
            destination_enclave=dest,
            permitted_classifications=all_permitted,
        )
    ] + other_rules

    sanitization_latency = draw(sanitization_latency_st)

    return (enclave_names, rules, source, dest, msg_classification, sanitization_latency)


@st.composite
def denied_routing_scenario_st(draw):
    """Generate a routing scenario guaranteed to be DENIED.

    Ensures the message classification is NOT in the permitted_classifications
    for the chosen (source, dest) pair.
    """
    enclave_names = draw(
        st.lists(enclave_name_st, min_size=2, max_size=6, unique=True)
    )

    source = draw(st.sampled_from(enclave_names))
    dest = draw(st.sampled_from(enclave_names))
    msg_classification = draw(classification_st)

    # Build permitted list that does NOT include msg_classification
    all_classifications = ["UNCLASSIFIED", "CONFIDENTIAL", "SECRET", "TOP_SECRET", "SCI"]
    available = [c for c in all_classifications if c != msg_classification]
    permitted = draw(
        st.lists(st.sampled_from(available), min_size=0, max_size=3, unique=True)
    ) if available else []

    # Choose whether to have a rule for this pair or not (both mean denied)
    has_rule = draw(st.booleans())

    rules = []
    if has_rule:
        rules.append(
            RoutingRule(
                source_enclave=source,
                destination_enclave=dest,
                permitted_classifications=permitted,
            )
        )

    # Optionally add other rules for other pairs
    other_pairs = draw(
        st.lists(
            st.tuples(
                st.sampled_from(enclave_names),
                st.sampled_from(enclave_names),
            ),
            min_size=0,
            max_size=5,
        )
    )
    for src, dst in other_pairs:
        if (src, dst) != (source, dest):
            other_permitted = draw(
                st.lists(classification_st, min_size=0, max_size=3, unique=True)
            )
            rules.append(
                RoutingRule(
                    source_enclave=src,
                    destination_enclave=dst,
                    permitted_classifications=other_permitted,
                )
            )

    sanitization_latency = draw(sanitization_latency_st)

    return (enclave_names, rules, source, dest, msg_classification, sanitization_latency)


def _make_mission_config(
    enclaves: list[str],
    routing_rules: list[RoutingRule],
    sanitization_latency: float,
) -> MissionConfiguration:
    """Build a minimal MissionConfiguration with cross-domain gateway config."""
    enclave_objects = [
        SecurityEnclave(
            entity_id=f"enc_{name}",
            name=name,
            classification_level="SECRET",
        )
        for name in enclaves
    ]

    gateway_config = CrossDomainGatewayConfig(
        sanitization_latency=sanitization_latency,
        enclaves=enclave_objects,
        routing_rules=routing_rules,
    )

    return MissionConfiguration(
        mission_id="test-mission",
        mission_name="Test Mission",
        route_graph=RouteGraphConfig(
            nodes=[
                RouteNode(entity_id="n1", name="Origin", node_type="base"),
                RouteNode(entity_id="n2", name="Dest", node_type="base"),
            ],
            edges=[RouteEdge(source="Origin", destination="Dest", distance_nm=100.0)],
            origin="Origin",
            destination="Dest",
        ),
        aircraft=AircraftConfig(
            entity_id="ac1",
            aircraft_type="C-17",
            lift_to_drag_ratio=15.0,
            specific_fuel_consumption=0.6,
            initial_fuel_weight=50000.0,
            max_fuel_capacity=60000.0,
        ),
        cross_domain_gateway=gateway_config,
        active_modules=["cross_domain"],
    )


class TestCrossDomainGatewayRoutingRuleEnforcement:
    """Property 17: Cross-Domain Gateway Routing Rule Enforcement.

    Validates: Requirements 7.2, 7.3

    For any message, source enclave, destination enclave, and set of routing rules,
    the gateway SHALL permit transfer if and only if the message classification
    appears in the permitted_classifications for that (source, destination) enclave
    pair. When denied, the gateway SHALL publish a classification-violation event
    and discard the message.
    """

    @given(data=routing_rules_and_query_st())
    @settings(max_examples=200)
    def test_check_routing_permits_iff_classification_in_rules(self, data):
        """check_routing returns True iff classification is in permitted_classifications
        for that (source, dest) pair."""
        (
            enclave_names,
            rules,
            source,
            dest,
            msg_classification,
            sanitization_latency,
            expected_permitted,
        ) = data

        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(enclave_names, rules, sanitization_latency)

        module.initialize(env, event_bus, config)

        result = module.check_routing(msg_classification, source, dest)
        assert result == expected_permitted, (
            f"check_routing({msg_classification!r}, {source!r}, {dest!r}) "
            f"returned {result}, expected {expected_permitted}"
        )

    @given(data=denied_routing_scenario_st(), message_id=message_id_st)
    @settings(max_examples=200)
    def test_process_message_publishes_violation_when_denied(self, data, message_id: str):
        """When routing is denied, process_message publishes ClassificationViolationEvent
        and returns None (no time elapsed)."""
        (
            enclave_names,
            rules,
            source,
            dest,
            msg_classification,
            sanitization_latency,
        ) = data

        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(enclave_names, rules, sanitization_latency)

        module.initialize(env, event_bus, config)

        violation_events: list[ClassificationViolationEvent] = []
        sanitization_events: list[SanitizationCompleteEvent] = []
        event_bus.subscribe(
            ClassificationViolationEvent,
            lambda e: violation_events.append(e),
            "test_violation",
        )
        event_bus.subscribe(
            SanitizationCompleteEvent,
            lambda e: sanitization_events.append(e),
            "test_sanitization",
        )

        # Run process_message as a SimPy process
        result_holder: list[float | None] = []

        def run_process():
            gen = module.process_message(env, message_id, msg_classification, source, dest)
            result = yield from gen
            result_holder.append(result)

        env.process(run_process())
        env.run()

        # When denied: violation event published, no sanitization event, result is None
        assert len(violation_events) == 1
        assert violation_events[0].message_id == message_id
        assert violation_events[0].source_enclave == source
        assert violation_events[0].destination_enclave == dest
        assert violation_events[0].message_classification == msg_classification
        assert len(sanitization_events) == 0
        assert result_holder == [None]
        # No simulation time elapsed for denied messages
        assert env.now == 0

    @given(data=permitted_routing_scenario_st(), message_id=message_id_st)
    @settings(max_examples=200)
    def test_process_message_permits_and_applies_latency_when_allowed(self, data, message_id: str):
        """When routing is permitted, process_message applies sanitization latency,
        publishes SanitizationCompleteEvent, and returns the latency value."""
        (
            enclave_names,
            rules,
            source,
            dest,
            msg_classification,
            sanitization_latency,
        ) = data

        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(enclave_names, rules, sanitization_latency)

        module.initialize(env, event_bus, config)

        violation_events: list[ClassificationViolationEvent] = []
        sanitization_events: list[SanitizationCompleteEvent] = []
        event_bus.subscribe(
            ClassificationViolationEvent,
            lambda e: violation_events.append(e),
            "test_violation",
        )
        event_bus.subscribe(
            SanitizationCompleteEvent,
            lambda e: sanitization_events.append(e),
            "test_sanitization",
        )

        # Run process_message as a SimPy process
        result_holder: list[float | None] = []

        def run_process():
            gen = module.process_message(env, message_id, msg_classification, source, dest)
            result = yield from gen
            result_holder.append(result)

        env.process(run_process())
        env.run()

        # When permitted: sanitization event published, no violation, correct latency
        assert len(violation_events) == 0
        assert len(sanitization_events) == 1
        assert sanitization_events[0].sanitization_duration == sanitization_latency
        assert sanitization_events[0].source_enclave == source
        assert sanitization_events[0].destination_enclave == dest
        assert result_holder == [sanitization_latency]
        # Simulation time advanced by sanitization latency
        assert env.now == sanitization_latency

    @given(data=denied_routing_scenario_st(), message_id=message_id_st)
    @settings(max_examples=200)
    def test_denied_message_discarded_no_time_elapsed(self, data, message_id: str):
        """When denied, process_message returns None and no simulation time elapses."""
        (
            enclave_names,
            rules,
            source,
            dest,
            msg_classification,
            sanitization_latency,
        ) = data

        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(enclave_names, rules, sanitization_latency)

        module.initialize(env, event_bus, config)

        result_holder: list[float | None] = []

        def run_process():
            gen = module.process_message(env, message_id, msg_classification, source, dest)
            result = yield from gen
            result_holder.append(result)

        env.process(run_process())
        env.run()

        # No time elapsed for denied messages
        assert env.now == 0
        assert result_holder == [None]

    @given(data=permitted_routing_scenario_st(), message_id=message_id_st)
    @settings(max_examples=200)
    def test_permitted_message_returns_sanitization_latency(self, data, message_id: str):
        """When permitted, process_message returns the sanitization latency
        and simulation time advances by exactly that amount."""
        (
            enclave_names,
            rules,
            source,
            dest,
            msg_classification,
            sanitization_latency,
        ) = data

        module = CrossDomainGatewayModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_mission_config(enclave_names, rules, sanitization_latency)

        module.initialize(env, event_bus, config)

        result_holder: list[float | None] = []

        def run_process():
            gen = module.process_message(env, message_id, msg_classification, source, dest)
            result = yield from gen
            result_holder.append(result)

        env.process(run_process())
        env.run()

        assert result_holder == [sanitization_latency]
        assert env.now == sanitization_latency
