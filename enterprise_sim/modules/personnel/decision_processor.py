"""Decision Point Processor for the Personnel Module.

Manages decision point evaluation as SimPy processes. Each decision point
is modeled as a SimPy process that consumes simulation time for deliberation,
resolves the assigned personnel by role and roster order, and publishes
appropriate events on completion, deferral, or timeout.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 2.3, 2.6
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import simpy

from enterprise_sim.models.personnel import (
    DecisionOutcome,
    DecisionPointConfig,
    Personnel,
    ReadinessState,
)
from enterprise_sim.models.personnel_events import (
    DecisionDeferredEvent,
    DecisionRenderedEvent,
    DecisionTimeoutEvent,
    MissionBlockedEvent,
)

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus


class DecisionPointProcessor:
    """Manages decision point evaluation as SimPy processes.

    Stores decision point configurations and tracks in-progress decisions.
    Resolves personnel assignment by role and roster order, respecting
    readiness state constraints.
    """

    def __init__(self) -> None:
        # decision_id -> DecisionPointConfig
        self._decision_configs: dict[str, DecisionPointConfig] = {}
        # decision_id -> personnel_name currently assigned (in-progress)
        self._in_progress: dict[str, str] = {}

    def register_decision_points(self, decision_points: list[DecisionPointConfig]) -> None:
        """Store decision point configurations for later triggering.

        Args:
            decision_points: List of decision point configurations to register.
        """
        for dp in decision_points:
            self._decision_configs[dp.decision_id] = dp

    @property
    def in_progress(self) -> dict[str, str]:
        """Return the mapping of in-progress decision_id -> personnel_name."""
        return self._in_progress

    def get_assigned_personnel(
        self, decision_id: str, roster: list[Personnel]
    ) -> Personnel | None:
        """Resolve the READY personnel member for a decision point's assigned role.

        Iterates through the roster in order and returns the first Personnel
        whose role matches the decision point's assigned_role and whose
        readiness_state is READY. Returns None if no match is found.

        Args:
            decision_id: The decision point identifier to resolve personnel for.
            roster: The personnel roster to search (in order).

        Returns:
            The first matching READY Personnel member, or None.
        """
        config = self._decision_configs.get(decision_id)
        if config is None:
            return None

        for person in roster:
            if (
                person.role == config.assigned_role
                and person.readiness_state == ReadinessState.READY
            ):
                return person
        return None

    def trigger_decision(
        self,
        decision_id: str,
        env: simpy.Environment,
        event_bus: EventBus,
        roster: list[Personnel],
    ):
        """Start a decision point evaluation as a SimPy process.

        This is a generator function intended to be used with env.process().
        It resolves the assigned personnel, handles deferral/timeout when
        no READY personnel is available, performs deliberation, and publishes
        the appropriate outcome events.

        Args:
            decision_id: The decision point identifier to trigger.
            env: The SimPy simulation environment.
            event_bus: The event bus for publishing events.
            roster: The personnel roster for resolving assignment.

        Yields:
            SimPy events for deliberation timing and timeout handling.
        """
        config = self._decision_configs.get(decision_id)
        if config is None:
            return

        # Try to resolve assigned personnel
        assigned = self.get_assigned_personnel(decision_id, roster)

        if assigned is None:
            # No READY personnel — publish deferred event
            event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    assigned_role=config.assigned_role,
                )
            )

            # Wait for readiness change or timeout
            if config.timeout is not None:
                # Wait up to timeout for a READY personnel to become available
                timeout_event = env.timeout(config.timeout)
                yield timeout_event

                # After timeout, check again
                assigned = self.get_assigned_personnel(decision_id, roster)
                if assigned is None:
                    # Timeout expired without READY personnel
                    event_bus.publish(
                        DecisionTimeoutEvent(
                            entity_id=f"decision_timeout_{decision_id}_{env.now}",
                            timestamp=env.now,
                            decision_id=decision_id,
                            assigned_role=config.assigned_role,
                        )
                    )
                    return
            else:
                # No timeout configured — outcome is DEFER with no further waiting
                # (In a full implementation with readiness change signals, we would
                # wait indefinitely. For now, without external signal mechanisms,
                # we timeout immediately as DEFER.)
                return

        # We have a READY personnel member — track in-progress
        self._in_progress[decision_id] = assigned.name

        # Deliberation phase: consume deliberation_duration simulation time
        try:
            yield env.timeout(config.deliberation_duration)
        except simpy.Interrupt:
            # Personnel became non-READY during deliberation
            self._in_progress.pop(decision_id, None)
            event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    personnel_name=assigned.name,
                    readiness_state=assigned.readiness_state.value,
                )
            )
            return

        # Deliberation complete — check if personnel is still READY
        if assigned.readiness_state != ReadinessState.READY:
            # Personnel became non-READY during deliberation
            self._in_progress.pop(decision_id, None)
            event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    personnel_name=assigned.name,
                    readiness_state=assigned.readiness_state.value,
                )
            )
            return

        # Decision rendered successfully — determine outcome based on decision_type
        # Default outcome is APPROVE for simulation purposes
        # (actual outcome determination would depend on scenario logic)
        outcome = DecisionOutcome.APPROVE

        self._in_progress.pop(decision_id, None)

        # Publish decision rendered event
        event_bus.publish(
            DecisionRenderedEvent(
                entity_id=f"decision_rendered_{decision_id}_{env.now}",
                timestamp=env.now,
                decision_id=decision_id,
                personnel_name=assigned.name,
                decision_type=config.decision_type.value,
                outcome=outcome.value,
            )
        )

        # If outcome is DENY, also publish mission-blocked event
        if outcome == DecisionOutcome.DENY:
            event_bus.publish(
                MissionBlockedEvent(
                    entity_id=f"mission_blocked_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    blocking_personnel=assigned.name,
                    reason=f"Decision {decision_id} denied by {assigned.name}",
                )
            )

    def trigger_decision_with_outcome(
        self,
        decision_id: str,
        env: simpy.Environment,
        event_bus: EventBus,
        roster: list[Personnel],
        outcome: DecisionOutcome,
    ):
        """Start a decision point evaluation with a predetermined outcome.

        Same as trigger_decision but allows specifying the outcome explicitly.
        Useful for scenario-driven simulations where the outcome is known.

        Args:
            decision_id: The decision point identifier to trigger.
            env: The SimPy simulation environment.
            event_bus: The event bus for publishing events.
            roster: The personnel roster for resolving assignment.
            outcome: The predetermined decision outcome.

        Yields:
            SimPy events for deliberation timing and timeout handling.
        """
        config = self._decision_configs.get(decision_id)
        if config is None:
            return

        # Try to resolve assigned personnel
        assigned = self.get_assigned_personnel(decision_id, roster)

        if assigned is None:
            # No READY personnel — publish deferred event
            event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    assigned_role=config.assigned_role,
                )
            )

            # Wait for readiness change or timeout
            if config.timeout is not None:
                timeout_event = env.timeout(config.timeout)
                yield timeout_event

                # After timeout, check again
                assigned = self.get_assigned_personnel(decision_id, roster)
                if assigned is None:
                    # Timeout expired without READY personnel
                    event_bus.publish(
                        DecisionTimeoutEvent(
                            entity_id=f"decision_timeout_{decision_id}_{env.now}",
                            timestamp=env.now,
                            decision_id=decision_id,
                            assigned_role=config.assigned_role,
                        )
                    )
                    return
            else:
                return

        # We have a READY personnel member — track in-progress
        self._in_progress[decision_id] = assigned.name

        # Deliberation phase
        try:
            yield env.timeout(config.deliberation_duration)
        except simpy.Interrupt:
            # Personnel became non-READY during deliberation
            self._in_progress.pop(decision_id, None)
            event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    personnel_name=assigned.name,
                    readiness_state=assigned.readiness_state.value,
                )
            )
            return

        # Check if personnel is still READY after deliberation
        if assigned.readiness_state != ReadinessState.READY:
            self._in_progress.pop(decision_id, None)
            event_bus.publish(
                DecisionDeferredEvent(
                    entity_id=f"decision_deferred_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    personnel_name=assigned.name,
                    readiness_state=assigned.readiness_state.value,
                )
            )
            return

        self._in_progress.pop(decision_id, None)

        # Publish decision rendered event
        event_bus.publish(
            DecisionRenderedEvent(
                entity_id=f"decision_rendered_{decision_id}_{env.now}",
                timestamp=env.now,
                decision_id=decision_id,
                personnel_name=assigned.name,
                decision_type=config.decision_type.value,
                outcome=outcome.value,
            )
        )

        # If outcome is DENY, also publish mission-blocked event
        if outcome == DecisionOutcome.DENY:
            event_bus.publish(
                MissionBlockedEvent(
                    entity_id=f"mission_blocked_{decision_id}_{env.now}",
                    timestamp=env.now,
                    decision_id=decision_id,
                    blocking_personnel=assigned.name,
                    reason=f"Decision {decision_id} denied by {assigned.name}",
                )
            )

    def clear(self) -> None:
        """Clear all stored decision configs and in-progress state."""
        self._decision_configs.clear()
        self._in_progress.clear()
