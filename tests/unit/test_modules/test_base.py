"""Tests for DomainModule abstract base class."""

from __future__ import annotations

from typing import ClassVar, Set
from unittest.mock import MagicMock

import simpy

from enterprise_sim.modules.base import DomainModule


class ConcreteDomainModule(DomainModule):
    """A concrete implementation for testing purposes."""

    module_type: ClassVar[str] = "test_module"
    dependencies: ClassVar[Set[str]] = {"dep_a", "dep_b"}

    def initialize(self, env, event_bus, config) -> None:
        self.initialized = True

    def create_processes(self, env) -> list[simpy.events.Process]:
        return []

    def finalize(self) -> None:
        self.finalized = True


class ModuleWithCustomValidation(DomainModule):
    """Module that overrides validate_config."""

    module_type: ClassVar[str] = "validated_module"
    dependencies: ClassVar[Set[str]] = set()

    def initialize(self, env, event_bus, config) -> None:
        pass

    def create_processes(self, env) -> list[simpy.events.Process]:
        return []

    def finalize(self) -> None:
        pass

    def validate_config(self, config) -> list[str]:
        errors = []
        if not hasattr(config, "mission_id"):
            errors.append("Missing mission_id")
        return errors


class TestDomainModuleInterface:
    """Test that DomainModule enforces the abstract interface contract."""

    def test_cannot_instantiate_abstract_class(self):
        """DomainModule cannot be instantiated directly."""
        try:
            DomainModule()  # type: ignore
            assert False, "Should have raised TypeError"
        except TypeError as e:
            assert "abstract" in str(e).lower()

    def test_concrete_module_instantiates(self):
        """A fully-implemented subclass can be instantiated."""
        module = ConcreteDomainModule()
        assert module.module_type == "test_module"
        assert module.dependencies == {"dep_a", "dep_b"}

    def test_module_type_is_class_var(self):
        """module_type is a class-level attribute."""
        assert ConcreteDomainModule.module_type == "test_module"

    def test_dependencies_is_class_var(self):
        """dependencies is a class-level attribute."""
        assert ConcreteDomainModule.dependencies == {"dep_a", "dep_b"}

    def test_initialize_is_called(self):
        """initialize method can be called with expected arguments."""
        module = ConcreteDomainModule()
        env = simpy.Environment()
        event_bus = MagicMock()
        config = MagicMock()

        module.initialize(env, event_bus, config)
        assert module.initialized is True

    def test_create_processes_returns_list(self):
        """create_processes returns a list."""
        module = ConcreteDomainModule()
        env = simpy.Environment()
        result = module.create_processes(env)
        assert isinstance(result, list)

    def test_finalize_is_called(self):
        """finalize method can be called."""
        module = ConcreteDomainModule()
        module.finalize()
        assert module.finalized is True

    def test_validate_config_default_returns_empty_list(self):
        """Default validate_config returns an empty list."""
        module = ConcreteDomainModule()
        config = MagicMock()
        result = module.validate_config(config)
        assert result == []

    def test_validate_config_can_be_overridden(self):
        """Subclasses can override validate_config to return errors."""
        module = ModuleWithCustomValidation()
        config = MagicMock(spec=[])  # no attributes
        result = module.validate_config(config)
        assert "Missing mission_id" in result

    def test_validate_config_override_returns_empty_on_valid(self):
        """Overridden validate_config returns empty list for valid config."""
        module = ModuleWithCustomValidation()
        config = MagicMock()
        config.mission_id = "test-mission"
        result = module.validate_config(config)
        assert result == []

    def test_incomplete_subclass_cannot_instantiate(self):
        """A subclass missing abstract methods cannot be instantiated."""

        class IncompleteModule(DomainModule):
            module_type: ClassVar[str] = "incomplete"
            dependencies: ClassVar[Set[str]] = set()

            def initialize(self, env, event_bus, config) -> None:
                pass

            # Missing create_processes and finalize

        try:
            IncompleteModule()  # type: ignore
            assert False, "Should have raised TypeError"
        except TypeError as e:
            assert "abstract" in str(e).lower()

    def test_empty_dependencies(self):
        """A module can declare no dependencies."""

        class NoDepsModule(DomainModule):
            module_type: ClassVar[str] = "no_deps"
            dependencies: ClassVar[Set[str]] = set()

            def initialize(self, env, event_bus, config) -> None:
                pass

            def create_processes(self, env) -> list[simpy.events.Process]:
                return []

            def finalize(self) -> None:
                pass

        module = NoDepsModule()
        assert module.dependencies == set()
