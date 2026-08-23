"""AerodynamicsModule implementation.

Computes fuel-burn across flight legs using a linearized Breguet range equation.
Publishes FuelConsumedEvent on successful leg completion and FuelInsufficientEvent
when a leg cannot be flown due to lack of fuel. Optionally debits the fiscal
module for fuel costs.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, ClassVar, Set

import simpy

from enterprise_sim.engine.exceptions import ConfigurationError
from enterprise_sim.models.events import FuelConsumedEvent, FuelInsufficientEvent
from enterprise_sim.modules.base import DomainModule

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.mission import MissionConfiguration


class AerodynamicsModule(DomainModule):
    """Aerodynamics domain module.

    Computes fuel consumption for each flight leg in the route graph using
    a linearized approximation of the Breguet range equation:

        fuel_consumed = distance_nm * specific_fuel_consumption / lift_to_drag_ratio

    Maintains cumulative fuel consumed across all legs, detects fuel
    insufficiency before each leg, and publishes appropriate events.
    """

    module_type: ClassVar[str] = "aerodynamics"
    dependencies: ClassVar[Set[str]] = {"fiscal"}

    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Validate aircraft parameters, store config, initialize fuel tracking.

        Args:
            env: The SimPy simulation environment.
            event_bus: The event bus for publishing fuel events.
            config: The mission configuration with aircraft and route data.

        Raises:
            ConfigurationError: If aircraft parameters are invalid (non-positive).
        """
        self._env = env
        self._event_bus = event_bus
        self._config = config

        # Validate aircraft parameters (all must be positive)
        aircraft = config.aircraft
        invalid_params: list[str] = []
        if aircraft.lift_to_drag_ratio <= 0:
            invalid_params.append("lift_to_drag_ratio")
        if aircraft.specific_fuel_consumption <= 0:
            invalid_params.append("specific_fuel_consumption")
        if aircraft.initial_fuel_weight <= 0:
            invalid_params.append("initial_fuel_weight")
        if aircraft.max_fuel_capacity <= 0:
            invalid_params.append("max_fuel_capacity")

        if invalid_params:
            raise ConfigurationError(
                invalid_fields=invalid_params,
                violations=[
                    f"Aircraft parameter '{p}' must be positive"
                    for p in invalid_params
                ],
            )

        # Store aircraft parameters
        self._lift_to_drag_ratio = aircraft.lift_to_drag_ratio
        self._specific_fuel_consumption = aircraft.specific_fuel_consumption
        self._initial_fuel_weight = aircraft.initial_fuel_weight

        # Initialize cumulative fuel tracking
        self._cumulative_fuel_consumed: float = 0.0

        # Compute the ordered leg sequence from origin to destination
        self._legs = self._compute_leg_sequence()

    def compute_fuel_burn(self, distance_nm: float) -> float:
        """Compute fuel burn for a given distance using linearized Breguet equation.

        fuel_consumed = distance_nm * specific_fuel_consumption / lift_to_drag_ratio

        Args:
            distance_nm: The leg distance in nautical miles.

        Returns:
            The fuel consumed in weight units.
        """
        return distance_nm * self._specific_fuel_consumption / self._lift_to_drag_ratio

    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Return SimPy processes for each leg in the route graph.

        Creates a single process that iterates through the legs sequentially.

        Args:
            env: The SimPy simulation environment.

        Returns:
            A list containing one SimPy process for the flight sequence.
        """
        if not self._legs:
            return []
        return [env.process(self._fly_route(env))]

    def finalize(self) -> None:
        """Clean up internal state."""
        self._legs = []
        self._cumulative_fuel_consumed = 0.0

    def _fly_route(self, env: simpy.Environment):
        """SimPy generator process that flies each leg sequentially.

        For each leg:
        1. Compute fuel burn
        2. Check remaining fuel sufficiency
        3. If insufficient: publish FuelInsufficientEvent, stop
        4. If sufficient: simulate flight time, update cumulative fuel,
           publish FuelConsumedEvent, debit fiscal account
        """
        for leg_source, leg_destination, distance_nm in self._legs:
            leg_id = f"{leg_source}->{leg_destination}"

            # Compute fuel burn for this leg
            fuel_burn = self.compute_fuel_burn(distance_nm)

            # Check remaining fuel
            remaining_fuel = self._initial_fuel_weight - self._cumulative_fuel_consumed

            if fuel_burn > remaining_fuel:
                # Insufficient fuel — publish event and stop flying
                self._event_bus.publish(
                    FuelInsufficientEvent(
                        entity_id=f"fuel_insufficient_{leg_id}_{env.now}",
                        timestamp=env.now,
                        source_module="aerodynamics",
                        leg_id=leg_id,
                        required_fuel=fuel_burn,
                        available_fuel=remaining_fuel,
                    )
                )
                # Do NOT proceed with this or subsequent legs
                return

            # Simulate flight time (use distance as a proxy for time in sim units)
            flight_time = distance_nm
            yield env.timeout(flight_time)

            # Update cumulative fuel consumed
            self._cumulative_fuel_consumed += fuel_burn

            # Publish fuel consumed event
            self._event_bus.publish(
                FuelConsumedEvent(
                    entity_id=f"fuel_consumed_{leg_id}_{env.now}",
                    timestamp=env.now,
                    source_module="aerodynamics",
                    leg_id=leg_id,
                    fuel_quantity=fuel_burn,
                )
            )

            # Fuel cost debiting is handled by the Fiscal module's subscription
            # to FuelConsumedEvent via the Event Bus (Requirement 8.1, 6.5)

    def _compute_leg_sequence(self) -> list[tuple[str, str, float]]:
        """Compute ordered sequence of legs from origin to destination.

        Uses BFS to find the shortest path (by hop count) from origin to
        destination in the route graph.

        Returns:
            A list of (source, destination, distance_nm) tuples representing
            the flight legs in order.
        """
        route_graph = self._config.route_graph
        origin = route_graph.origin
        destination = route_graph.destination

        # Build adjacency list with edge data
        adjacency: dict[str, list[tuple[str, float]]] = {}
        for node in route_graph.nodes:
            adjacency[node.name] = []
        for edge in route_graph.edges:
            adjacency[edge.source].append((edge.destination, edge.distance_nm))

        # BFS to find shortest path from origin to destination
        visited: set[str] = {origin}
        queue: deque[tuple[str, list[str]]] = deque([(origin, [origin])])
        path: list[str] | None = None

        while queue:
            current, current_path = queue.popleft()
            if current == destination:
                path = current_path
                break
            for neighbor, _ in adjacency.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, current_path + [neighbor]))

        if path is None:
            return []

        # Convert path to leg sequence with distances
        legs: list[tuple[str, str, float]] = []
        edge_lookup: dict[tuple[str, str], float] = {}
        for edge in route_graph.edges:
            edge_lookup[(edge.source, edge.destination)] = edge.distance_nm

        for i in range(len(path) - 1):
            source = path[i]
            dest = path[i + 1]
            distance = edge_lookup.get((source, dest), 0.0)
            legs.append((source, dest, distance))

        return legs

    @property
    def cumulative_fuel_consumed(self) -> float:
        """Access the cumulative fuel consumed across all completed legs."""
        return self._cumulative_fuel_consumed

    @property
    def remaining_fuel(self) -> float:
        """Access the remaining fuel (initial - cumulative consumed)."""
        return self._initial_fuel_weight - self._cumulative_fuel_consumed
