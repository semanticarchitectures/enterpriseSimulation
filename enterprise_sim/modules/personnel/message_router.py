"""Message router for the Personnel Module.

Routes C2 messages to personnel based on role and duty station. Handles
message queuing for non-READY recipients and publishes appropriate events
for routing outcomes.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from enterprise_sim.models.personnel import (
    MessageRoutingRule,
    Personnel,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    MessageDelayedEvent,
    MessageReceivedEvent,
    MessageUnroutableEvent,
    MessageUnroutedEvent,
    ReceiverUnresolvedEvent,
    SenderUnresolvedEvent,
)

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus


def _event_id(prefix: str, timestamp: float) -> str:
    """Generate a unique event entity_id."""
    return f"{prefix}_{timestamp}_{uuid.uuid4().hex[:8]}"


@dataclass
class QueuedMessage:
    """A message queued for later delivery when personnel becomes READY."""

    message_type: str
    personnel_name: str
    timestamp: float


class MessageRouter:
    """Routes C2 messages to personnel based on role and duty station.

    The router uses MessageRoutingRules to match message types to destination
    roles, then resolves specific personnel at duty stations. Messages destined
    for non-READY personnel are queued and delivered on state transition.
    """

    def __init__(self) -> None:
        # Queue of messages per personnel name, awaiting READY state
        self._message_queue: dict[str, list[QueuedMessage]] = defaultdict(list)

    def resolve_recipient(
        self,
        message_type: str,
        destination_node: str,
        roster: list[Personnel],
        routing_rules: list[MessageRoutingRule],
    ) -> Personnel | None:
        """Find the personnel member at destination_node with the role
        matching the routing rule for this message type.

        Args:
            message_type: The C2 message type being routed.
            destination_node: The destination node where the message is delivered.
            roster: The personnel roster to search.
            routing_rules: The configured message routing rules.

        Returns:
            The matching Personnel member, or None if no match found.
        """
        # Find the routing rule for this message type
        rule = self._find_routing_rule(message_type, routing_rules)
        if rule is None:
            return None

        # Find personnel at destination_node whose role matches the rule's destination_role
        for person in roster:
            if (
                person.duty_station == destination_node
                and person.role == rule.destination_role
            ):
                return person

        return None

    def resolve_sender(
        self,
        message_type: str,
        source_node: str,
        roster: list[Personnel],
        routing_rules: list[MessageRoutingRule],
    ) -> Personnel | None:
        """Find the personnel member at source_node with the role
        matching the routing rule for this message type.

        Args:
            message_type: The C2 message type being sent.
            source_node: The source node where the message originates.
            roster: The personnel roster to search.
            routing_rules: The configured message routing rules.

        Returns:
            The matching Personnel member, or None if no match found.
        """
        # Find the routing rule for this message type
        rule = self._find_routing_rule(message_type, routing_rules)
        if rule is None:
            return None

        # Find personnel at source_node whose role matches the rule's destination_role
        for person in roster:
            if (
                person.duty_station == source_node
                and person.role == rule.destination_role
            ):
                return person

        return None

    def queue_message(
        self, message_type: str, personnel_name: str, timestamp: float
    ) -> None:
        """Queue a message for later delivery when personnel becomes READY.

        Args:
            message_type: The C2 message type being queued.
            personnel_name: The name of the personnel the message is destined for.
            timestamp: The simulation timestamp when the message was queued.
        """
        queued = QueuedMessage(
            message_type=message_type,
            personnel_name=personnel_name,
            timestamp=timestamp,
        )
        self._message_queue[personnel_name].append(queued)

    def deliver_queued_messages(
        self,
        personnel_name: str,
        event_bus: EventBus,
        timestamp: float,
        roster: list[Personnel],
    ) -> None:
        """Flush the message queue for a personnel member when they become READY.

        Publishes a MessageReceivedEvent for each queued message delivered.

        Args:
            personnel_name: The name of the personnel who became READY.
            event_bus: The event bus to publish delivery events on.
            timestamp: The simulation timestamp of delivery (actual delivery time).
            roster: The personnel roster to look up role information.
        """
        if personnel_name not in self._message_queue:
            return

        queued_messages = self._message_queue.pop(personnel_name)

        # Look up the personnel member's role for the event
        person = self._find_personnel_by_name(personnel_name, roster)
        if person is None:
            return

        for msg in queued_messages:
            event_bus.publish(
                MessageReceivedEvent(
                    entity_id=_event_id("message_received", timestamp),
                    message_type=msg.message_type,
                    recipient_name=personnel_name,
                    recipient_role=person.role,
                    timestamp=timestamp,
                )
            )

    def route_message(
        self,
        message_type: str,
        destination_node: str,
        roster: list[Personnel],
        routing_rules: list[MessageRoutingRule],
        event_bus: EventBus,
        timestamp: float,
    ) -> Personnel | None:
        """Route a message to the appropriate personnel member.

        Handles all routing outcomes:
        - No routing rule: publishes MessageUnroutedEvent
        - No matching personnel at destination: publishes MessageUnroutableEvent
        - Non-READY recipient: queues message, publishes MessageDelayedEvent
        - Successful delivery: publishes MessageReceivedEvent

        Args:
            message_type: The C2 message type being routed.
            destination_node: The destination node of the message.
            roster: The personnel roster.
            routing_rules: The configured message routing rules.
            event_bus: The event bus for publishing events.
            timestamp: Current simulation timestamp.

        Returns:
            The recipient Personnel if successfully delivered, None otherwise.
        """
        # Check if a routing rule exists for this message type
        rule = self._find_routing_rule(message_type, routing_rules)
        if rule is None:
            event_bus.publish(
                MessageUnroutedEvent(
                    entity_id=_event_id("message_unrouted", timestamp),
                    message_type=message_type,
                    destination_node=destination_node,
                    timestamp=timestamp,
                )
            )
            return None

        # Find personnel at destination matching the rule's destination_role
        recipient = self.resolve_recipient(
            message_type, destination_node, roster, routing_rules
        )
        if recipient is None:
            event_bus.publish(
                MessageUnroutableEvent(
                    entity_id=_event_id("message_unroutable", timestamp),
                    message_type=message_type,
                    expected_role=rule.destination_role,
                    destination_node=destination_node,
                    timestamp=timestamp,
                )
            )
            return None

        # Check if recipient is READY
        if recipient.readiness_state != ReadinessState.READY:
            self.queue_message(message_type, recipient.name, timestamp)
            event_bus.publish(
                MessageDelayedEvent(
                    entity_id=_event_id("message_delayed", timestamp),
                    message_type=message_type,
                    personnel_name=recipient.name,
                    readiness_state=recipient.readiness_state.value,
                    timestamp=timestamp,
                )
            )
            return None

        # Successful delivery
        event_bus.publish(
            MessageReceivedEvent(
                entity_id=_event_id("message_received", timestamp),
                message_type=message_type,
                recipient_name=recipient.name,
                recipient_role=recipient.role,
                timestamp=timestamp,
            )
        )
        return recipient

    def resolve_sender_with_event(
        self,
        message_type: str,
        source_node: str,
        roster: list[Personnel],
        routing_rules: list[MessageRoutingRule],
        event_bus: EventBus,
        timestamp: float,
    ) -> Personnel | None:
        """Resolve the sender personnel, publishing SenderUnresolvedEvent if not found.

        Args:
            message_type: The C2 message type being sent.
            source_node: The source node of the message.
            roster: The personnel roster.
            routing_rules: The configured message routing rules.
            event_bus: The event bus for publishing events.
            timestamp: Current simulation timestamp.

        Returns:
            The sender Personnel if resolved, None otherwise.
        """
        sender = self.resolve_sender(message_type, source_node, roster, routing_rules)
        if sender is None:
            # Only publish if there IS a routing rule but no matching personnel
            rule = self._find_routing_rule(message_type, routing_rules)
            if rule is not None:
                event_bus.publish(
                    SenderUnresolvedEvent(
                        entity_id=_event_id("sender_unresolved", timestamp),
                        message_type=message_type,
                        source_node=source_node,
                        timestamp=timestamp,
                    )
                )
        return sender

    def resolve_receiver_with_event(
        self,
        message_type: str,
        destination_node: str,
        roster: list[Personnel],
        routing_rules: list[MessageRoutingRule],
        event_bus: EventBus,
        timestamp: float,
    ) -> Personnel | None:
        """Resolve the receiver personnel, publishing ReceiverUnresolvedEvent if not found.

        Args:
            message_type: The C2 message type being received.
            destination_node: The destination node of the message.
            roster: The personnel roster.
            routing_rules: The configured message routing rules.
            event_bus: The event bus for publishing events.
            timestamp: Current simulation timestamp.

        Returns:
            The receiver Personnel if resolved, None otherwise.
        """
        receiver = self.resolve_recipient(
            message_type, destination_node, roster, routing_rules
        )
        if receiver is None:
            # Only publish if there IS a routing rule but no matching personnel
            rule = self._find_routing_rule(message_type, routing_rules)
            if rule is not None:
                event_bus.publish(
                    ReceiverUnresolvedEvent(
                        entity_id=_event_id("receiver_unresolved", timestamp),
                        message_type=message_type,
                        destination_node=destination_node,
                        timestamp=timestamp,
                    )
                )
        return receiver

    def has_queued_messages(self, personnel_name: str) -> bool:
        """Check if a personnel member has queued messages.

        Args:
            personnel_name: The name of the personnel to check.

        Returns:
            True if messages are queued for the personnel member.
        """
        return bool(self._message_queue.get(personnel_name))

    def get_queue_size(self, personnel_name: str) -> int:
        """Get the number of queued messages for a personnel member.

        Args:
            personnel_name: The name of the personnel to check.

        Returns:
            The number of queued messages.
        """
        return len(self._message_queue.get(personnel_name, []))

    def clear(self) -> None:
        """Clear all queued messages. Used during finalization."""
        self._message_queue.clear()

    @staticmethod
    def _find_routing_rule(
        message_type: str, routing_rules: list[MessageRoutingRule]
    ) -> MessageRoutingRule | None:
        """Find the routing rule for a given message type.

        Args:
            message_type: The message type to look up.
            routing_rules: The list of configured routing rules.

        Returns:
            The matching routing rule, or None if no rule exists.
        """
        for rule in routing_rules:
            if rule.message_type == message_type:
                return rule
        return None

    @staticmethod
    def _find_personnel_by_name(
        name: str, roster: list[Personnel]
    ) -> Personnel | None:
        """Find a personnel member by name in the roster.

        Args:
            name: The name to search for.
            roster: The personnel roster.

        Returns:
            The matching Personnel, or None if not found.
        """
        for person in roster:
            if person.name == name:
                return person
        return None
