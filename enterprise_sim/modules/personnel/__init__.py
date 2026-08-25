"""Personnel domain module package.

Implements the PersonnelModule, a DomainModule that models individual personnel
in mission execution. Manages personnel roster, decision points, message routing,
credential checking, and readiness states.

Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.1, 2.4, 9.1, 9.2, 9.3
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Set

import simpy

from enterprise_sim.models.events import (
    ExtractionStartEvent,
    GateCompletionEvent,
    MessageDeliveredEvent,
    SimulationEvent,
)
from enterprise_sim.models.personnel import (
    AuthorizationGateBinding,
    ClearanceLevel,
    DecisionOutcome,
    DecisionPointConfig,
    DecisionType,
    MessageRoutingRule,
    Personnel,
    PersonnelConfig,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    CargoReleaseConfirmedEvent,
    CargoReleaseDeniedEvent,
    DecisionDeferredEvent,
    DecisionRenderedEvent,
    DecisionTimeoutEvent,
    GateBlockedEvent,
    GateTimeoutPersonnelEvent,
    MissionBlockedEvent,
    ReadinessChangedEvent,
)
from enterprise_sim.modules.base import DomainModule
from enterprise_sim.modules.personnel.credential_checker import CredentialChecker
from enterprise_sim.modules.personnel.decision_processor import DecisionPointProcessor
from enterprise_sim.modules.personnel.message_router import MessageRouter

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.mission import MissionConfiguration


class PersonnelModule(DomainModule):
    """Domain module modeling individual personnel in mission execution.

    Manages personnel roster lifecycle, decision point evaluation, message
    routing, credential-based access control, and readiness state tracking.
    Integrates with Authorization, C2 Message, Cross-Domain Gateway, and
    Airdrop modules through the synchronous EventBus.
    """

    module_type: ClassVar[str] = "personnel"
    dependencies: ClassVar[Set[str]] = {
        "authorization",
        "c2_message",
        "cross_domain",
        "airdrop",
    }

    def __init__(self) -> None:
        self._env: simpy.Environment | None = None
        self._event_bus: EventBus | None = None
        self._config: MissionConfiguration | None = None

        # Roster state
        self._roster: list[Personnel] = []
        self._roster_by_name: dict[str, Personnel] = {}

        # Internal components
        self._decision_processor: DecisionPointProcessor | None = None
        self._message_router: MessageRouter | None = None
        self._credential_checker: CredentialChecker | None = None

        # Configuration references
        self._routing_rules: list[MessageRoutingRule] = []
        self._gate_bindings: list[AuthorizationGateBinding] = []
        self._decision_points: list[DecisionPointConfig] = []

        # Gate approval tracking: gate_name -> approving personnel name
        self._gate_approvals: dict[str, str] = {}
        # Blocked gates awaiting READY personnel: gate_name -> required_role
        self._blocked_gates: dict[str, str] = {}

        # Loadmaster decision outcome override (None = default APPROVE)
        self._loadmaster_outcome: DecisionOutcome | None = None

    def validate_config(self, config: MissionConfiguration) -> list[str]:
        """Validate personnel configuration against other module configs.

        Validates:
        - Roster: unique names, size 1-100, valid roles, valid duty stations
        - Message routing rules: message types exist, roles exist in roster
        - Authorization gate bindings: gate names exist, roles exist in roster
        - Decision points: unique IDs, positive deliberation_duration, roles in roster
        - Credentials: authorized_enclaves exist in cross_domain_gateway config

        Returns aggregated list of all errors found (not fail-fast).
        """
        errors: list[str] = []

        # Check for personnel section presence
        personnel_config = getattr(config, "personnel_parameters", None)
        if personnel_config is None:
            errors.append(
                "Personnel configuration section is required when personnel module is active"
            )
            return errors

        # --- Roster validation ---
        roster = personnel_config.roster

        # Size validation (Pydantic handles min/max but we validate for clear error messages)
        if len(roster) == 0:
            errors.append("Personnel roster requires at least one entry")
        elif len(roster) > 100:
            errors.append("Personnel roster exceeds maximum of 100 entries")

        # Unique names validation
        names_seen: set[str] = set()
        for person in roster:
            if person.name in names_seen:
                errors.append(f"Duplicate personnel name: {person.name}")
            names_seen.add(person.name)

        # Build set of roles present in the roster for cross-reference
        roster_roles: set[str] = {p.role for p in roster}

        # Valid duty stations — check against route graph nodes
        route_graph_nodes: set[str] = set()
        if config.route_graph:
            route_graph_nodes = {node.name for node in config.route_graph.nodes}

        for person in roster:
            if route_graph_nodes and person.duty_station not in route_graph_nodes:
                errors.append(
                    f"Personnel '{person.name}' references unknown duty_station: "
                    f"{person.duty_station}"
                )

        # --- Message routing rules validation ---
        # Collect configured C2 message type names
        c2_message_types: set[str] = set()
        if config.c2_messages:
            c2_message_types = {msg.name for msg in config.c2_messages}

        for rule in personnel_config.message_routing_rules:
            if c2_message_types and rule.message_type not in c2_message_types:
                errors.append(
                    f"Routing rule references unknown message type: {rule.message_type}"
                )
            if rule.destination_role not in roster_roles:
                errors.append(
                    f"Routing rule references unknown role: {rule.destination_role}"
                )

        # --- Authorization gate binding validation ---
        # Collect configured authorization gate names
        auth_gate_names: set[str] = set()
        if config.authorization_chain:
            auth_gate_names = {
                gate.gate_name for gate in config.authorization_chain.gates
            }

        for binding in personnel_config.authorization_gate_bindings:
            if auth_gate_names and binding.gate_name not in auth_gate_names:
                errors.append(
                    f"Gate binding references unknown gate: {binding.gate_name}"
                )
            if binding.approving_role not in roster_roles:
                errors.append(
                    f"Gate binding references unknown role: {binding.approving_role}"
                )

        # --- Decision points validation ---
        decision_ids_seen: set[str] = set()
        for dp in personnel_config.decision_points:
            if dp.decision_id in decision_ids_seen:
                errors.append(f"Duplicate decision_id: {dp.decision_id}")
            decision_ids_seen.add(dp.decision_id)

            if dp.deliberation_duration <= 0:
                errors.append(
                    f"Decision '{dp.decision_id}' has invalid "
                    f"deliberation_duration: {dp.deliberation_duration}"
                )

            if dp.assigned_role not in roster_roles:
                errors.append(
                    f"Decision '{dp.decision_id}' references unknown role: "
                    f"{dp.assigned_role}"
                )

        # --- Credential validation ---
        # Collect enclave names from cross_domain_gateway config
        gateway_enclave_names: set[str] = set()
        if config.cross_domain_gateway:
            gateway_enclave_names = {
                enclave.name for enclave in config.cross_domain_gateway.enclaves
            }

        if gateway_enclave_names:
            for person in roster:
                for enclave in person.credential.authorized_enclaves:
                    if enclave not in gateway_enclave_names:
                        errors.append(
                            f"Personnel '{person.name}' references unknown enclave: "
                            f"{enclave}"
                        )

        return errors

    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Initialize personnel module state, components, and event subscriptions.

        Stores the roster, builds internal lookup structures, instantiates
        sub-components (DecisionPointProcessor, MessageRouter, CredentialChecker),
        and subscribes to relevant cross-module events.
        """
        self._env = env
        self._event_bus = event_bus
        self._config = config

        # Load personnel config
        personnel_config: PersonnelConfig | None = getattr(
            config, "personnel_parameters", None
        )
        if personnel_config is None:
            return

        # Store roster and build lookup structures
        self._roster = list(personnel_config.roster)
        self._roster_by_name = {p.name: p for p in self._roster}

        # Store configuration references
        self._routing_rules = list(personnel_config.message_routing_rules)
        self._gate_bindings = list(personnel_config.authorization_gate_bindings)
        self._decision_points = list(personnel_config.decision_points)

        # Instantiate internal components
        self._decision_processor = DecisionPointProcessor()
        self._decision_processor.register_decision_points(self._decision_points)

        self._message_router = MessageRouter()
        self._credential_checker = CredentialChecker()

        # Subscribe to cross-module events
        # GateCompletionEvent — for authorization gate activation handling
        event_bus.subscribe(
            GateCompletionEvent,
            self._handle_gate_activation,
            subscriber_id="personnel_gate",
        )

        # ExtractionStartEvent — for airdrop extraction ready handling
        event_bus.subscribe(
            ExtractionStartEvent,
            self._handle_extraction_ready,
            subscriber_id="personnel_extraction",
        )

        # MessageDeliveredEvent — for C2 message routing integration
        event_bus.subscribe(
            MessageDeliveredEvent,
            self._handle_message_delivered,
            subscriber_id="personnel_message",
        )

    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Create SimPy processes for active decision points and message delivery.

        Returns a list of SimPy processes:
        - One process per active decision point
        - One message queue delivery process
        """
        processes: list[simpy.events.Process] = []

        if self._decision_processor is None:
            return processes

        # Create processes for active decision points
        for dp in self._decision_points:
            proc = env.process(
                self._decision_processor.trigger_decision(
                    dp.decision_id, env, self._event_bus, self._roster
                )
            )
            processes.append(proc)

        # Note: message queue delivery is handled synchronously in
        # change_readiness() — no separate polling process needed.

        return processes

    def finalize(self) -> None:
        """Clear roster state, queues, and internal references."""
        self._roster = []
        self._roster_by_name = {}
        self._routing_rules = []
        self._gate_bindings = []
        self._decision_points = []
        self._gate_approvals = {}
        self._blocked_gates = {}

        if self._decision_processor is not None:
            self._decision_processor.clear()
            self._decision_processor = None

        if self._message_router is not None:
            self._message_router.clear()
            self._message_router = None

        self._credential_checker = None
        self._env = None
        self._event_bus = None
        self._config = None

    # --- Event handlers (placeholder implementations for later tasks) ---

    def _handle_gate_activation(self, event: SimulationEvent) -> None:
        """Handle authorization gate activation events.

        On gate completion from the Authorization module:
        1. Extract the gate_name from the event
        2. Look up the AuthorizationGateBinding for that gate_name
        3. Find a READY personnel member with the bound approving_role
        4. If found: record the approving personnel name in gate_approvals
        5. If not found: publish GateBlockedEvent with gate_name, required_role, timestamp
        6. Handle timeout by publishing GateTimeoutPersonnelEvent

        Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
        """
        # Extract gate_name from the GateCompletionEvent
        gate_name: str = getattr(event, "gate_name", "")
        if not gate_name:
            return

        # Look up the AuthorizationGateBinding for this gate
        binding: AuthorizationGateBinding | None = None
        for b in self._gate_bindings:
            if b.gate_name == gate_name:
                binding = b
                break

        # If no binding exists for this gate, nothing to do
        if binding is None:
            return

        required_role = binding.approving_role
        timestamp = getattr(event, "timestamp", 0.0)

        # Find a READY personnel member with the required role (roster order)
        approving_person: Personnel | None = None
        for person in self._roster:
            if person.role == required_role and person.readiness_state == ReadinessState.READY:
                approving_person = person
                break

        if approving_person is not None:
            # Record the approving personnel name for gate completion
            self._gate_approvals[gate_name] = approving_person.name
            # Remove from blocked gates if previously blocked
            self._blocked_gates.pop(gate_name, None)
        else:
            # No READY personnel with the required role — block the gate
            self._blocked_gates[gate_name] = required_role

            if self._event_bus is not None:
                self._event_bus.publish(
                    GateBlockedEvent(
                        entity_id=f"gate_blocked_{gate_name}_{timestamp}",
                        timestamp=timestamp,
                        gate_name=gate_name,
                        required_role=required_role,
                    )
                )

            # Check for gate timeout: look up the gate's timeout from auth chain config
            gate_timeout = self._get_gate_timeout(gate_name)
            if gate_timeout is not None:
                # Schedule a timeout check via SimPy process if env is available
                if self._env is not None:
                    self._env.process(
                        self._gate_timeout_process(
                            gate_name, required_role, gate_timeout
                        )
                    )

    def _get_gate_timeout(self, gate_name: str) -> float | None:
        """Look up the configured timeout for an authorization gate.

        Returns the timeout value if configured, None otherwise.
        """
        if self._config is None or self._config.authorization_chain is None:
            return None

        for gate in self._config.authorization_chain.gates:
            if gate.gate_name == gate_name:
                return gate.timeout

        return None

    def _gate_timeout_process(
        self, gate_name: str, required_role: str, timeout: float
    ):
        """SimPy process that waits for gate timeout while blocked.

        If the gate is still blocked when the timeout expires, publishes
        GateTimeoutPersonnelEvent and the gate transitions to failed.

        Requirements: 5.6
        """
        assert self._env is not None

        yield self._env.timeout(timeout)

        # Check if the gate is still blocked (hasn't been resolved by a readiness change)
        if gate_name in self._blocked_gates:
            # Gate timeout expired — publish timeout event
            self._blocked_gates.pop(gate_name, None)

            if self._event_bus is not None:
                self._event_bus.publish(
                    GateTimeoutPersonnelEvent(
                        entity_id=f"gate_timeout_personnel_{gate_name}_{self._env.now}",
                        timestamp=self._env.now,
                        gate_name=gate_name,
                        required_role=required_role,
                    )
                )

    def resolve_blocked_gates(self, personnel_name: str) -> None:
        """Check if any blocked gates can be resolved after a readiness change.

        Called when a personnel member transitions to READY state.
        Resolves any gates blocked on a role this personnel member has.

        Requirements: 5.4
        """
        person = self._roster_by_name.get(personnel_name)
        if person is None or person.readiness_state != ReadinessState.READY:
            return

        # Find blocked gates that require this person's role
        gates_to_resolve: list[str] = []
        for gate_name, required_role in self._blocked_gates.items():
            if person.role == required_role:
                gates_to_resolve.append(gate_name)

        for gate_name in gates_to_resolve:
            self._blocked_gates.pop(gate_name, None)
            self._gate_approvals[gate_name] = person.name

    def _handle_extraction_ready(self, event: SimulationEvent) -> None:
        """Handle airdrop extraction ready events.

        When the Airdrop module publishes an ExtractionStartEvent, this handler
        triggers a Loadmaster Decision_Point at the drop zone node. The handler
        spawns a SimPy process to handle the deliberation timing.

        - If APPROVE: publishes CargoReleaseConfirmedEvent, extraction proceeds
        - If DENY: publishes CargoReleaseDeniedEvent, extraction does NOT initiate
        - If no READY Loadmaster: publishes DecisionDeferredEvent, waits for
          READY or timeout; on timeout publishes DecisionTimeoutEvent

        Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
        """
        if self._env is None or self._event_bus is None:
            return

        # Extract the drop zone node from the event
        drop_zone_node: str = getattr(event, "drop_zone_id", "")
        if not drop_zone_node:
            return

        # Spawn a SimPy process to handle the loadmaster decision
        self._env.process(
            self._loadmaster_decision_process(drop_zone_node)
        )

    def _loadmaster_decision_process(self, drop_zone_node: str):
        """SimPy process for loadmaster cargo release decision.

        Resolves the Loadmaster at the drop zone, performs deliberation,
        and publishes the appropriate outcome events.

        Args:
            drop_zone_node: The drop zone node identifier from the extraction event.

        Yields:
            SimPy events for deliberation timing and timeout handling.
        """
        assert self._env is not None
        assert self._event_bus is not None

        # Find a decision point config for the Loadmaster role, or create a dynamic one
        loadmaster_dp_config: DecisionPointConfig | None = None
        for dp in self._decision_points:
            if dp.assigned_role == "Loadmaster":
                loadmaster_dp_config = dp
                break

        # If no configured decision point for Loadmaster, create a dynamic one
        if loadmaster_dp_config is None:
            loadmaster_dp_config = DecisionPointConfig(
                decision_id=f"loadmaster_cargo_release_{drop_zone_node}",
                assigned_role="Loadmaster",
                decision_type=DecisionType.APPROVE_DENY,
                deliberation_duration=1.0,
                timeout=10.0,
            )

        decision_id = loadmaster_dp_config.decision_id

        # Find READY Loadmaster at the drop zone duty station
        assigned: Personnel | None = None
        for person in self._roster:
            if (
                person.role == "Loadmaster"
                and person.readiness_state == ReadinessState.READY
                and person.duty_station == drop_zone_node
            ):
                assigned = person
                break

        if assigned is None:
            # No READY Loadmaster at drop zone — publish DecisionDeferredEvent
            self._event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{self._env.now}",
                    timestamp=self._env.now,
                    decision_id=decision_id,
                    assigned_role="Loadmaster",
                )
            )

            # Wait for timeout (if configured) for a READY Loadmaster to become available
            timeout_duration = loadmaster_dp_config.timeout
            if timeout_duration is not None:
                yield self._env.timeout(timeout_duration)

                # Re-check after timeout for READY Loadmaster at drop zone
                assigned = None
                for person in self._roster:
                    if (
                        person.role == "Loadmaster"
                        and person.readiness_state == ReadinessState.READY
                        and person.duty_station == drop_zone_node
                    ):
                        assigned = person
                        break

                if assigned is None:
                    # Timeout expired — publish DecisionTimeoutEvent, extraction does NOT initiate
                    self._event_bus.publish(
                        DecisionTimeoutEvent(
                            entity_id=f"decision_timeout_{decision_id}_{self._env.now}",
                            timestamp=self._env.now,
                            decision_id=decision_id,
                            assigned_role="Loadmaster",
                        )
                    )
                    return
            else:
                # No timeout configured, extraction blocked indefinitely (DEFER)
                return

        # We have a READY Loadmaster — track in-progress
        if self._decision_processor is not None:
            self._decision_processor.in_progress[decision_id] = assigned.name

        # Deliberation phase: consume deliberation_duration simulation time
        try:
            yield self._env.timeout(loadmaster_dp_config.deliberation_duration)
        except simpy.Interrupt:
            # Loadmaster became non-READY during deliberation
            if self._decision_processor is not None:
                self._decision_processor.in_progress.pop(decision_id, None)
            self._event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{self._env.now}",
                    timestamp=self._env.now,
                    decision_id=decision_id,
                    personnel_name=assigned.name,
                    readiness_state=assigned.readiness_state.value,
                )
            )
            return

        # Check if Loadmaster is still READY after deliberation
        if assigned.readiness_state != ReadinessState.READY:
            if self._decision_processor is not None:
                self._decision_processor.in_progress.pop(decision_id, None)
            self._event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{self._env.now}",
                    timestamp=self._env.now,
                    decision_id=decision_id,
                    personnel_name=assigned.name,
                    readiness_state=assigned.readiness_state.value,
                )
            )
            return

        # Clean up in-progress tracking
        if self._decision_processor is not None:
            self._decision_processor.in_progress.pop(decision_id, None)

        # Determine outcome — use override if set, otherwise default APPROVE
        # (Scenario-driven outcomes can set _loadmaster_outcome before triggering)
        outcome = self._loadmaster_outcome if self._loadmaster_outcome is not None else DecisionOutcome.APPROVE

        # Publish decision rendered event
        self._event_bus.publish(
            DecisionRenderedEvent(
                entity_id=f"decision_rendered_{decision_id}_{self._env.now}",
                timestamp=self._env.now,
                decision_id=decision_id,
                personnel_name=assigned.name,
                decision_type=loadmaster_dp_config.decision_type.value,
                outcome=outcome.value,
            )
        )

        # Publish outcome-specific event
        if outcome == DecisionOutcome.APPROVE:
            self._event_bus.publish(
                CargoReleaseConfirmedEvent(
                    entity_id=f"cargo_release_confirmed_{drop_zone_node}_{self._env.now}",
                    timestamp=self._env.now,
                    loadmaster_name=assigned.name,
                    drop_zone_node=drop_zone_node,
                )
            )
        elif outcome == DecisionOutcome.DENY:
            self._event_bus.publish(
                CargoReleaseDeniedEvent(
                    entity_id=f"cargo_release_denied_{drop_zone_node}_{self._env.now}",
                    timestamp=self._env.now,
                    loadmaster_name=assigned.name,
                    drop_zone_node=drop_zone_node,
                )
            )
            # Also publish mission-blocked event for DENY
            self._event_bus.publish(
                MissionBlockedEvent(
                    entity_id=f"mission_blocked_{decision_id}_{self._env.now}",
                    timestamp=self._env.now,
                    decision_id=decision_id,
                    blocking_personnel=assigned.name,
                    reason=f"Loadmaster {assigned.name} denied cargo release at {drop_zone_node}",
                )
            )

    def _handle_message_delivered(self, event: SimulationEvent) -> None:
        """Handle C2 message delivery events for personnel routing integration.

        On message delivery:
        1. Resolve sender personnel at source node via MessageRouter
        2. Route message to recipient at destination node via MessageRouter
        3. Store sender_personnel and receiver_personnel names on event
        4. Publish SenderUnresolvedEvent if sender cannot be resolved
        5. Publish ReceiverUnresolvedEvent if receiver cannot be resolved

        Requirements: 4.2, 4.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
        """
        if self._message_router is None or self._event_bus is None:
            return

        # Extract fields from the MessageDeliveredEvent
        message_type: str = getattr(event, "message_type", "")
        source_node: str = getattr(event, "source_node", "")
        destination_node: str = getattr(event, "destination_node", "")
        timestamp: float = event.timestamp

        # Resolve sender personnel (publishes SenderUnresolvedEvent if not found)
        sender = self._message_router.resolve_sender_with_event(
            message_type=message_type,
            source_node=source_node,
            roster=self._roster,
            routing_rules=self._routing_rules,
            event_bus=self._event_bus,
            timestamp=timestamp,
        )

        # Route message to recipient (handles all routing outcomes including
        # unrouted, unroutable, delayed, and successful delivery events)
        receiver = self._message_router.route_message(
            message_type=message_type,
            destination_node=destination_node,
            roster=self._roster,
            routing_rules=self._routing_rules,
            event_bus=self._event_bus,
            timestamp=timestamp,
        )

        # Record sender/receiver personnel names on the event for Reporter output
        sender_name = sender.name if sender is not None else None
        receiver_name = receiver.name if receiver is not None else None
        setattr(event, "sender_personnel", sender_name)
        setattr(event, "receiver_personnel", receiver_name)

    # --- Internal processes ---

    def _message_queue_process(self, env: simpy.Environment):
        """SimPy process for message queue delivery monitoring.

        Monitors readiness state changes and triggers delivery of queued
        messages when personnel transition to READY state.
        This is a long-running process that yields periodically.
        """
        # This process runs for the duration of the simulation.
        # In practice, message delivery is triggered by readiness changes
        # (handled in task 9.1). This process provides the SimPy scheduling
        # context for that delivery.
        while True:
            yield env.timeout(1.0)  # Periodic check interval

    # --- Public API ---

    def change_readiness(
        self, personnel_name: str, new_state: str | ReadinessState, timestamp: float
    ) -> str | None:
        """Change a personnel member's readiness state.

        Validates the new state, updates the personnel record, publishes a
        ReadinessChangedEvent, defers any in-progress decisions assigned to
        the personnel, and triggers queued message delivery on transition to READY.

        Args:
            personnel_name: The name of the personnel member to update.
            new_state: The target readiness state (string or ReadinessState enum).
            timestamp: The simulation timestamp of the state change.

        Returns:
            None on success, or an error string on failure.

        Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
        """
        # Validate new_state is a valid ReadinessState
        if isinstance(new_state, ReadinessState):
            validated_state = new_state
        else:
            try:
                validated_state = ReadinessState(new_state)
            except ValueError:
                return (
                    f"Invalid readiness state: {new_state}. "
                    f"Must be one of: {[s.value for s in ReadinessState]}"
                )

        # Look up personnel by name
        person = self._roster_by_name.get(personnel_name)
        if person is None:
            return f"Personnel not found: {personnel_name}"

        # Store previous state and update
        previous_state = person.readiness_state
        person.readiness_state = validated_state

        # Publish ReadinessChangedEvent
        if self._event_bus is not None:
            self._event_bus.publish(
                ReadinessChangedEvent(
                    entity_id=f"readiness_changed_{personnel_name}_{timestamp}",
                    timestamp=timestamp,
                    personnel_name=personnel_name,
                    previous_state=previous_state.value,
                    new_state=validated_state.value,
                )
            )

        # If personnel was assigned to an in-progress decision, defer it
        if self._decision_processor is not None:
            decisions_to_defer: list[str] = []
            for decision_id, assigned_name in self._decision_processor.in_progress.items():
                if assigned_name == personnel_name:
                    decisions_to_defer.append(decision_id)

            for decision_id in decisions_to_defer:
                # Remove from in-progress tracking
                self._decision_processor.in_progress.pop(decision_id, None)
                # Publish DecisionDeferredEvent
                if self._event_bus is not None:
                    self._event_bus.publish(
                        DecisionDeferredEvent(
                            entity_id=f"decision_deferred_{decision_id}_{timestamp}",
                            timestamp=timestamp,
                            decision_id=decision_id,
                            personnel_name=personnel_name,
                            readiness_state=validated_state.value,
                        )
                    )

        # If transitioning to READY, deliver queued messages
        if validated_state == ReadinessState.READY and self._message_router is not None:
            if self._event_bus is not None:
                self._message_router.deliver_queued_messages(
                    personnel_name=personnel_name,
                    event_bus=self._event_bus,
                    timestamp=timestamp,
                    roster=self._roster,
                )

        # If transitioning to READY, resolve any blocked authorization gates
        if validated_state == ReadinessState.READY:
            self.resolve_blocked_gates(personnel_name)

        return None

    @property
    def roster(self) -> list[Personnel]:
        """Access the personnel roster."""
        return self._roster

    @property
    def roster_by_name(self) -> dict[str, Personnel]:
        """Access the personnel roster indexed by name."""
        return self._roster_by_name

    @property
    def decision_processor(self) -> DecisionPointProcessor | None:
        """Access the decision point processor."""
        return self._decision_processor

    @property
    def message_router(self) -> MessageRouter | None:
        """Access the message router."""
        return self._message_router

    @property
    def credential_checker(self) -> CredentialChecker | None:
        """Access the credential checker."""
        return self._credential_checker

    @property
    def routing_rules(self) -> list[MessageRoutingRule]:
        """Access the configured message routing rules."""
        return self._routing_rules

    @property
    def gate_bindings(self) -> list[AuthorizationGateBinding]:
        """Access the configured authorization gate bindings."""
        return self._gate_bindings

    @property
    def gate_approvals(self) -> dict[str, str]:
        """Access the gate approval records (gate_name -> approving personnel name)."""
        return self._gate_approvals

    @property
    def blocked_gates(self) -> dict[str, str]:
        """Access currently blocked gates (gate_name -> required_role)."""
        return self._blocked_gates

    def set_loadmaster_outcome(self, outcome: DecisionOutcome) -> None:
        """Set the outcome for the next loadmaster cargo release decision.

        This allows scenario-driven control of whether the loadmaster
        approves or denies cargo release.

        Args:
            outcome: The decision outcome to use (APPROVE, DENY, or DEFER).
        """
        self._loadmaster_outcome = outcome

    def check_cross_domain_access(
        self, personnel_name: str, enclave_name: str, timestamp: float
    ) -> bool:
        """Check if a personnel member is authorized for cross-domain transit to an enclave.

        This method should be called before allowing a message to transit through
        the cross-domain gateway. It verifies:
        1. The destination enclave is in the personnel's authorized_enclaves list.
        2. The personnel's clearance_level meets or exceeds the enclave's classification.

        If access is denied (enclave not authorized or clearance insufficient), the
        message should be blocked and discarded. If access is granted, an
        AuthenticationSuccessEvent is published and transit is allowed.

        Args:
            personnel_name: The name of the personnel member initiating communication.
            enclave_name: The name of the destination enclave.
            timestamp: The current simulation timestamp.

        Returns:
            True if access is granted (transit allowed), False if denied (block/discard).

        Requirements: 6.2, 6.3, 6.4, 6.5
        """
        if self._credential_checker is None or self._event_bus is None:
            return False

        # Look up personnel by name
        person = self._roster_by_name.get(personnel_name)
        if person is None:
            return False

        # Determine the enclave's classification level from cross-domain gateway config
        enclave_classification = self._resolve_enclave_classification(enclave_name)

        # Delegate to CredentialChecker.authenticate() which handles:
        # - Enclave access check (authorized_enclaves)
        # - Clearance level check (clearance_level >= classification)
        # - Publishing AccessDeniedEvent, ClearanceInsufficientEvent, or AuthenticationSuccessEvent
        return self._credential_checker.authenticate(
            personnel=person,
            enclave_name=enclave_name,
            enclave_classification=enclave_classification,
            event_bus=self._event_bus,
            timestamp=timestamp,
        )

    def _resolve_enclave_classification(self, enclave_name: str) -> ClearanceLevel:
        """Resolve the classification level for a named enclave.

        Looks up the enclave in the cross-domain gateway configuration and
        converts its classification_level string to a ClearanceLevel enum.

        Args:
            enclave_name: The name of the enclave to look up.

        Returns:
            The ClearanceLevel corresponding to the enclave's classification.
            Defaults to TOP_SECRET if the enclave is not found or classification
            is unrecognized (fail-secure).
        """
        if self._config is None or self._config.cross_domain_gateway is None:
            return ClearanceLevel.TOP_SECRET

        for enclave in self._config.cross_domain_gateway.enclaves:
            if enclave.name == enclave_name:
                # Convert string classification to ClearanceLevel enum
                classification_str = enclave.classification_level.upper()
                try:
                    return ClearanceLevel[classification_str]
                except KeyError:
                    # Unrecognized classification — fail secure
                    return ClearanceLevel.TOP_SECRET

        # Enclave not found in config — fail secure
        return ClearanceLevel.TOP_SECRET
