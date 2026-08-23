"""Synchronous publish-subscribe event bus.

The EventBus provides decoupled communication between domain modules.
Events are delivered synchronously to all type-matched subscribers before
the publish call returns. Subscriber faults are isolated — a failing handler
is logged and skipped, with delivery continuing to remaining subscribers.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
"""

import logging
from collections import defaultdict
from typing import Callable, Type

from enterprise_sim.models.events import SimulationEvent

logger = logging.getLogger(__name__)


class EventBus:
    """Synchronous publish-subscribe event bus.

    Events are delivered in subscription registration order. Wildcard
    subscribers (registered via subscribe_all) receive all event types
    and are delivered after type-specific subscribers, also in registration order.
    """

    def __init__(self) -> None:
        # Type-specific subscribers: event_type -> list of (handler, subscriber_id)
        self._subscribers: dict[Type[SimulationEvent], list[tuple[Callable[[SimulationEvent], None], str]]] = defaultdict(list)
        # Wildcard subscribers: list of (handler, subscriber_id)
        self._wildcard_subscribers: list[tuple[Callable[[SimulationEvent], None], str]] = []
        # Global registration order for consistent delivery ordering
        self._registration_order: list[tuple[Callable[[SimulationEvent], None], str, Type[SimulationEvent] | None]] = []

    def publish(self, event: SimulationEvent) -> None:
        """Deliver event to all subscribers of this event type synchronously.

        Delivery order follows subscription registration order across both
        type-specific and wildcard subscribers. If a subscriber raises an
        exception, the error is logged with the subscriber_id and event_type,
        the faulting subscriber is skipped, and delivery continues to the
        remaining subscribers.

        If no subscribers are registered for the event type (and no wildcard
        subscribers exist), the event is silently discarded.
        """
        event_type = type(event)

        # Build the delivery list in registration order
        # We iterate the global registration order and include handlers that match
        handlers_to_call: list[tuple[Callable[[SimulationEvent], None], str]] = []
        for handler, subscriber_id, subscribed_type in self._registration_order:
            if subscribed_type is None:
                # Wildcard subscriber — receives all events
                handlers_to_call.append((handler, subscriber_id))
            elif subscribed_type is event_type:
                # Type-specific subscriber matching this event type
                handlers_to_call.append((handler, subscriber_id))

        # Deliver to each handler, isolating faults
        for handler, subscriber_id in handlers_to_call:
            try:
                handler(event)
            except Exception as exc:
                logger.error(
                    "Subscriber '%s' raised an exception handling event type '%s': %s",
                    subscriber_id,
                    event_type.__name__,
                    exc,
                )

    def subscribe(
        self,
        event_type: Type[SimulationEvent],
        handler: Callable[[SimulationEvent], None],
        subscriber_id: str,
    ) -> None:
        """Register a handler for a specific event type.

        The handler will be called for all events of exactly this type
        published after registration. Delivery order follows registration order.
        """
        self._subscribers[event_type].append((handler, subscriber_id))
        self._registration_order.append((handler, subscriber_id, event_type))

    def subscribe_all(
        self,
        handler: Callable[[SimulationEvent], None],
        subscriber_id: str,
    ) -> None:
        """Register a handler that receives ALL event types.

        Used by the Reporter to collect every event published on the bus.
        Wildcard handlers are delivered in their registration order relative
        to all other subscriptions.
        """
        self._wildcard_subscribers.append((handler, subscriber_id))
        self._registration_order.append((handler, subscriber_id, None))
