"""Unit tests for the custom exception hierarchy."""

import pytest

from enterprise_sim.engine.exceptions import (
    ConfigurationError,
    CycleError,
    GraphValidationError,
    RegistrationError,
    SimulationError,
)


class TestSimulationError:
    """Tests for the base SimulationError exception."""

    def test_is_exception(self):
        assert issubclass(SimulationError, Exception)

    def test_message_attribute(self):
        err = SimulationError("test error")
        assert err.message == "test error"
        assert str(err) == "test error"

    def test_empty_message(self):
        err = SimulationError()
        assert err.message == ""


class TestConfigurationError:
    """Tests for ConfigurationError with structured fields."""

    def test_inherits_simulation_error(self):
        assert issubclass(ConfigurationError, SimulationError)

    def test_fields_stored(self):
        err = ConfigurationError(
            invalid_fields=["fuel_weight", "altitude"],
            violations=["must be positive", "must be > 0"],
        )
        assert err.invalid_fields == ["fuel_weight", "altitude"]
        assert err.violations == ["must be positive", "must be > 0"]

    def test_auto_message(self):
        err = ConfigurationError(invalid_fields=["x", "y"])
        assert "x" in str(err)
        assert "y" in str(err)

    def test_custom_message_overrides(self):
        err = ConfigurationError(message="custom", invalid_fields=["x"])
        assert str(err) == "custom"

    def test_defaults_to_empty_lists(self):
        err = ConfigurationError()
        assert err.invalid_fields == []
        assert err.violations == []

    def test_catchable_as_simulation_error(self):
        with pytest.raises(SimulationError):
            raise ConfigurationError(invalid_fields=["bad_field"])


class TestRegistrationError:
    """Tests for RegistrationError with structured fields."""

    def test_inherits_simulation_error(self):
        assert issubclass(RegistrationError, SimulationError)

    def test_fields_stored(self):
        err = RegistrationError(
            module_name="broken_module",
            missing_methods=["initialize", "finalize"],
        )
        assert err.module_name == "broken_module"
        assert err.missing_methods == ["initialize", "finalize"]

    def test_auto_message(self):
        err = RegistrationError(
            module_name="mod_a", missing_methods=["create_processes"]
        )
        assert "mod_a" in str(err)
        assert "create_processes" in str(err)

    def test_defaults(self):
        err = RegistrationError()
        assert err.module_name == ""
        assert err.missing_methods == []


class TestCycleError:
    """Tests for CycleError with structured fields."""

    def test_inherits_simulation_error(self):
        assert issubclass(CycleError, SimulationError)

    def test_cycle_modules_stored(self):
        err = CycleError(cycle_modules=["auth", "fiscal", "auth"])
        assert err.cycle_modules == ["auth", "fiscal", "auth"]

    def test_auto_message(self):
        err = CycleError(cycle_modules=["a", "b", "c"])
        assert "a" in str(err)
        assert "b" in str(err)

    def test_defaults(self):
        err = CycleError()
        assert err.cycle_modules == []


class TestGraphValidationError:
    """Tests for GraphValidationError with structured fields."""

    def test_inherits_simulation_error(self):
        assert issubclass(GraphValidationError, SimulationError)

    def test_fields_stored(self):
        err = GraphValidationError(
            node_name="WP3", reason="no outbound edges"
        )
        assert err.node_name == "WP3"
        assert err.reason == "no outbound edges"

    def test_auto_message(self):
        err = GraphValidationError(node_name="NODE_X", reason="disconnected")
        assert "NODE_X" in str(err)
        assert "disconnected" in str(err)

    def test_defaults(self):
        err = GraphValidationError()
        assert err.node_name == ""
        assert err.reason == ""
