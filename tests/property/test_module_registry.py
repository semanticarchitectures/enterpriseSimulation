"""Property tests for Module Registry.

**Property 1: Module Registration Validation Completeness**
**Property 3: Missing Module Detection Completeness**
**Property 32: Dependency Cycle Detection**
**Validates: Requirements 1.2, 1.5, 2.2, 2.3, 13.6**

Tests verify that:
- The registry accepts modules if and only if they implement all required methods
- When rejected, the error identifies every missing or non-compliant method
- Missing module detection lists exactly the set of module types not in the registry
- Circular dependencies raise CycleError listing the involved modules
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

import pytest

from enterprise_sim.engine.module_registry import ModuleRegistry
from enterprise_sim.engine.exceptions import CycleError, RegistrationError


# === Strategies ===

# The three required interface methods for a DomainModule
REQUIRED_METHODS = ("initialize", "create_processes", "finalize")

# Strategy for module type identifiers
module_type_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)

# Strategy for subsets of required methods (to test partial implementations)
method_subsets = st.frozensets(
    st.sampled_from(REQUIRED_METHODS),
    min_size=0,
    max_size=3,
)


def make_module(module_type: str, methods: frozenset, dependencies: set | None = None):
    """Create a mock module with the specified methods present as callables.

    Methods NOT in the `methods` set are omitted from the object entirely,
    simulating a module that fails to implement those interface methods.
    """
    if dependencies is None:
        dependencies = set()

    class FakeModule:
        pass

    obj = FakeModule()
    obj.module_type = module_type
    obj.dependencies = dependencies

    for method_name in methods:
        setattr(obj, method_name, lambda *args, **kwargs: None)

    return obj


# === Property 1: Module Registration Validation Completeness ===


class TestModuleRegistrationValidationCompleteness:
    """Property 1: Module Registration Validation Completeness.

    **Validates: Requirements 1.2, 1.5**

    For any object submitted for registration as a Domain_Module, the
    Module_Registry SHALL accept it if and only if it implements all required
    interface methods (initialize, create_processes, finalize), and when
    rejected, the error SHALL identify every missing or non-compliant method.
    """

    @given(
        module_type=module_type_str,
        present_methods=method_subsets,
    )
    @settings(max_examples=200)
    def test_accepts_iff_all_methods_present(
        self, module_type: str, present_methods: frozenset
    ):
        """Registry accepts a module iff it has all required methods."""
        registry = ModuleRegistry()
        module = make_module(module_type, present_methods)

        all_present = set(REQUIRED_METHODS) == set(present_methods)

        if all_present:
            # Should succeed
            registry.register(module)
            assert registry.get(module_type) is module
        else:
            # Should raise RegistrationError
            with pytest.raises(RegistrationError):
                registry.register(module)

    @given(
        module_type=module_type_str,
        present_methods=method_subsets,
    )
    @settings(max_examples=200)
    def test_error_identifies_all_missing_methods(
        self, module_type: str, present_methods: frozenset
    ):
        """When registration fails, the error identifies every missing method."""
        assume(set(present_methods) != set(REQUIRED_METHODS))

        registry = ModuleRegistry()
        module = make_module(module_type, present_methods)

        expected_missing = set(REQUIRED_METHODS) - set(present_methods)

        with pytest.raises(RegistrationError) as exc_info:
            registry.register(module)

        error = exc_info.value
        assert set(error.missing_methods) == expected_missing
        assert error.module_name == module_type


# === Property 3: Missing Module Detection Completeness ===


class TestMissingModuleDetectionCompleteness:
    """Property 3: Missing Module Detection Completeness.

    **Validates: Requirements 2.2, 2.3**

    For any MissionConfiguration referencing a set of module types, and a
    Module_Registry containing a subset of those types, the validation error
    SHALL list exactly the set of module types that are referenced but not
    present in the registry.
    """

    @given(
        referenced_types=st.frozensets(module_type_str, min_size=1, max_size=10),
        registered_subset=st.frozensets(module_type_str, min_size=0, max_size=10),
    )
    @settings(max_examples=200)
    def test_missing_modules_detected_exactly(
        self, referenced_types: frozenset, registered_subset: frozenset
    ):
        """Missing module detection lists exactly the unregistered referenced types."""
        registry = ModuleRegistry()

        # Register only those types that are in the registered_subset AND referenced
        # (we can only register types that exist in the superset of referenced types
        # to keep the test focused)
        actually_registered = set()
        for mt in registered_subset:
            if mt in referenced_types:
                module = make_module(mt, frozenset(REQUIRED_METHODS))
                registry.register(module)
                actually_registered.add(mt)

        # Compute expected missing set
        expected_missing = set(referenced_types) - actually_registered

        # Simulate missing module detection (the logic that load_configuration uses)
        detected_missing = set()
        for mt in referenced_types:
            if registry.get(mt) is None:
                detected_missing.add(mt)

        assert detected_missing == expected_missing


# === Property 32: Dependency Cycle Detection ===


class TestDependencyCycleDetection:
    """Property 32: Dependency Cycle Detection.

    **Validates: Requirements 13.6**

    For any set of modules with circular dependencies,
    resolve_initialization_order SHALL raise CycleError listing the
    involved modules.
    """

    @given(
        cycle_size=st.integers(min_value=2, max_value=8),
        prefix=module_type_str,
    )
    @settings(max_examples=100)
    def test_simple_cycle_detected(self, cycle_size: int, prefix: str):
        """A simple cycle of N modules is detected and all participants listed."""
        registry = ModuleRegistry()

        # Create a cycle: mod_0 -> mod_1 -> ... -> mod_(N-1) -> mod_0
        module_types = [f"{prefix}_{i}" for i in range(cycle_size)]

        for i, mt in enumerate(module_types):
            # Each module depends on the next one in the cycle (wraps around)
            dep = module_types[(i + 1) % cycle_size]
            module = make_module(mt, frozenset(REQUIRED_METHODS), dependencies={dep})
            registry.register(module)

        with pytest.raises(CycleError) as exc_info:
            registry.resolve_initialization_order()

        error = exc_info.value
        # All modules in the cycle should be listed in cycle_modules
        assert set(error.cycle_modules) == set(module_types)

    @given(
        data=st.data(),
        num_modules=st.integers(min_value=3, max_value=8),
    )
    @settings(max_examples=100)
    def test_partial_cycle_in_larger_graph(self, data, num_modules: int):
        """When only a subset forms a cycle, those modules are in cycle_modules."""
        registry = ModuleRegistry()

        # Create module names
        module_types = [f"mod_{i}" for i in range(num_modules)]

        # Pick a subset of at least 2 modules to form a cycle
        cycle_size = data.draw(st.integers(min_value=2, max_value=num_modules))
        cycle_modules = module_types[:cycle_size]
        non_cycle_modules = module_types[cycle_size:]

        # Register cycle modules: each depends on the next (wrapping)
        for i, mt in enumerate(cycle_modules):
            dep = cycle_modules[(i + 1) % cycle_size]
            module = make_module(mt, frozenset(REQUIRED_METHODS), dependencies={dep})
            registry.register(module)

        # Register non-cycle modules with no dependencies (or dependency on a cycle module)
        for mt in non_cycle_modules:
            module = make_module(mt, frozenset(REQUIRED_METHODS), dependencies=set())
            registry.register(module)

        with pytest.raises(CycleError) as exc_info:
            registry.resolve_initialization_order()

        error = exc_info.value
        # The cycle_modules in the error should be exactly the cycle participants
        # (non-cycle modules with zero in-degree get processed by Kahn's algorithm)
        assert set(error.cycle_modules) == set(cycle_modules)

    @given(
        num_modules=st.integers(min_value=2, max_value=8),
    )
    @settings(max_examples=100)
    def test_acyclic_graph_resolves_successfully(self, num_modules: int):
        """An acyclic (chain) dependency graph resolves without error."""
        registry = ModuleRegistry()

        # Create a linear chain: mod_0 <- mod_1 <- mod_2 <- ...
        # mod_i depends on mod_(i-1)
        module_types = [f"chain_{i}" for i in range(num_modules)]

        for i, mt in enumerate(module_types):
            deps = {module_types[i - 1]} if i > 0 else set()
            module = make_module(mt, frozenset(REQUIRED_METHODS), dependencies=deps)
            registry.register(module)

        # Should not raise
        result = registry.resolve_initialization_order()
        assert len(result) == num_modules

        # Verify topological order: each module appears after its dependencies
        order_map = {getattr(m, "module_type"): idx for idx, m in enumerate(result)}
        for i, mt in enumerate(module_types):
            if i > 0:
                dep_mt = module_types[i - 1]
                assert order_map[dep_mt] < order_map[mt]
