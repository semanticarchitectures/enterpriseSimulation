"""C2MessageModule implementation.

Models Command and Control information flows using configurable message types.
For each configured C2 message type, a SimPy process simulates the complete
message lifecycle: generation → reachability check → transmission → optional
cross-domain routing → delivery (or failure).

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, ClassVar, Set

import simpy

from enterprise_sim.models.events import MessageDeliveredEvent, MessageDeliveryFailureEvent
from enterprise_sim.models.messaging import C2MessageInstance
from enterprise_sim.modules.base import DomainModule

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.messaging import C2MessageType
    from enterprise_sim.models.mission import MissionConfiguration
    from enterprise_sim.modules.cross_domain import CrossDomainGatewayModule


class C2MessageModule(DomainModule):
    """Domain module for C2 message flow simulation.

    For each configured C2MessageType, creates a SimPy process that simulates
    the full message lifecycle including generation, reachability checks,
    transmission latency, optional cross-domain gateway traversal, and
    delivery or failure handling.
    """

    module_type: ClassVar[str] = "c2_message"
    dependencies: ClassVar[Set[str]] = {"cross_domain"}

    def __init__(self) -> None:
        self._env: simpy.Environment | None = None
        self._event_bus: EventBus | None = None
        self._message_types: list[C2MessageType] = []
        self._message_log: list[C2MessageInstance] = []
        self._gateway: CrossDomainGatewayModule | None = None
        self._gateway_config = None
        self._route_graph_nodes: set[str] = set()

    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Set up message type definitions, message log, and gateway reference.

        Stores message type definitions from config, initializes the message log,
        builds the set of reachable nodes from the route graph and message
        source/destination nodes, and stores the cross-domain gateway config
        for latency calculations.
        """
        self._env = env
        self._event_bus = event_bus
        self._message_types = config.c2_messages or []
        self._message_log = []

        # Build set of reachable nodes from route graph node names
        self._route_graph_nodes: set[str] = set()
        if config.route_graph:
            for node in config.route_graph.nodes:
                self._route_graph_nodes.add(node.name)

        # Obtain gateway reference if cross_domain_gateway is configured
        # The gateway config provides sanitization_latency and routing rules
        self._gateway_config = config.cross_domain_gateway

    def set_gateway(self, gateway: CrossDomainGatewayModule | None) -> None:
        """Set the cross-domain gateway module reference.

        Called externally (e.g., during wiring) to provide access to the
        actual CrossDomainGatewayModule instance for message routing.
        """
        self._gateway = gateway

    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Return SimPy processes for each configured message type flow."""
        processes = []
        for msg_type in self._message_types:
            proc = env.process(self._message_flow(env, msg_type))
            processes.append(proc)
        return processes

    def finalize(self) -> None:
        """Clean up internal state."""
        self._env = None
        self._event_bus = None
        self._message_types = []
        self._gateway = None
        self._gateway_config = None
        self._route_graph_nodes = set()

    @property
    def message_log(self) -> list[C2MessageInstance]:
        """Access the message log for reporting."""
        return self._message_log

    def _is_reachable(self, destination_node: str, current_msg_type: C2MessageType) -> bool:
        """Check if a destination node is reachable.

        A destination is reachable if it exists in the route graph node names
        OR is a defined source/destination of other configured messages.
        """
        if destination_node in self._route_graph_nodes:
            return True

        # Check if the destination appears as source/destination of OTHER messages
        for msg in self._message_types:
            if msg is current_msg_type:
                continue
            if destination_node in (msg.source_node, msg.destination_node):
                return True

        return False

    def _message_flow(self, env: simpy.Environment, msg_type: C2MessageType):
        """SimPy process simulating the lifecycle of a single message type.

        Steps:
        1. Generate message instance (record generation_time)
        2. Check destination reachability
        3. Apply transmission latency
        4. Check cross-domain routing (if gateway available and message has classification)
        5. Deliver or fail
        """
        assert self._event_bus is not None

        # Step 1: Generate message instance
        generation_time = env.now
        instance = C2MessageInstance(
            entity_id=f"msg_{msg_type.name}_{uuid.uuid4().hex[:8]}",
            timestamp=generation_time,
            message_type=msg_type.name,
            generation_time=generation_time,
            transmission_start_time=generation_time,
            status="generated",
        )

        # Step 2: Check destination reachability
        if not self._is_reachable(msg_type.destination_node, msg_type):
            # Destination unreachable — publish failure event
            instance.status = "failed"
            self._message_log.append(instance)

            self._event_bus.publish(
                MessageDeliveryFailureEvent(
                    entity_id=f"failure_{instance.entity_id}",
                    timestamp=env.now,
                    source_module=self.module_type,
                    message_type=msg_type.name,
                    source=msg_type.source_node,
                    intended_destination=msg_type.destination_node,
                    generation_time=generation_time,
                )
            )
            return

        # Step 3: Apply transmission latency
        instance.status = "in_transit"
        if msg_type.transmission_latency > 0:
            yield env.timeout(msg_type.transmission_latency)

        # Step 4: Check cross-domain routing
        sanitization_latency = 0.0
        if self._gateway_config and msg_type.classification:
            # Route through cross-domain gateway
            sanitization_latency = self._gateway_config.sanitization_latency
            if sanitization_latency > 0:
                yield env.timeout(sanitization_latency)

        # Step 5: Deliver
        delivery_time = generation_time + msg_type.transmission_latency + sanitization_latency
        instance.delivery_time = delivery_time
        instance.status = "delivered"
        self._message_log.append(instance)

        # Publish delivery event for subscribers (e.g., personnel module)
        self._event_bus.publish(
            MessageDeliveredEvent(
                entity_id=f"delivered_{instance.entity_id}",
                timestamp=delivery_time,
                source_module=self.module_type,
                message_type=msg_type.name,
                source_node=msg_type.source_node,
                destination_node=msg_type.destination_node,
                delivery_time=delivery_time,
            )
        )
