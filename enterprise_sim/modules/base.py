"""DomainModule abstract base class.

Defines the contract that all domain modules must implement for lifecycle
management by the Simulation Engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Set

import simpy

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.mission import MissionConfiguration


class DomainModule(ABC):
    """Abstract base class that all domain modules must implement.

    Domain modules are pluggable components that encapsulate specific areas
    of domain logic (e.g., authorization, fiscal, aerodynamics). They follow
    a well-defined lifecycle managed by the Simulation Engine:

    1. initialize — set up internal state, subscribe to events
    2. create_processes — return SimPy processes for the execution phase
    3. finalize — clean up resources, flush state

    Subclasses must declare:
        module_type: A unique string identifier (e.g., "authorization")
        dependencies: A set of module_type identifiers this module depends on
    """

    # Class-level metadata — subclasses must override these
    module_type: ClassVar[str]
    """Unique identifier for this module type (e.g., 'authorization', 'fiscal')."""

    dependencies: ClassVar[Set[str]]
    """Set of module_type identifiers that this module depends on for initialization ordering."""

    @abstractmethod
    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Called during the initialization phase.

        Set up internal state, subscribe to events on the event bus, and
        store references needed during execution. Must not advance
        simulation time.

        Args:
            env: The SimPy simulation environment.
            event_bus: The publish-subscribe event bus for inter-module communication.
            config: The mission configuration driving this simulation run.
        """
        ...

    @abstractmethod
    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Return SimPy processes that this module runs during the execution phase.

        Each process is a generator function registered with env.process().
        Modules that are purely reactive (respond to events only) may return
        an empty list.

        Args:
            env: The SimPy simulation environment.

        Returns:
            A list of SimPy Process objects to be run during execution.
        """
        ...

    @abstractmethod
    def finalize(self) -> None:
        """Called during the finalization phase.

        Clean up resources, flush state, and perform any end-of-simulation
        bookkeeping. Must not advance simulation time.
        """
        ...

    def validate_config(self, config: MissionConfiguration) -> list[str]:
        """Validate the mission configuration for this module.

        Optional method that subclasses can override to perform module-specific
        configuration validation. Returns a list of error messages describing
        any validation issues found.

        Args:
            config: The mission configuration to validate.

        Returns:
            A list of validation error strings. Empty list means valid.
        """
        return []
