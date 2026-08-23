"""Property tests for Event Bus guarantees.

**Property 24: Event Bus Synchronous Delivery**
**Property 25: Event Bus Fault Isolation**
**Property 26: Event Bus Subscription Timing**
**Validates: Requirements 10.2, 10.3, 10.5, 10.6**

For any set of subscribers registered for an event type, publishing an event SHALL
deliver to all subscribers before the publish call returns, and delivery SHALL occur
in subscription registration order.

For any set of subscribers where one raises an exception during handling, the event
SHALL still be delivered to all remaining subscribers in their original order, and
the exception SHALL be logged.

For any subscriber that registers for an event type, it SHALL receive all events of
that type published after registration, and SHALL NOT receive events published before
registration.
"""

import logging
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import (
    SimulationEvent,
    GateCompletionEvent,
    FuelConsumedEvent,
    ExtractionStartEvent,
)


# === Hypothesis Strategies ===

# Strategy for subscriber identifiers
subscriber_id_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)

# Strategy for generating a list of unique subscriber IDs
subscriber_ids_st = st.lists(
    subscriber_id_st,
    min_size=1,
    max_size=10,
    unique=True,
)

# Non-negative float for timestamps
non_negative_float = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)

# Positive float for fuel quantities
positive_float = st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False)

# Non-empty string strategy
non_empty_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)


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


# Strategy for any event type (used in multi-type scenarios)
any_event_st = st.one_of(
    gate_completion_event_st(),
    fuel_consumed_event_st(),
    extraction_start_event_st(),
)

# Strategy for a sequence of events of a specific type
event_sequence_st = st.lists(
    gate_completion_event_st(),
    min_size=1,
    max_size=5,
)

# Strategy for which subscriber index should fault (raise exception)
fault_index_st = st.integers(min_value=0)


