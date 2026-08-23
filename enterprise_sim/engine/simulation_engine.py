"""SimPy environment management and lifecycle orchestration.

The SimulationEngine orchestrates the full simulation lifecycle:
initialization → execution → finalization. It manages module ordering
based on declared dependencies and handles error recovery during
initialization and finalization.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.7
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import simpy

from enterprise_sim.engine.exceptions import SimulationError

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.engine.module_registry import ModuleRegistry
    from enterprise_sim.models.mission import MissionConfiguration

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Result of a simulation run."""

    success: bool = True
    error: str | None = None
    failed_module: str | None = None
    exception: Exception | None = None


class SimulationEngine:
    """Orchestrates the simulation lifecycle.

    The engine executes three sequential phases:
    1. Initialization — calls initialize on each module in topological order
    2. Execution — runs all SimPy processes returned by modules
    3. Finalization — calls finalize on each module in reverse init order

    Error handling:
    - If initialization fails, previously-initialized modules are finalized
      in reverse order.
    - If finalization raises, the error is logged and finalization continues
      for remaining modules.
    """

    def __init__(self, registry: ModuleRegistry, event_bus: EventBus) -> None:
        """Create a SimulationEngine.

        Args:
            registry: The module registry containing registered domain modules.
            event_bus: The event bus for inter-module communication.
        """
        self._registry = registry
        self._event_bus = event_bus

    def load_configuration(self, config: MissionConfiguration) -> list[str]:
        """Validate config against registry.

        Checks that all modules referenced in config.active_modules are
        present in the registry.

        Args:
            config: The mission configuration to validate.

        Returns:
            A list of error strings (empty means valid).
        """
        errors: list[str] = []
        for module_type in config.active_modules:
            if self._registry.get(module_type) is None:
                errors.append(f"Module '{module_type}' not found in registry")
        return errors

    def run(self, config: MissionConfiguration) -> SimulationResult:
        """Execute the full initialization → execution → finalization lifecycle.

        Modules are initialized in topological order of their declared
        dependencies. Execution runs all SimPy processes from all modules.
        Finalization occurs in reverse initialization order.

        Args:
            config: The mission configuration driving this simulation run.

        Returns:
            SimulationResult indicating success or failure with details.
        """
        # Create SimPy environment with clock starting at 0
        env = simpy.Environment()

        # Resolve initialization order (topological sort)
        ordered_modules = self._registry.resolve_initialization_order()

        # Filter to only active modules if specified
        if config.active_modules:
            active_set = set(config.active_modules)
            ordered_modules = [
                m for m in ordered_modules if m.module_type in active_set
            ]

        # Phase 1: Initialization
        initialized_modules: list[Any] = []
        try:
            for module in ordered_modules:
                module.initialize(env, self._event_bus, config)
                initialized_modules.append(module)
        except Exception as exc:
            failed_module_type = module.module_type
            logger.error(
                "Module '%s' failed during initialization: %s",
                failed_module_type,
                exc,
            )
            # Finalize previously-initialized modules in reverse order
            self._finalize_modules(list(reversed(initialized_modules)))
            return SimulationResult(
                success=False,
                error=f"Initialization failed in module '{failed_module_type}': {exc}",
                failed_module=failed_module_type,
                exception=exc,
            )

        # Post-initialization wiring: connect cross-module references
        self._wire_module_dependencies(initialized_modules)

        # Phase 2: Execution
        processes: list[simpy.events.Process] = []
        for module in initialized_modules:
            module_processes = module.create_processes(env)
            processes.extend(module_processes)

        if processes:
            env.run()

        # Phase 3: Finalization (reverse initialization order)
        self._finalize_modules(list(reversed(initialized_modules)))

        return SimulationResult(success=True)

    def _finalize_modules(self, modules: list[Any]) -> None:
        """Finalize modules in the given order, logging and continuing on errors.

        Args:
            modules: Modules to finalize, already in desired order.
        """
        for module in modules:
            try:
                module.finalize()
            except Exception as exc:
                logger.error(
                    "Module '%s' raised an exception during finalization: %s",
                    module.module_type,
                    exc,
                )

    def _wire_module_dependencies(self, modules: list[Any]) -> None:
        """Wire cross-module references after initialization.

        Handles inter-module dependencies that require direct references
        rather than purely event-bus communication:
        - C2MessageModule gets a reference to CrossDomainGatewayModule for
          latency queries during message routing (Requirement 6.5).

        Args:
            modules: All initialized modules.
        """
        # Build a lookup by module_type
        module_lookup: dict[str, Any] = {m.module_type: m for m in modules}

        # Wire C2MessageModule → CrossDomainGatewayModule
        c2_module = module_lookup.get("c2_message")
        gateway_module = module_lookup.get("cross_domain")
        if c2_module is not None and gateway_module is not None:
            if hasattr(c2_module, "set_gateway"):
                c2_module.set_gateway(gateway_module)
