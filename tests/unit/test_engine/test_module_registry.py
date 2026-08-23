"""Unit tests for the ModuleRegistry implementation."""

from __future__ import annotations

from typing import ClassVar, Set

import pytest
import simpy

from enterprise_sim.engine.exceptions import CycleError, RegistrationError
from enterprise_sim.engine.module_registry import ModuleRegistry
from enterprise_sim.modules.base import DomainModule


# --- Test helper modules ---


class ValidModuleA(DomainModule):
    """A valid domain module with no dependencies."""

    module_type: ClassVar[str] = "module_a"
    dependencies: ClassVar[Set[str]] = set()

    def initialize(self, env, event_bus, config):
        pass

    def create_processes(self, env):
        return []

    def finalize(self):
        pass


class ValidModuleB(DomainModule):
    """A valid domain module that depends on module_a."""

    module_type: ClassVar[str] = "module_b"
    dependencies: ClassVar[Set[str]] = {"module_a"}

    def initialize(self, env, event_bus, config):
        pass

    def create_processes(self, env):
        return []

    def finalize(self):
        pass


class ValidModuleC(DomainModule):
    """A valid domain module that depends on module_b."""

    module_type: ClassVar[str] = "module_c"
    dependencies: ClassVar[Set[str]] = {"module_b"}

    def initialize(self, env, event_bus, config):
        pass

    def create_processes(self, env):
        return []

    def finalize(self):
        pass


class ModuleNoDependencies(DomainModule):
    """An independent module with no dependencies."""

    module_type: ClassVar[str] = "independent"
    dependencies: ClassVar[Set[str]] = set()

    def initialize(self, env, event_bus, config):
        pass

    def create_processes(self, env):
        return []

    def finalize(self):
        pass


class CyclicModuleX(DomainModule):
    """Module X depends on Y — used for cycle testing."""

    module_type: ClassVar[str] = "cyclic_x"
    dependencies: ClassVar[Set[str]] = {"cyclic_y"}

    def initialize(self, env, event_bus, config):
        pass

    def create_processes(self, env):
        return []

    def finalize(self):
        pass


class CyclicModuleY(DomainModule):
    """Module Y depends on X — used for cycle testing."""

    module_type: ClassVar[str] = "cyclic_y"
    dependencies: ClassVar[Set[str]] = {"cyclic_x"}

    def initialize(self, env, event_bus, config):
        pass

    def create_processes(self, env):
        return []

    def finalize(self):
        pass


# --- Tests ---


class TestRegistration:
    """Tests for module registration validation."""

    def test_register_valid_module(self):
        registry = ModuleRegistry()
        module = ValidModuleA()
        registry.register(module)
        assert registry.get("module_a") is module

    def test_register_rejects_duplicate_module_type(self):
        registry = ModuleRegistry()
        registry.register(ValidModuleA())
        with pytest.raises(RegistrationError) as exc_info:
            registry.register(ValidModuleA())
        assert exc_info.value.module_name == "module_a"

    def test_register_rejects_missing_methods(self):
        registry = ModuleRegistry()

        # Create an object missing required methods
        class IncompleteModule:
            module_type = "incomplete"
            dependencies = set()

            def initialize(self, env, event_bus, config):
                pass

            # Missing create_processes and finalize

        with pytest.raises(RegistrationError) as exc_info:
            registry.register(IncompleteModule())  # type: ignore
        assert "create_processes" in exc_info.value.missing_methods
        assert "finalize" in exc_info.value.missing_methods
        assert exc_info.value.module_name == "incomplete"

    def test_register_rejects_non_callable_methods(self):
        registry = ModuleRegistry()

        class BadModule:
            module_type = "bad"
            dependencies = set()
            initialize = "not a method"  # Not callable
            create_processes = 42  # Not callable
            finalize = None  # Not callable

        with pytest.raises(RegistrationError) as exc_info:
            registry.register(BadModule())  # type: ignore
        assert "initialize" in exc_info.value.missing_methods
        assert "create_processes" in exc_info.value.missing_methods
        assert "finalize" in exc_info.value.missing_methods

    def test_register_identifies_all_missing_methods(self):
        registry = ModuleRegistry()

        class EmptyModule:
            module_type = "empty"
            dependencies = set()

        with pytest.raises(RegistrationError) as exc_info:
            registry.register(EmptyModule())  # type: ignore
        # All three required methods should be listed
        assert len(exc_info.value.missing_methods) == 3
        assert set(exc_info.value.missing_methods) == {
            "initialize",
            "create_processes",
            "finalize",
        }


