"""Property tests for Reporter event completeness, ordering, and metrics.

**Property 27: Reporter Event Completeness and Ordering**
**Property 28: Reporter Metrics Computation**
**Validates: Requirements 12.1, 12.2, 12.3, 12.7**

Property 27: For any set of events published to the Event_Bus during a simulation
run, the Reporter's timeline SHALL contain all published events, ordered by simulation
timestamp, with publication order preserved for events sharing the same timestamp.

Property 28: For any set of simulation events, the Reporter's summary metrics SHALL
produce:
- total_duration equal to the maximum timestamp
- total_costs equal to the sum of all fiscal debit amounts
- message_count_sent/delivered/failed matching actual event counts
- mean_delivery_time computed from delivered message timestamps
- chain_completion_times from ChainCompletionEvent instances
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import (
    ChainCompletionEvent,
    ClassificationViolationEvent,
    ExtractionCompleteEvent,
    ExtractionStartEvent,
    FuelConsumedEvent,
    FuelInsufficientEvent,
    FundsExhaustedEvent,
    GateCompletionEvent,
    GateTimeoutEvent,
    LowBalanceWarningEvent,
    MessageDeliveryFailureEvent,
    SanitizationCompleteEvent,
    SimulationEvent,
    WeightExceededEvent,
)
from enterprise_sim.reporting.reporter import Reporter


# === Hypothesis Strategies ===

non_negative_float = st.floats(
    min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False
)

positive_float = st.floats(
    min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False
)

non_empty_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)

source_module_st = st.sampled_from([
    "authorization", "fiscal", "c2_message", "cross_domain", "aerodynamics", "airdrop"
])


@st.composite
def gate_completion_event_st(draw):
    """Generate a valid GateCompletionEvent."""
    return GateCompletionEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="authorization",
        gate_name=draw(non_empty_str),
        chain_id=draw(non_empty_str),
    )


@st.composite
def gate_timeout_event_st(draw):
    """Generate a valid GateTimeoutEvent."""
    return GateTimeoutEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="authorization",
        gate_name=draw(non_empty_str),
        chain_id=draw(non_empty_str),
    )


@st.composite
def chain_completion_event_st(draw):
    """Generate a valid ChainCompletionEvent."""
    return ChainCompletionEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="authorization",
        chain_id=draw(non_empty_str),
        elapsed_time=draw(positive_float),
    )


@st.composite
def funds_exhausted_event_st(draw):
    """Generate a valid FundsExhaustedEvent."""
    return FundsExhaustedEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="fiscal",
        account_id=draw(non_empty_str),
        requested_amount=draw(positive_float),
        current_balance=draw(non_negative_float),
    )


@st.composite
def low_balance_warning_event_st(draw):
    """Generate a valid LowBalanceWarningEvent."""
    return LowBalanceWarningEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="fiscal",
        account_id=draw(non_empty_str),
        current_balance=draw(non_negative_float),
        threshold_pct=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)),
    )


@st.composite
def message_delivery_failure_event_st(draw):
    """Generate a valid MessageDeliveryFailureEvent."""
    return MessageDeliveryFailureEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="c2_message",
        message_type=draw(non_empty_str),
        source=draw(non_empty_str),
        intended_destination=draw(non_empty_str),
        generation_time=draw(non_negative_float),
    )


@st.composite
def sanitization_complete_event_st(draw):
    """Generate a valid SanitizationCompleteEvent representing a delivered message."""
    return SanitizationCompleteEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="c2_message",
        sanitization_duration=draw(positive_float),
        source_enclave=draw(non_empty_str),
        destination_enclave=draw(non_empty_str),
    )


@st.composite
def fuel_consumed_event_st(draw):
    """Generate a valid FuelConsumedEvent."""
    return FuelConsumedEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="aerodynamics",
        leg_id=draw(non_empty_str),
        fuel_quantity=draw(positive_float),
    )


@st.composite
def extraction_start_event_st(draw):
    """Generate a valid ExtractionStartEvent."""
    return ExtractionStartEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="airdrop",
        drop_zone_id=draw(non_empty_str),
    )


@st.composite
def extraction_complete_event_st(draw):
    """Generate a valid ExtractionCompleteEvent."""
    return ExtractionCompleteEvent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
        source_module="airdrop",
        drop_zone_id=draw(non_empty_str),
        cargo_manifest=draw(st.lists(non_empty_str, min_size=0, max_size=5)),
    )


# Strategy for any event type (for completeness/ordering tests)
any_event_st = st.one_of(
    gate_completion_event_st(),
    gate_timeout_event_st(),
    chain_completion_event_st(),
    funds_exhausted_event_st(),
    low_balance_warning_event_st(),
    message_delivery_failure_event_st(),
    fuel_consumed_event_st(),
    extraction_start_event_st(),
    extraction_complete_event_st(),
)


class TestReporterEventCompletenessAndOrdering:
    """Property 27: Reporter Event Completeness and Ordering.

    **Validates: Requirements 12.1, 12.2, 12.7**

    For any set of events published to the Event_Bus during a simulation run,
    the Reporter's timeline SHALL contain all published events, ordered by
    simulation timestamp, with publication order preserved for events sharing
    the same timestamp.
    """

    @given(events=st.lists(any_event_st, min_size=0, max_size=20))
    @settings(max_examples=200)
    def test_timeline_contains_all_published_events(
        self, events: list[SimulationEvent]
    ):
        """The timeline contains exactly all published events (completeness)."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in events:
            bus.publish(event)

        timeline = reporter.get_timeline()
        assert len(timeline) == len(events)

    @given(events=st.lists(any_event_st, min_size=1, max_size=20))
    @settings(max_examples=200)
    def test_timeline_ordered_by_timestamp(
        self, events: list[SimulationEvent]
    ):
        """Timeline entries are ordered by simulation timestamp (non-decreasing)."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in events:
            bus.publish(event)

        timeline = reporter.get_timeline()

        for i in range(len(timeline) - 1):
            assert timeline[i].timestamp <= timeline[i + 1].timestamp, (
                f"Timeline not ordered: entry {i} timestamp {timeline[i].timestamp} > "
                f"entry {i+1} timestamp {timeline[i+1].timestamp}"
            )

    @given(
        timestamp=non_negative_float,
        count=st.integers(min_value=2, max_value=10),
    )
    @settings(max_examples=100)
    def test_same_timestamp_preserves_publication_order(
        self, timestamp: float, count: int
    ):
        """Events sharing the same timestamp preserve publication order."""
        bus = EventBus()
        reporter = Reporter(bus)

        # Create events with the same timestamp but distinguishable entity_ids
        events = []
        for i in range(count):
            event = GateCompletionEvent(
                entity_id=f"event_{i}",
                timestamp=timestamp,
                source_module="authorization",
                gate_name=f"gate_{i}",
                chain_id="chain_1",
            )
            events.append(event)
            bus.publish(event)

        timeline = reporter.get_timeline()

        assert len(timeline) == count
        # Verify stable ordering: events with same timestamp appear in publication order
        for i, entry in enumerate(timeline):
            assert entry.payload["gate_name"] == f"gate_{i}", (
                f"Expected gate_{i} at position {i}, got {entry.payload['gate_name']}"
            )

    @given(events=st.lists(any_event_st, min_size=1, max_size=15))
    @settings(max_examples=200)
    def test_timeline_entries_match_event_fields(
        self, events: list[SimulationEvent]
    ):
        """Each timeline entry correctly reflects the event's type, timestamp, and source."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in events:
            bus.publish(event)

        timeline = reporter.get_timeline()

        # Sort events the same way the reporter does for comparison
        sorted_events = sorted(events, key=lambda e: e.timestamp)

        for entry, event in zip(timeline, sorted_events):
            assert entry.event_type == event.event_type
            assert entry.timestamp == event.timestamp
            assert entry.source_module == event.source_module

    @given(
        events_batch1=st.lists(any_event_st, min_size=1, max_size=5),
        events_batch2=st.lists(any_event_st, min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_incremental_publishing_captures_all(
        self,
        events_batch1: list[SimulationEvent],
        events_batch2: list[SimulationEvent],
    ):
        """Events published in multiple batches are all captured in the timeline."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in events_batch1:
            bus.publish(event)
        for event in events_batch2:
            bus.publish(event)

        timeline = reporter.get_timeline()
        total_events = len(events_batch1) + len(events_batch2)
        assert len(timeline) == total_events

    def test_empty_events_produces_empty_timeline(self):
        """Zero events produces an empty timeline without error."""
        bus = EventBus()
        reporter = Reporter(bus)

        timeline = reporter.get_timeline()
        assert timeline == []


class TestReporterMetricsComputation:
    """Property 28: Reporter Metrics Computation.

    **Validates: Requirements 12.1, 12.2, 12.3, 12.7**

    For any set of simulation events, the Reporter's summary metrics SHALL produce:
    - total_duration equal to the maximum timestamp
    - total_costs equal to the sum of all fiscal debit amounts
    - message_count_sent/delivered/failed matching actual event counts
    - mean_delivery_time computed from delivered message timestamps
    - chain_completion_times from ChainCompletionEvent instances
    """

    @given(events=st.lists(any_event_st, min_size=1, max_size=20))
    @settings(max_examples=200)
    def test_total_duration_equals_max_timestamp(
        self, events: list[SimulationEvent]
    ):
        """total_duration equals the maximum timestamp across all events."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in events:
            bus.publish(event)

        metrics = reporter.get_metrics()
        expected_max = max(e.timestamp for e in events)
        assert metrics.total_duration == expected_max

    @given(
        failure_events=st.lists(
            message_delivery_failure_event_st(), min_size=0, max_size=5
        ),
        other_c2_events=st.lists(
            sanitization_complete_event_st(), min_size=0, max_size=5
        ),
    )
    @settings(max_examples=200)
    def test_message_counts_match_event_counts(
        self,
        failure_events: list[MessageDeliveryFailureEvent],
        other_c2_events: list[SanitizationCompleteEvent],
    ):
        """messages_sent/delivered/failed match actual event counts from c2_message source."""
        bus = EventBus()
        reporter = Reporter(bus)

        # Publish failure events (from c2_message source)
        for event in failure_events:
            bus.publish(event)

        # Publish other c2_message events (represent delivered messages)
        for event in other_c2_events:
            bus.publish(event)

        metrics = reporter.get_metrics()

        total_c2_events = len(failure_events) + len(other_c2_events)
        assert metrics.messages_failed == len(failure_events)
        assert metrics.messages_sent == total_c2_events
        assert metrics.messages_delivered == len(other_c2_events)

    @given(
        chain_events=st.lists(chain_completion_event_st(), min_size=1, max_size=5)
    )
    @settings(max_examples=200)
    def test_chain_completion_times_from_events(
        self, chain_events: list[ChainCompletionEvent]
    ):
        """chain_completion_times contains entries from all ChainCompletionEvent instances."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in chain_events:
            bus.publish(event)

        metrics = reporter.get_metrics()

        # Each ChainCompletionEvent should produce an entry in chain_completion_times
        # If duplicate chain_ids exist, the last one wins
        expected = {}
        for event in chain_events:
            expected[event.chain_id] = event.elapsed_time

        assert metrics.chain_completion_times == expected

    def test_zero_events_produces_zero_metrics(self):
        """Zero events produces zero-value metrics without error."""
        bus = EventBus()
        reporter = Reporter(bus)

        metrics = reporter.get_metrics()
        assert metrics.total_duration == 0.0
        assert metrics.total_costs == 0.0
        assert metrics.messages_sent == 0
        assert metrics.messages_delivered == 0
        assert metrics.messages_failed == 0
        assert metrics.mean_delivery_time == 0.0
        assert metrics.chain_completion_times == {}

    @given(
        failure_events=st.lists(
            message_delivery_failure_event_st(), min_size=0, max_size=3
        ),
        chain_events=st.lists(chain_completion_event_st(), min_size=0, max_size=3),
        fuel_events=st.lists(fuel_consumed_event_st(), min_size=0, max_size=3),
    )
    @settings(max_examples=200)
    def test_total_duration_with_mixed_events(
        self,
        failure_events: list[MessageDeliveryFailureEvent],
        chain_events: list[ChainCompletionEvent],
        fuel_events: list[FuelConsumedEvent],
    ):
        """total_duration correctly reflects max timestamp across mixed event types."""
        all_events = failure_events + chain_events + fuel_events
        assume(len(all_events) > 0)

        bus = EventBus()
        reporter = Reporter(bus)

        for event in all_events:
            bus.publish(event)

        metrics = reporter.get_metrics()
        expected_max = max(e.timestamp for e in all_events)
        assert metrics.total_duration == expected_max

    @given(
        chain_events=st.lists(chain_completion_event_st(), min_size=1, max_size=5),
        other_events=st.lists(any_event_st, min_size=0, max_size=5),
    )
    @settings(max_examples=100)
    def test_chain_completion_times_unaffected_by_other_events(
        self,
        chain_events: list[ChainCompletionEvent],
        other_events: list[SimulationEvent],
    ):
        """chain_completion_times is computed solely from ChainCompletionEvent instances."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in chain_events:
            bus.publish(event)
        for event in other_events:
            bus.publish(event)

        metrics = reporter.get_metrics()

        # Collect expected from chain events and any ChainCompletionEvents in other_events
        expected = {}
        for event in chain_events:
            expected[event.chain_id] = event.elapsed_time
        for event in other_events:
            if isinstance(event, ChainCompletionEvent):
                expected[event.chain_id] = event.elapsed_time

        assert metrics.chain_completion_times == expected

    @given(
        failure_events=st.lists(
            message_delivery_failure_event_st(), min_size=1, max_size=5
        ),
    )
    @settings(max_examples=100)
    def test_only_failures_means_zero_delivered(
        self, failure_events: list[MessageDeliveryFailureEvent]
    ):
        """When only failure events exist, messages_delivered is 0 and mean_delivery_time is 0."""
        bus = EventBus()
        reporter = Reporter(bus)

        for event in failure_events:
            bus.publish(event)

        metrics = reporter.get_metrics()
        assert metrics.messages_delivered == 0
        assert metrics.messages_failed == len(failure_events)
        assert metrics.messages_sent == len(failure_events)
        assert metrics.mean_delivery_time == 0.0
