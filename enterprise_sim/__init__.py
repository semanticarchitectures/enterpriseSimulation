"""Enterprise Simulation - Modular discrete-event enterprise simulation framework.

Public API exports and convenience function for running simulations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

__version__ = "0.1.0"

# Core engine components
from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.engine.module_registry import ModuleRegistry
from enterprise_sim.engine.simulation_engine import SimulationEngine, SimulationResult

# Reporting
from enterprise_sim.reporting.reporter import Reporter

# Mission configuration model
from enterprise_sim.models.mission import MissionConfiguration

# Domain modules
from enterprise_sim.modules.authorization import AuthorizationModule
from enterprise_sim.modules.fiscal import FiscalModule
from enterprise_sim.modules.c2_message import C2MessageModule
from enterprise_sim.modules.cross_domain import CrossDomainGatewayModule
from enterprise_sim.modules.aerodynamics import AerodynamicsModule
from enterprise_sim.modules.airdrop import AirdropModule
from enterprise_sim.modules.personnel import PersonnelModule

# Configuration loader
from enterprise_sim.config.loader import load as load_configuration


__all__ = [
    # Core engine
    "SimulationEngine",
    "ModuleRegistry",
    "EventBus",
    "SimulationResult",
    # Reporting
    "Reporter",
    # Configuration
    "MissionConfiguration",
    "load_configuration",
    # Domain modules
    "AuthorizationModule",
    "FiscalModule",
    "C2MessageModule",
    "CrossDomainGatewayModule",
    "AerodynamicsModule",
    "AirdropModule",
    "PersonnelModule",
    # Convenience function
    "run_simulation",
]


def run_simulation(config_path: Union[str, Path]) -> SimulationResult:
    """Run a full simulation from a configuration file.

    Convenience function that wires together all components:
    1. Loads configuration from file (YAML or JSON, auto-detected by extension)
    2. Creates EventBus, ModuleRegistry, Reporter
    3. Registers all available domain modules
    4. Creates SimulationEngine
    5. Calls engine.run(config) and returns the result

    Args:
        config_path: Path to a mission configuration file (.yaml, .yml, or .json).

    Returns:
        SimulationResult indicating success or failure with details.

    Raises:
        ConfigurationError: If the configuration file cannot be loaded or validated.
        CycleError: If domain module dependencies contain a cycle.
        RegistrationError: If a module fails interface validation during registration.
    """
    # 1. Load configuration from file
    config = load_configuration(config_path)

    # 2. Create core infrastructure
    event_bus = EventBus()
    registry = ModuleRegistry()
    reporter = Reporter(event_bus)  # noqa: F841 — reporter subscribes to event_bus

    # 3. Register all available domain modules
    _register_all_modules(registry)

    # 4. Create simulation engine
    engine = SimulationEngine(registry, event_bus)

    # 5. Run simulation and return result
    return engine.run(config)


def _register_all_modules(registry: ModuleRegistry) -> None:
    """Register all available domain modules with the registry.

    Args:
        registry: The module registry to populate.
    """
    registry.register(AuthorizationModule())
    registry.register(FiscalModule())
    registry.register(C2MessageModule())
    registry.register(CrossDomainGatewayModule())
    registry.register(AerodynamicsModule())
    registry.register(AirdropModule())
    registry.register(PersonnelModule())
