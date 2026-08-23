"""AirdropModule implementation.

Manages the airdrop operation lifecycle, transitioning through phases
(en_route → slowdown → extraction → acceleration → en_route) while
publishing appropriate events. Enforces cargo weight limits and ensures
the aircraft occupies exactly one phase at any simulation time.

The module subscribes to FuelConsumedEvent from the Aerodynamics module
to detect when the aircraft reaches a drop zone node in the route graph,
triggering the airdrop state transition sequence.

Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Set

import simpy

from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase
from enterprise_sim.models.events import (
    ExtractionCompleteEvent,
    ExtractionStartEvent,
    FuelConsumedEvent,
    SimulationEvent,
    WeightExceededEvent,
)
from enterprise_sim.modules.base import DomainModule

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.mission import MissionConfiguration


class AirdropModule(DomainModule):
    """Airdrop domain module.

    Manages the airdrop operation as a state machine with four phases:
    EN_ROUTE → SLOWDOWN → EXTRACTION → ACCELERATION → EN_ROUTE.

    Before transitioning, validates that total cargo weight does not
    exceed the configured cargo weight limit. If exceeded, publishes a
    WeightExceededEvent and remains in EN_ROUTE.

    Publishes ExtractionStartEvent at the beginning of the extraction
    phase and ExtractionCompleteEvent at its end (with cargo manifest IDs).

    The airdrop is triggered when the aircraft reaches a drop zone node,
    detected by subscribing to FuelConsumedEvent and checking the leg
    destination against route graph nodes with node_type="drop_zone".
    """

    module_type: ClassVar[str] = "airdrop"
    dependencies: ClassVar[Set[str]] = {"aerodynamics"}

    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Store airdrop config, validate phase durations, set initial phase.

        Subscribes to FuelConsumedEvent to detect when the aircraft reaches
        a drop zone node in the route graph.

        Args:
            env: The SimPy simulation environment.
            event_bus: The event bus for publishing airdrop events.
            config: The mission configuration with airdrop parameters.

        Raises:
            ValueError: If any phase duration is not positive.
        """
        self._env = env
        self._event_bus = event_bus
        self._config = config

        # Store airdrop config (use from mission config or default)
        self._airdrop_config: AirdropConfig | None = config.airdrop_parameters

        if self._airdrop_config is not None:
            # Validate phase durations (all must be > 0)
            for phase, duration in self._airdrop_config.phase_durations.items():
                if duration <= 0:
                    raise ValueError(
                        f"Phase duration for '{phase.value}' must be positive, got {duration}"
                    )

        # Set initial phase
        self._current_phase: AirdropPhase = AirdropPhase.EN_ROUTE

        # Track whether airdrop has been triggered (one-shot)
        self._airdrop_triggered: bool = False

        # Identify drop zone nodes from route graph
        self._drop_zone_nodes: set[str] = set()
        if config.route_graph:
            for node in config.route_graph.nodes:
                if node.node_type == "drop_zone":
                    self._drop_zone_nodes.add(node.name)

        # Subscribe to FuelConsumedEvent to detect arrival at drop zone
        # The leg_id format is "source->destination"
        # Only subscribe if aerodynamics module is active (will produce events)
        self._wait_for_trigger = False
        if self._airdrop_config is not None and self._drop_zone_nodes:
            if "aerodynamics" in (config.active_modules or []):
                event_bus.subscribe(
                    FuelConsumedEvent,
                    self._handle_fuel_consumed,
                    subscriber_id="airdrop",
                )
                self._wait_for_trigger = True

        # SimPy event to trigger the airdrop sequence
        self._trigger_event: simpy.Event | None = None
        if self._airdrop_config is not None and self._wait_for_trigger:
            self._trigger_event = env.event()

    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Return SimPy process for the airdrop sequence.

        The airdrop waits for a trigger (drop zone arrival) before executing
        the state transition sequence. If no airdrop config or no drop zones
        are configured, runs the airdrop immediately as before (backward compat).

        Args:
            env: The SimPy simulation environment.

        Returns:
            A list containing one SimPy process for the airdrop sequence,
            or empty if no airdrop config.
        """
        if self._airdrop_config is None:
            return []
        return [env.process(self._airdrop_sequence(env))]

    def finalize(self) -> None:
        """Clean up internal state."""
        self._current_phase = AirdropPhase.EN_ROUTE
        self._airdrop_config = None
        self._airdrop_triggered = False
        self._drop_zone_nodes = set()
        self._trigger_event = None
        self._wait_for_trigger = False

    @property
    def current_phase(self) -> AirdropPhase:
        """Access the current airdrop phase."""
        return self._current_phase

    def _handle_fuel_consumed(self, event: SimulationEvent) -> None:
        """Handle FuelConsumedEvent to detect arrival at a drop zone.

        The leg_id format is "source->destination". If the destination
        is a drop zone node, trigger the airdrop sequence.

        Args:
            event: A FuelConsumedEvent with leg_id field.
        """
        if not isinstance(event, FuelConsumedEvent):
            return
        if self._airdrop_triggered:
            return

        # Parse leg_id to get destination node
        leg_id = event.leg_id
        if "->" in leg_id:
            _, destination = leg_id.split("->", 1)
            if destination in self._drop_zone_nodes:
                self._airdrop_triggered = True
                if self._trigger_event is not None and not self._trigger_event.triggered:
                    self._trigger_event.succeed()

    def _airdrop_sequence(self, env: simpy.Environment):
        """SimPy generator process for the airdrop operation.

        Waits for the aircraft to reach a drop zone (via trigger event),
        then:
        1. Check total cargo weight against limit
        2. If exceeded: publish WeightExceededEvent, remain in EN_ROUTE
        3. If valid: transition through SLOWDOWN → EXTRACTION → ACCELERATION → EN_ROUTE
        """
        assert self._airdrop_config is not None

        # Wait for trigger if drop zones are configured and aerodynamics is active
        if self._wait_for_trigger and self._trigger_event is not None:
            yield self._trigger_event
        # If no drop zones configured or no aerodynamics, start immediately (backward compat)

        # Compute total cargo weight
        total_cargo_weight = sum(
            item.weight for item in self._airdrop_config.cargo_manifest
        )
        cargo_weight_limit = self._airdrop_config.cargo_weight_limit

        # Weight check
        if total_cargo_weight > cargo_weight_limit:
            self._event_bus.publish(
                WeightExceededEvent(
                    entity_id=f"weight_exceeded_{env.now}",
                    timestamp=env.now,
                    source_module="airdrop",
                    cargo_weight=total_cargo_weight,
                    weight_limit=cargo_weight_limit,
                )
            )
            # Remain in EN_ROUTE, do not proceed
            return

        # Phase transitions with configured durations
        phase_durations = self._airdrop_config.phase_durations

        # Determine drop zone ID from the triggered leg destination
        drop_zone_id = "drop_zone_1"  # Default
        if self._drop_zone_nodes:
            drop_zone_id = next(iter(self._drop_zone_nodes))

        # SLOWDOWN phase
        self._current_phase = AirdropPhase.SLOWDOWN
        yield env.timeout(phase_durations[AirdropPhase.SLOWDOWN])

        # EXTRACTION phase
        self._current_phase = AirdropPhase.EXTRACTION
        # Publish ExtractionStartEvent at extraction phase start
        self._event_bus.publish(
            ExtractionStartEvent(
                entity_id=f"extraction_start_{env.now}",
                timestamp=env.now,
                source_module="airdrop",
                drop_zone_id=drop_zone_id,
            )
        )

        yield env.timeout(phase_durations[AirdropPhase.EXTRACTION])

        # Publish ExtractionCompleteEvent at extraction phase end
        cargo_manifest_ids = [
            item.item_id for item in self._airdrop_config.cargo_manifest
        ]
        self._event_bus.publish(
            ExtractionCompleteEvent(
                entity_id=f"extraction_complete_{env.now}",
                timestamp=env.now,
                source_module="airdrop",
                drop_zone_id=drop_zone_id,
                cargo_manifest=cargo_manifest_ids,
            )
        )

        # ACCELERATION phase
        self._current_phase = AirdropPhase.ACCELERATION
        yield env.timeout(phase_durations[AirdropPhase.ACCELERATION])

        # Return to EN_ROUTE
        self._current_phase = AirdropPhase.EN_ROUTE