class TestEventBusSynchronousDelivery:
    """Property 24: Event Bus Synchronous Delivery.

    Validates: Requirements 10.2, 10.3

    For any set of subscribers registered for an event type, publishing an event
    SHALL deliver to all subscribers before the publish call returns, and delivery
    SHALL occur in subscription registration order.
    """

    @given(
        subscriber_ids=subscriber_ids_st,
        event=gate_completion_event_st(),
    )
    @settings(max_examples=100)
    def test_all_subscribers_receive_event(self, subscriber_ids: list[str], event: GateCompletionEvent):
        """All registered subscribers receive the published event."""
        bus = EventBus()
        received: dict[str, list[SimulationEvent]] = {sid: [] for sid in subscriber_ids}

        for sid in subscriber_ids:
            bus.subscribe(
                GateCompletionEvent,
                lambda e, s=sid: received[s].append(e),
                sid,
            )

        bus.publish(event)

        # All subscribers must have received exactly the event
        for sid in subscriber_ids:
            assert len(received[sid]) == 1, f"Subscriber '{sid}' did not receive event"
            assert received[sid][0] is event

    @given(
        subscriber_ids=subscriber_ids_st,
        event=gate_completion_event_st(),
    )
    @settings(max_examples=100)
    def test_delivery_in_registration_order(self, subscriber_ids: list[str], event: GateCompletionEvent):
        """Events are delivered in subscriber registration order."""
        bus = EventBus()
        delivery_order: list[str] = []

        for sid in subscriber_ids:
            bus.subscribe(
                GateCompletionEvent,
                lambda e, s=sid: delivery_order.append(s),
                sid,
            )

        bus.publish(event)

        assert delivery_order == subscriber_ids

    @given(
        subscriber_ids=subscriber_ids_st,
        events=st.lists(gate_completion_event_st(), min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_synchronous_delivery_completes_before_return(
        self, subscriber_ids: list[str], events: list[GateCompletionEvent]
    ):
        """All deliveries complete before publish returns (synchronous guarantee)."""
        bus = EventBus()
        total_deliveries: list[tuple[str, SimulationEvent]] = []

        for sid in subscriber_ids:
            bus.subscribe(
                GateCompletionEvent,
                lambda e, s=sid: total_deliveries.append((s, e)),
                sid,
            )

        for event in events:
            count_before = len(total_deliveries)
            bus.publish(event)
            # After publish returns, all subscribers must have been called
            count_after = len(total_deliveries)
            assert count_after - count_before == len(subscriber_ids)

    @given(
        type_a_subs=subscriber_ids_st,
        type_b_subs=subscriber_ids_st,
        event=gate_completion_event_st(),
    )
    @settings(max_examples=100)
    def test_only_matching_type_subscribers_receive(
        self, type_a_subs: list[str], type_b_subs: list[str], event: GateCompletionEvent
    ):
        """Only subscribers of the matching event type receive the event."""
        bus = EventBus()
        gate_received: list[str] = []
        fuel_received: list[str] = []

        for sid in type_a_subs:
            bus.subscribe(
                GateCompletionEvent,
                lambda e, s=sid: gate_received.append(s),
                f"gate_{sid}",
            )

        for sid in type_b_subs:
            bus.subscribe(
                FuelConsumedEvent,
                lambda e, s=sid: fuel_received.append(s),
                f"fuel_{sid}",
            )

        bus.publish(event)

        # Only GateCompletionEvent subscribers should receive
        assert len(gate_received) == len(type_a_subs)
        assert len(fuel_received) == 0


class TestEventBusFaultIsolation:
    """Property 25: Event Bus Fault Isolation.

    Validates: Requirements 10.5

    For any set of subscribers where one raises an exception during handling, the
    event SHALL still be delivered to all remaining subscribers in their original
    order, and the exception SHALL be logged.
    """

    @given(
        subscriber_ids=st.lists(subscriber_id_st, min_size=2, max_size=10, unique=True),
        fault_index=fault_index_st,
        event=gate_completion_event_st(),
    )
    @settings(max_examples=100)
    def test_fault_does_not_prevent_remaining_delivery(
        self, subscriber_ids: list[str], fault_index: int, event: GateCompletionEvent
    ):
        """A faulting subscriber does not prevent delivery to remaining subscribers."""
        fault_index = fault_index % len(subscriber_ids)
        bus = EventBus()
        received: dict[str, list[SimulationEvent]] = {sid: [] for sid in subscriber_ids}

        for i, sid in enumerate(subscriber_ids):
            if i == fault_index:
                def faulty_handler(e, s=sid):
                    received[s].append(e)
                    raise RuntimeError(f"Subscriber {s} exploded")
                bus.subscribe(GateCompletionEvent, faulty_handler, sid)
            else:
                bus.subscribe(
                    GateCompletionEvent,
                    lambda e, s=sid: received[s].append(e),
                    sid,
                )

        bus.publish(event)

        # All subscribers (including the faulting one) should have received the event
        for sid in subscriber_ids:
            assert len(received[sid]) == 1, f"Subscriber '{sid}' missed event"
            assert received[sid][0] is event

    @given(
        subscriber_ids=st.lists(subscriber_id_st, min_size=2, max_size=10, unique=True),
        fault_index=fault_index_st,
        event=gate_completion_event_st(),
    )
    @settings(max_examples=100)
    def test_delivery_order_preserved_despite_fault(
        self, subscriber_ids: list[str], fault_index: int, event: GateCompletionEvent
    ):
        """Delivery order is preserved even when a subscriber faults."""
        fault_index = fault_index % len(subscriber_ids)
        bus = EventBus()
        delivery_order: list[str] = []

        for i, sid in enumerate(subscriber_ids):
            if i == fault_index:
                def faulty_handler(e, s=sid):
                    delivery_order.append(s)
                    raise ValueError(f"Fault in {s}")
                bus.subscribe(GateCompletionEvent, faulty_handler, sid)
            else:
                bus.subscribe(
                    GateCompletionEvent,
                    lambda e, s=sid: delivery_order.append(s),
                    sid,
                )

        bus.publish(event)

        # All subscribers should appear in the delivery order, in registration order
        assert delivery_order == subscriber_ids

    @given(
        subscriber_ids=st.lists(subscriber_id_st, min_size=2, max_size=10, unique=True),
        fault_index=fault_index_st,
        event=gate_completion_event_st(),
    )
    @settings(max_examples=100)
    def test_exception_is_logged(
        self, subscriber_ids: list[str], fault_index: int, event: GateCompletionEvent
    ):
        """Exceptions from faulting subscribers are logged with subscriber ID and event type."""
        fault_index = fault_index % len(subscriber_ids)
        faulty_sid = subscriber_ids[fault_index]
        bus = EventBus()

        for i, sid in enumerate(subscriber_ids):
            if i == fault_index:
                def faulty_handler(e):
                    raise RuntimeError("test fault")
                bus.subscribe(GateCompletionEvent, faulty_handler, sid)
            else:
                bus.subscribe(
                    GateCompletionEvent,
                    lambda e: None,
                    sid,
                )

        with patch("enterprise_sim.engine.event_bus.logger") as mock_logger:
            bus.publish(event)
            # Logger.error should have been called with subscriber_id and event type
            mock_logger.error.assert_called()
            log_call_args = mock_logger.error.call_args
            log_message = log_call_args[0][0] % log_call_args[0][1:]
            assert faulty_sid in log_message
            assert "GateCompletionEvent" in log_message

    @given(
        subscriber_ids=st.lists(subscriber_id_st, min_size=3, max_size=8, unique=True),
        fault_indices=st.lists(st.integers(min_value=0), min_size=2, max_size=3),
        event=gate_completion_event_st(),
    )
    @settings(max_examples=50)
    def test_multiple_faults_still_deliver_to_all(
        self, subscriber_ids: list[str], fault_indices: list[int], event: GateCompletionEvent
    ):
        """Multiple faulting subscribers don't prevent delivery to non-faulting ones."""
        # Normalize fault indices to valid range
        fault_set = set(idx % len(subscriber_ids) for idx in fault_indices)
        bus = EventBus()
        received: dict[str, list[SimulationEvent]] = {sid: [] for sid in subscriber_ids}

        for i, sid in enumerate(subscriber_ids):
            if i in fault_set:
                def faulty_handler(e, s=sid):
                    received[s].append(e)
                    raise RuntimeError(f"Fault in {s}")
                bus.subscribe(GateCompletionEvent, faulty_handler, sid)
            else:
                bus.subscribe(
                    GateCompletionEvent,
                    lambda e, s=sid: received[s].append(e),
                    sid,
                )

        bus.publish(event)

        # All subscribers should still have received the event
        for sid in subscriber_ids:
            assert len(received[sid]) == 1


class TestEventBusSubscriptionTiming:
    """Property 26: Event Bus Subscription Timing.

    Validates: Requirements 10.6

    For any subscriber that registers for an event type, it SHALL receive all events
    of that type published after registration, and SHALL NOT receive events published
    before registration.
    """

    @given(
        pre_events=st.lists(gate_completion_event_st(), min_size=1, max_size=5),
        post_events=st.lists(gate_completion_event_st(), min_size=1, max_size=5),
        subscriber_id=subscriber_id_st,
    )
    @settings(max_examples=100)
    def test_subscriber_receives_only_post_registration_events(
        self,
        pre_events: list[GateCompletionEvent],
        post_events: list[GateCompletionEvent],
        subscriber_id: str,
    ):
        """Subscriber receives events published after registration, not before."""
        bus = EventBus()
        received: list[SimulationEvent] = []

        # Publish pre-registration events
        for event in pre_events:
            bus.publish(event)

        # Register subscriber
        bus.subscribe(
            GateCompletionEvent,
            lambda e: received.append(e),
            subscriber_id,
        )

        # Publish post-registration events
        for event in post_events:
            bus.publish(event)

        # Should only have received post-registration events
        assert len(received) == len(post_events)
        for i, event in enumerate(post_events):
            assert received[i] is event

    @given(
        events=st.lists(gate_completion_event_st(), min_size=2, max_size=8),
        split_point=st.integers(min_value=1),
        subscriber_id=subscriber_id_st,
    )
    @settings(max_examples=100)
    def test_mid_sequence_subscription_timing(
        self,
        events: list[GateCompletionEvent],
        split_point: int,
        subscriber_id: str,
    ):
        """Subscriber registered mid-sequence receives only subsequent events."""
        split_point = split_point % len(events)
        assume(split_point > 0)  # Ensure at least 1 event before subscription

        bus = EventBus()
        received: list[SimulationEvent] = []

        # Publish first portion
        for event in events[:split_point]:
            bus.publish(event)

        # Register
        bus.subscribe(
            GateCompletionEvent,
            lambda e: received.append(e),
            subscriber_id,
        )

        # Publish remaining events
        for event in events[split_point:]:
            bus.publish(event)

        # Should only have received events after subscription
        expected = events[split_point:]
        assert len(received) == len(expected)
        for i, event in enumerate(expected):
            assert received[i] is event

    @given(
        subscriber_ids=st.lists(subscriber_id_st, min_size=2, max_size=5, unique=True),
        events=st.lists(gate_completion_event_st(), min_size=3, max_size=6),
    )
    @settings(max_examples=100)
    def test_staggered_subscriptions_receive_correct_events(
        self,
        subscriber_ids: list[str],
        events: list[GateCompletionEvent],
    ):
        """Multiple subscribers registered at different times each receive correct events."""
        assume(len(events) >= len(subscriber_ids))
        bus = EventBus()
        received: dict[str, list[SimulationEvent]] = {sid: [] for sid in subscriber_ids}

        # Space out subscription registration between event publications
        # Each subscriber registers after a certain number of events are published
        step = len(events) // len(subscriber_ids)

        for sub_idx, sid in enumerate(subscriber_ids):
            # Publish events up to this subscriber's registration point
            start = sub_idx * step
            end = (sub_idx + 1) * step if sub_idx < len(subscriber_ids) - 1 else len(events)

            # Register subscriber
            bus.subscribe(
                GateCompletionEvent,
                lambda e, s=sid: received[s].append(e),
                sid,
            )

            # Publish remaining events for this batch
            for event in events[start:end]:
                bus.publish(event)

        # Verify each subscriber: should receive events published from its batch onward
        for sub_idx, sid in enumerate(subscriber_ids):
            start = sub_idx * step
            expected_events = events[start:]
            assert len(received[sid]) == len(expected_events), (
                f"Subscriber '{sid}' (registered at index {sub_idx}) expected "
                f"{len(expected_events)} events, got {len(received[sid])}"
            )

    @given(
        subscriber_id=subscriber_id_st,
        gate_events=st.lists(gate_completion_event_st(), min_size=1, max_size=3),
        fuel_events=st.lists(fuel_consumed_event_st(), min_size=1, max_size=3),
    )
    @settings(max_examples=100)
    def test_subscription_type_isolation(
        self,
        subscriber_id: str,
        gate_events: list[GateCompletionEvent],
        fuel_events: list[FuelConsumedEvent],
    ):
        """Subscriber registered for one type does not receive events of other types."""
        bus = EventBus()
        received: list[SimulationEvent] = []

        # Subscribe only to GateCompletionEvent
        bus.subscribe(
            GateCompletionEvent,
            lambda e: received.append(e),
            subscriber_id,
        )

        # Publish both types
        for event in fuel_events:
            bus.publish(event)
        for event in gate_events:
            bus.publish(event)

        # Should only have received GateCompletionEvents
        assert len(received) == len(gate_events)
        for event in received:
            assert isinstance(event, GateCompletionEvent)
