"""Module discovery, validation, and storage.

Provides the ModuleRegistry class that validates Domain Module implementations
against the required interface, stores them by type, and resolves initialization
order via topological sort of declared dependencies.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from enterprise_sim.engine.exceptions import CycleError, RegistrationError

if TYPE_CHECKING:
    from enterprise_sim.modules.base import DomainModule


# Required methods that every DomainModule must implement as callable attributes
_REQUIRED_METHODS = ("initialize", "create_processes", "finalize")


class ModuleRegistry:
    """Validates and stores Domain Module implementations.

    The registry ensures that:
    - Every registered module implements the required DomainModule interface
    - No two modules share the same module_type identifier
    - Module initialization order can be resolved via topological sort
    """

    def __init__(self) -> None:
        self._modules: dict[str, DomainModule] = {}

    def register(self, module: DomainModule) -> None:
        """Register a domain module after validating its interface.

        Validates that the module has callable `initialize`, `create_processes`,
        and `finalize` methods. Rejects registration if the module's
        `module_type` is already registered.

        Args:
            module: The domain module instance to register.

        Raises:
            RegistrationError: If the module is missing required methods or
                if a module with the same module_type is already registered.
        """
        # Determine module name for error reporting
        module_name = getattr(module, "module_type", None) or type(module).__name__

        # Validate required interface methods
        missing_methods: list[str] = []
        for method_name in _REQUIRED_METHODS:
            attr = getattr(module, method_name, None)
            if attr is None or not callable(attr):
                missing_methods.append(method_name)

        if missing_methods:
            raise RegistrationError(
                module_name=module_name,
                missing_methods=missing_methods,
            )

        # Check for duplicate module_type
        module_type = getattr(module, "module_type", None)
        if module_type is None:
            raise RegistrationError(
                module_name=type(module).__name__,
                missing_methods=["module_type (class variable)"],
            )

        if module_type in self._modules:
            raise RegistrationError(
                message=(
                    f"Module type '{module_type}' is already registered. "
                    f"Cannot register duplicate module type."
                ),
                module_name=module_name,
                missing_methods=[],
            )

        self._modules[module_type] = module

    def get(self, module_type: str) -> DomainModule | None:
        """Retrieve a module by its type identifier.

        Args:
            module_type: The unique module type string (e.g., "authorization").

        Returns:
            The registered DomainModule instance, or None if not found.
        """
        return self._modules.get(module_type)

    def get_all(self) -> list[DomainModule]:
        """Return all registered modules.

        Returns:
            A list of all DomainModule instances currently in the registry.
        """
        return list(self._modules.values())

    def resolve_initialization_order(self) -> list[DomainModule]:
        """Resolve module initialization order via topological sort.

        Uses Kahn's algorithm to produce a topological ordering of modules
        based on their declared dependencies. Modules with no dependencies
        are initialized first, followed by modules whose dependencies have
        all been initialized.

        Returns:
            A list of DomainModule instances in valid initialization order.

        Raises:
            CycleError: If the dependency graph contains a cycle, with
                the cycle_modules attribute listing the involved module types.
        """
        if not self._modules:
            return []

        # Build adjacency list and compute in-degrees
        # An edge from A -> B means "B depends on A" (A must init before B)
        # Equivalently: if module B declares A in its dependencies,
        # then there's an edge A -> B in the graph.
        in_degree: dict[str, int] = {mt: 0 for mt in self._modules}
        adjacency: dict[str, list[str]] = {mt: [] for mt in self._modules}

        for module_type, module in self._modules.items():
            deps = getattr(module, "dependencies", set()) or set()
            for dep in deps:
                # Only consider dependencies that are actually registered
                if dep in self._modules:
                    adjacency[dep].append(module_type)
                    in_degree[module_type] += 1

        # Kahn's algorithm: start with zero in-degree nodes
        queue: deque[str] = deque()
        for mt, degree in in_degree.items():
            if degree == 0:
                queue.append(mt)

        sorted_order: list[str] = []

        while queue:
            current = queue.popleft()
            sorted_order.append(current)

            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If not all modules were processed, there's a cycle
        if len(sorted_order) != len(self._modules):
            cycle_modules = [
                mt for mt in self._modules if mt not in set(sorted_order)
            ]
            raise CycleError(cycle_modules=cycle_modules)

        return [self._modules[mt] for mt in sorted_order]