class TestGet:
    """Tests for module retrieval."""

    def test_get_registered_module(self):
        registry = ModuleRegistry()
        module = ValidModuleA()
        registry.register(module)
        assert registry.get("module_a") is module

    def test_get_returns_none_for_missing(self):
        registry = ModuleRegistry()
        assert registry.get("nonexistent") is None

    def test_get_all_returns_all_registered(self):
        registry = ModuleRegistry()
        mod_a = ValidModuleA()
        mod_b = ValidModuleB()
        registry.register(mod_a)
        registry.register(mod_b)
        all_modules = registry.get_all()
        assert len(all_modules) == 2
        assert mod_a in all_modules
        assert mod_b in all_modules

    def test_get_all_empty_registry(self):
        registry = ModuleRegistry()
        assert registry.get_all() == []


class TestResolveInitializationOrder:
    """Tests for topological sort of module dependencies."""

    def test_single_module_no_deps(self):
        registry = ModuleRegistry()
        mod = ValidModuleA()
        registry.register(mod)
        order = registry.resolve_initialization_order()
        assert order == [mod]

    def test_linear_dependency_chain(self):
        registry = ModuleRegistry()
        mod_a = ValidModuleA()
        mod_b = ValidModuleB()  # depends on A
        mod_c = ValidModuleC()  # depends on B
        registry.register(mod_c)
        registry.register(mod_a)
        registry.register(mod_b)
        order = registry.resolve_initialization_order()
        # A must come before B, B before C
        assert order.index(mod_a) < order.index(mod_b)
        assert order.index(mod_b) < order.index(mod_c)

    def test_independent_modules(self):
        registry = ModuleRegistry()
        mod_a = ValidModuleA()
        mod_ind = ModuleNoDependencies()
        registry.register(mod_a)
        registry.register(mod_ind)
        order = registry.resolve_initialization_order()
        assert len(order) == 2
        # Both should be present (order among independents is not strictly defined)
        assert set(order) == {mod_a, mod_ind}

    def test_cycle_raises_error(self):
        registry = ModuleRegistry()
        registry.register(CyclicModuleX())
        registry.register(CyclicModuleY())
        with pytest.raises(CycleError) as exc_info:
            registry.resolve_initialization_order()
        assert set(exc_info.value.cycle_modules) == {"cyclic_x", "cyclic_y"}

    def test_empty_registry(self):
        registry = ModuleRegistry()
        assert registry.resolve_initialization_order() == []

    def test_unregistered_dependency_is_ignored(self):
        """Modules with dependencies not in the registry should still resolve."""
        registry = ModuleRegistry()
        mod_b = ValidModuleB()  # depends on module_a which is not registered
        registry.register(mod_b)
        order = registry.resolve_initialization_order()
        assert order == [mod_b]

    def test_three_module_cycle(self):
        """Test detection of a 3-node cycle."""

        class CycA(DomainModule):
            module_type: ClassVar[str] = "cyc_a"
            dependencies: ClassVar[Set[str]] = {"cyc_c"}

            def initialize(self, env, event_bus, config):
                pass

            def create_processes(self, env):
                return []

            def finalize(self):
                pass

        class CycB(DomainModule):
            module_type: ClassVar[str] = "cyc_b"
            dependencies: ClassVar[Set[str]] = {"cyc_a"}

            def initialize(self, env, event_bus, config):
                pass

            def create_processes(self, env):
                return []

            def finalize(self):
                pass

        class CycC(DomainModule):
            module_type: ClassVar[str] = "cyc_c"
            dependencies: ClassVar[Set[str]] = {"cyc_b"}

            def initialize(self, env, event_bus, config):
                pass

            def create_processes(self, env):
                return []

            def finalize(self):
                pass

        registry = ModuleRegistry()
        registry.register(CycA())
        registry.register(CycB())
        registry.register(CycC())
        with pytest.raises(CycleError) as exc_info:
            registry.resolve_initialization_order()
        assert set(exc_info.value.cycle_modules) == {"cyc_a", "cyc_b", "cyc_c"}
