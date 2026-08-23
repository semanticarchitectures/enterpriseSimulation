"""Custom exception hierarchy for the enterprise simulation engine.

Provides structured exceptions with programmatic access to error details
for registration failures, configuration issues, dependency cycles, and
graph validation problems.
"""

from typing import List, Optional


class SimulationError(Exception):
    """Base exception for all simulation engine errors.

    All custom exceptions in the simulation framework inherit from this class,
    allowing callers to catch any simulation-related error with a single handler.
    """

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


class ConfigurationError(SimulationError):
    """Raised when a mission configuration fails validation.

    Attributes:
        invalid_fields: List of field names that failed validation.
        violations: List of constraint violation descriptions for each invalid field.
    """

    def __init__(
        self,
        message: str = "",
        invalid_fields: Optional[List[str]] = None,
        violations: Optional[List[str]] = None,
    ) -> None:
        self.invalid_fields = invalid_fields or []
        self.violations = violations or []
        if not message and self.invalid_fields:
            message = (
                f"Configuration validation failed for fields: "
                f"{', '.join(self.invalid_fields)}"
            )
        super().__init__(message)


class RegistrationError(SimulationError):
    """Raised when a domain module fails registration validation.

    Attributes:
        module_name: The name/type of the module that failed registration.
        missing_methods: List of required interface methods that are missing
            or non-compliant on the module.
    """

    def __init__(
        self,
        message: str = "",
        module_name: str = "",
        missing_methods: Optional[List[str]] = None,
    ) -> None:
        self.module_name = module_name
        self.missing_methods = missing_methods or []
        if not message and self.module_name:
            message = (
                f"Module '{self.module_name}' failed registration: "
                f"missing methods: {', '.join(self.missing_methods)}"
            )
        super().__init__(message)


class CycleError(SimulationError):
    """Raised when module dependencies contain a circular dependency.

    Attributes:
        cycle_modules: List of module type identifiers involved in the
            dependency cycle.
    """

    def __init__(
        self,
        message: str = "",
        cycle_modules: Optional[List[str]] = None,
    ) -> None:
        self.cycle_modules = cycle_modules or []
        if not message and self.cycle_modules:
            message = (
                f"Circular dependency detected among modules: "
                f"{' -> '.join(self.cycle_modules)}"
            )
        super().__init__(message)


class GraphValidationError(SimulationError):
    """Raised when a route graph fails structural validation.

    Attributes:
        node_name: The name of the node that caused the validation failure.
        reason: A description of why validation failed for this node.
    """

    def __init__(
        self,
        message: str = "",
        node_name: str = "",
        reason: str = "",
    ) -> None:
        self.node_name = node_name
        self.reason = reason
        if not message and self.node_name:
            message = f"Graph validation failed at node '{self.node_name}': {self.reason}"
        super().__init__(message)
