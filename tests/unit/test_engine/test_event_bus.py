"""Unit tests for the EventBus implementation.

Tests cover:
- Synchronous delivery to type-matched subscribers
- Wildcard subscription receiving all event types
- Delivery in registration order
- Fault isolation (skip faulting subscriber, continue to remaining)
- Silent discard when no subscribers registered
- Subscription timing (only receives events published after registration)
"""

import logging
from unittest.mock import MagicMock

import pytest

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import (
    FuelConsumedEvent,
    GateCompletionEvent,
    SimulationEvent,
)


def _make_gate_event() -> GateCompletionEvent:
    return GateCompletionEvent(
        entity_id="evt-1",
        timestamp=1.0,
        source_module="authorization",
        gate_name="gate_alpha",
        chain_id="chain-1",
    )


def _make_fuel_event() -> FuelConsumedEvent:
    return FuelConsumedEvent(
        entity_id="evt-2",
        timestamp=2.0,
        source_module="aerodynamics",
        leg_id="leg-1",
        fuel_quantity=100.5,
    )


class TestEventBusSubscribe:
    """Tests for type-specific subscribe behavior."""

    def test_subscribe_receives_matching_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(GateCompletionEvent, lambda e: received.append(e), "sub-1")

        event = _make_gate_event()
        bus.publish(event)

        assert received == [event]

    def test_subscribe_does_not_receive_non_matching_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(GateCompletionEvent, lambda e: received.append(e), "sub-1")

        bus.publish(_make_fuel_event())

        assert received == []

    def test_multiple_subscribers_all_receive(self):
        bus = EventBus()
        received_1 = []
        received_2 = []
        bus.subscribe(GateCompletionEvent, lambda e: received_1.append(e), "sub-1")
        bus.subscribe(GateCompletionEvent, lambda e: received_2.append(e), "sub-2")

        event = _make_gate_event()
        bus.publish(event)

        assert received_1 == [event]
        assert received_2 == [event]


class TestEventBusSubscribeAll:
    """Tests for wildcard subscribe_all behavior."""

    def test_subscribe_all_receives_all_event_types(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e), "reporter")

        gate_event = _make_gate_event()
        fuel_event = _make_fuel_event()
        bus.publish(gate_event)
        bus.publish(fuel_event)

        assert received == [gate_event, fuel_event]

    def test_subscribe_all_mixed_with_type_specific(self):
        bus = EventBus()
        all_events = []
        gate_events = []
        bus.subscribe(GateCompletionEvent, lambda e: gate_events.append(e), "gate-sub")
        bus.subscribe_all(lambda e: all_events.append(e), "reporter")

        gate_event = _make_gate_event()
        fuel_event = _make_fuel_event()
        bus.publish(gate_event)
        bus.publish(fuel_event)

        assert gate_events == [gate_event]
        assert all_events == [gate_event, fuel_event]


class TestEventBusDeliveryOrder:
    """Tests for subscription registration order delivery."""

    def test_delivery_in_registration_order(self):
        bus = EventBus()
        order = []
        bus.subscribe(GateCompletionEvent, lambda e: order.append("first"), "sub-1")
        bus.subscribe(GateCompletionEvent, lambda e: order.append("second"), "sub-2")
        bus.subscribe(GateCompletionEvent, lambda e: order.append("third"), "sub-3")

        bus.publish(_make_gate_event())

        assert order == ["first", "second", "third"]

    def test_wildcard_interleaved_with_type_specific_in_registration_order(self):
        bus = EventBus()
        order = []
        bus.subscribe(GateCompletionEvent, lambda e: order.append("type-1"), "sub-1")
        bus.subscribe_all(lambda e: order.append("wildcard"), "reporter")
        bus.subscribe(GateCompletionEvent, lambda e: order.append("type-2"), "sub-2")

        bus.publish(_make_gate_event())

        assert order == ["type-1", "wildcard", "type-2"]


class TestEventBusFaultIsolation:
    """Tests for subscriber exception handling."""

    def test_faulting_subscriber_does_not_block_others(self):
        bus = EventBus()
        received = []

        def failing_handler(e):
            raise RuntimeError("handler crashed")

        bus.subscribe(GateCompletionEvent, lambda e: received.append("before"), "sub-1")
        bus.subscribe(GateCompletionEvent, failing_handler, "sub-faulty")
        bus.subscribe(GateCompletionEvent, lambda e: received.append("after"), "sub-3")

        bus.publish(_make_gate_event())

        assert received == ["before", "after"]

    def test_faulting_subscriber_logs_error(self, caplog):
        bus = EventBus()

        def failing_handler(e):
            raise ValueError("bad value")

        bus.subscribe(GateCompletionEvent, failing_handler, "faulty-module")

        with caplog.at_level(logging.ERROR):
            bus.publish(_make_gate_event())

        assert "faulty-module" in caplog.text
        assert "GateCompletionEvent" in caplog.text

    def test_multiple_faulting_subscribers_all_isolated(self):
        bus = EventBus()
        received = []

        bus.subscribe(GateCompletionEvent, lambda e: received.append("ok-1"), "sub-1")
        bus.subscribe(
            GateCompletionEvent,
            lambda e: (_ for _ in ()).throw(RuntimeError("fail-1")),
            "sub-faulty-1",
        )
        bus.subscribe(
            GateCompletionEvent,
            lambda e: (_ for _ in ()).throw(RuntimeError("fail-2")),
            "sub-faulty-2",
        )
        bus.subscribe(GateCompletionEvent, lambda e: received.append("ok-2"), "sub-4")

        bus.publish(_make_gate_event())

        assert received == ["ok-1", "ok-2"]


class TestEventBusSilentDiscard:
    """Tests for silent discard when no subscribers."""

    def test_publish_with_no_subscribers_does_not_raise(self):
        bus = EventBus()
        # Should not raise
        bus.publish(_make_gate_event())

    def test_publish_with_no_matching_type_discards_silently(self):
        bus = EventBus()
        received = []
        bus.subscribe(FuelConsumedEvent, lambda e: received.append(e), "fuel-sub")

        bus.publish(_make_gate_event())

        assert received == []


class TestEventBusSubscriptionTiming:
    """Tests for subscription timing — only receives events after registration."""

    def test_subscriber_does_not_receive_events_published_before_registration(self):
        bus = EventBus()

        bus.publish(_make_gate_event())

        received = []
        bus.subscribe(GateCompletionEvent, lambda e: received.append(e), "late-sub")

        assert received == []

    def test_subscriber_receives_events_published_after_registration(self):
        bus = EventBus()
        received = []
        bus.subscribe(GateCompletionEvent, lambda e: received.append(e), "sub-1")

        event = _make_gate_event()
        bus.publish(event)

        assert received == [event]
