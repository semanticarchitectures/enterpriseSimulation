"""CrossDomainGatewayModule implementation.

Provides cross-domain message routing enforcement and sanitization for
messages traversing between security enclaves. This is a reactive module
with no SimPy processes of its own — it is called by the C2 Message Module
when messages need to cross enclave boundaries.

Requirements: 7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Generator, Set

import simpy

from enterprise_sim.models.events import (
    ClassificationViolationEvent,
    SanitizationCompleteEvent,
)
from enterprise_sim.modules.base import DomainModule

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.mission import MissionConfiguration


class CrossDomainGatewayModule(DomainModule):
    """Cross-domain gateway enforcing routing rules between security enclaves.

    This module checks whether a message with a given classification is
    permitted to traverse from one enclave to another based on configured
    routing rules. When permitted, it applies a sanitization delay before
    allowing the message through.

    This is a REACTIVE module — it has no SimPy processes of its own and
    is called by the C2 Message Module during message routing.
    """

    module_type: ClassVar[str] = "cross_domain"
    dependencies: ClassVar[Set[str]] = set()

    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Set up gateway state from configuration.

        Stores enclave definitions, builds an O(1) routing rules lookup dict
        mapping (source_enclave, dest_enclave) → set of permitted classifications,
        and stores the sanitization latency.
        """
        self._event_bus = event_bus
        self._env = env

        gateway_config = config.cross_domain_gateway
        if gateway_config is None:
            self._enclaves = {}
            self._routing_rules: dict[tuple[str, str], set[str]] = {}
            self._sanitization_latency = 0.0
            return

        # Store enclave definitions keyed by name
        self._enclaves = {enc.name: enc for enc in gateway_config.enclaves}

        # Build routing rules lookup: (source, dest) → set of permitted classifications
        self._routing_rules = {}
        for rule in gateway_config.routing_rules:
            key = (rule.source_enclave, rule.destination_enclave)
            self._routing_rules[key] = set(rule.permitted_classifications)

        self._sanitization_latency = gateway_config.sanitization_latency

    def check_routing(
        self, classification: str, source_enclave: str, dest_enclave: str
    ) -> bool:
        """Check whether a message classification is permitted between enclaves.

        Args:
            classification: The message's classification level.
            source_enclave: Name of the source enclave.
            dest_enclave: Name of the destination enclave.

        Returns:
            True if the classification is in the permitted_classifications for
            the (source, dest) routing rule pair; False otherwise.
        """
        key = (source_enclave, dest_enclave)
        permitted = self._routing_rules.get(key)
        if permitted is None:
            return False
        return classification in permitted

    def process_message(
        self,
        env: simpy.Environment,
        message_id: str,
        classification: str,
        source: str,
        dest: str,
    ) -> Generator[simpy.events.Event, None, float | None]:
        """Process a message through the cross-domain gateway.

        This is a SimPy generator process. It checks routing rules and either
        denies the message (publishing a violation event) or applies sanitization
        delay and permits it.

        Args:
            env: The SimPy simulation environment.
            message_id: Identifier of the message being processed.
            classification: The message's classification level.
            source: Name of the source enclave.
            dest: Name of the destination enclave.

        Yields:
            SimPy timeout event for sanitization delay (if permitted).

        Returns:
            The sanitization latency if permitted, or None if denied.
        """
        if not self.check_routing(classification, source, dest):
            # Routing denied — publish violation event and discard message
            violation_event = ClassificationViolationEvent(
                entity_id=f"violation_{message_id}",
                timestamp=env.now,
                source_module=self.module_type,
                message_id=message_id,
                source_enclave=source,
                destination_enclave=dest,
                message_classification=classification,
            )
            self._event_bus.publish(violation_event)
            return None

        # Routing permitted — apply sanitization delay
        yield env.timeout(self._sanitization_latency)

        # Publish sanitization complete event
        sanitization_event = SanitizationCompleteEvent(
            entity_id=f"sanitization_{message_id}",
            timestamp=env.now,
            source_module=self.module_type,
            sanitization_duration=self._sanitization_latency,
            source_enclave=source,
            destination_enclave=dest,
        )
        self._event_bus.publish(sanitization_event)

        return self._sanitization_latency

    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Return an empty list — this is a reactive module.

        The CrossDomainGatewayModule does not create its own SimPy processes.
        It is called by the C2 Message Module when messages need cross-domain routing.
        """
        return []

    def finalize(self) -> None:
        """Clean up internal state."""
        self._enclaves = {}
        self._routing_rules = {}
        self._sanitization_latency = 0.0
