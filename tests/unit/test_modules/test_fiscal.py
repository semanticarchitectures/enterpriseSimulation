"""Unit tests for FiscalModule.

Tests the fiscal module's debit logic, cost computation, event publishing,
and lifecycle methods.
"""

from __future__ import annotations

from typing import ClassVar, Set
from unittest.mock import MagicMock, patch

import simpy
import pytest

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import FundsExhaustedEvent, LowBalanceWarningEvent
from enterprise_sim.models.fiscal import FiscalAccount, FiscalConfig
from enterprise_sim.modules.fiscal import FiscalModule


def _make_config(
    accounts: list[FiscalAccount] | None = None,
    cost_per_flying_hour: float = 100.0,
    cost_per_fuel_unit: float = 5.0,
):
    """Helper to create a mock MissionConfiguration with fiscal parameters."""
    if accounts is None:
        accounts = [
            FiscalAccount(
                entity_id="acct_ops",
                account_id="ops",
                initial_allocation=10000.0,
                current_balance=10000.0,
                warning_threshold_pct=0.1,
            )
        ]
    fiscal_config = FiscalConfig(
        accounts=accounts,
        cost_per_flying_hour=cost_per_flying_hour,
        cost_per_fuel_unit=cost_per_fuel_unit,
    )
    config = MagicMock()
    config.fiscal_parameters = fiscal_config
    return config


class TestFiscalModuleMetadata:
    """Test class-level metadata."""

    def test_module_type(self):
        assert FiscalModule.module_type == "fiscal"

    def test_dependencies_empty(self):
        assert FiscalModule.dependencies == set()


class TestFiscalModuleInitialize:
    """Test initialization behavior."""

    def test_initialize_sets_up_accounts(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()

        module.initialize(env, event_bus, config)

        assert "ops" in module.accounts
        assert module.accounts["ops"].current_balance == 10000.0
        assert module.cumulative_debits["ops"] == 0.0

    def test_initialize_with_no_fiscal_parameters(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = MagicMock()
        config.fiscal_parameters = None

        module.initialize(env, event_bus, config)

        assert module.accounts == {}
        assert module.cumulative_debits == {}

    def test_initialize_multiple_accounts(self):
        accounts = [
            FiscalAccount(
                entity_id="acct_a",
                account_id="a",
                initial_allocation=5000.0,
                current_balance=5000.0,
            ),
            FiscalAccount(
                entity_id="acct_b",
                account_id="b",
                initial_allocation=3000.0,
                current_balance=3000.0,
            ),
        ]
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(accounts=accounts)

        module.initialize(env, event_bus, config)

        assert "a" in module.accounts
        assert "b" in module.accounts
        assert module.cumulative_debits["a"] == 0.0
        assert module.cumulative_debits["b"] == 0.0


class TestFiscalModuleDebit:
    """Test debit logic."""

    def _setup_module(self, initial_balance=10000.0, threshold_pct=0.1):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        accounts = [
            FiscalAccount(
                entity_id="acct_ops",
                account_id="ops",
                initial_allocation=initial_balance,
                current_balance=initial_balance,
                warning_threshold_pct=threshold_pct,
            )
        ]
        config = _make_config(accounts=accounts)
        module.initialize(env, event_bus, config)
        return module, event_bus

    def test_successful_debit(self):
        module, _ = self._setup_module()

        result = module.debit("ops", 500.0, "flying_hour", 1.0)

        assert result is True
        assert module.accounts["ops"].current_balance == 9500.0
        assert module.cumulative_debits["ops"] == 500.0

    def test_debit_records_line_item(self):
        module, _ = self._setup_module()

        module.debit("ops", 200.0, "fuel", 5.0)

        records = module.debit_records["ops"]
        assert len(records) == 1
        assert records[0].amount == 200.0
        assert records[0].cost_category == "fuel"
        assert records[0].balance_after == 9800.0
        assert records[0].timestamp == 5.0

    def test_multiple_debits_accumulate(self):
        module, _ = self._setup_module()

        module.debit("ops", 1000.0, "flying_hour", 1.0)
        module.debit("ops", 2000.0, "fuel", 2.0)
        module.debit("ops", 500.0, "other", 3.0)

        assert module.accounts["ops"].current_balance == 6500.0
        assert module.cumulative_debits["ops"] == 3500.0
        assert len(module.debit_records["ops"]) == 3

    def test_overdraft_rejected(self):
        module, _ = self._setup_module(initial_balance=100.0)

        result = module.debit("ops", 200.0, "fuel", 1.0)

        assert result is False
        # Balance unchanged
        assert module.accounts["ops"].current_balance == 100.0
        assert module.cumulative_debits["ops"] == 0.0

    def test_overdraft_publishes_funds_exhausted_event(self):
        module, event_bus = self._setup_module(initial_balance=100.0)
        events_received = []
        event_bus.subscribe(
            FundsExhaustedEvent,
            lambda e: events_received.append(e),
            "test_subscriber",
        )

        module.debit("ops", 200.0, "fuel", 1.0)

        assert len(events_received) == 1
        evt = events_received[0]
        assert evt.account_id == "ops"
        assert evt.requested_amount == 200.0
        assert evt.current_balance == 100.0

    def test_exact_balance_debit_succeeds(self):
        """Debit equal to current balance should succeed (result is zero, not negative)."""
        module, _ = self._setup_module(initial_balance=500.0)

        result = module.debit("ops", 500.0, "other", 1.0)

        assert result is True
        assert module.accounts["ops"].current_balance == 0.0

    def test_low_balance_warning_published(self):
        """Warning event published when balance drops below threshold."""
        module, event_bus = self._setup_module(
            initial_balance=1000.0, threshold_pct=0.2
        )
        # Threshold is 0.2 * 1000 = 200
        warnings = []
        event_bus.subscribe(
            LowBalanceWarningEvent,
            lambda e: warnings.append(e),
            "test_subscriber",
        )

        # Debit that leaves balance at 100 (below threshold of 200)
        module.debit("ops", 900.0, "fuel", 1.0)

        assert len(warnings) == 1
        assert warnings[0].account_id == "ops"
        assert warnings[0].current_balance == 100.0
        assert warnings[0].threshold_pct == 0.2

    def test_no_warning_when_above_threshold(self):
        """No warning event when balance is still above threshold."""
        module, event_bus = self._setup_module(
            initial_balance=1000.0, threshold_pct=0.1
        )
        # Threshold is 0.1 * 1000 = 100
        warnings = []
        event_bus.subscribe(
            LowBalanceWarningEvent,
            lambda e: warnings.append(e),
            "test_subscriber",
        )

        # Debit that leaves balance at 800 (above threshold of 100)
        module.debit("ops", 200.0, "fuel", 1.0)

        assert len(warnings) == 0

    def test_subsequent_debits_below_threshold_all_warn(self):
        """Each subsequent debit while below threshold triggers a warning."""
        module, event_bus = self._setup_module(
            initial_balance=1000.0, threshold_pct=0.2
        )
        # Threshold is 200
        warnings = []
        event_bus.subscribe(
            LowBalanceWarningEvent,
            lambda e: warnings.append(e),
            "test_subscriber",
        )

        # First debit drops to 100 (below 200) → warning
        module.debit("ops", 900.0, "fuel", 1.0)
        # Second debit drops to 50 (still below 200) → warning
        module.debit("ops", 50.0, "fuel", 2.0)

        assert len(warnings) == 2


class TestFiscalModuleCostComputation:
    """Test cost computation helpers."""

    def test_compute_flying_hour_cost(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(cost_per_flying_hour=150.0)
        module.initialize(env, event_bus, config)

        cost = module.compute_flying_hour_cost(3.5)
        assert cost == 525.0  # 3.5 * 150

    def test_compute_fuel_cost(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(cost_per_fuel_unit=8.0)
        module.initialize(env, event_bus, config)

        cost = module.compute_fuel_cost(200.0)
        assert cost == 1600.0  # 200 * 8

    def test_compute_flying_hour_cost_zero_hours(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(cost_per_flying_hour=100.0)
        module.initialize(env, event_bus, config)

        assert module.compute_flying_hour_cost(0.0) == 0.0

    def test_compute_fuel_cost_zero_units(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config(cost_per_fuel_unit=5.0)
        module.initialize(env, event_bus, config)

        assert module.compute_fuel_cost(0.0) == 0.0


class TestFiscalModuleLifecycle:
    """Test create_processes and finalize."""

    def test_create_processes_returns_empty_list(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        processes = module.create_processes(env)
        assert processes == []

    def test_finalize_cleans_up_state(self):
        module = FiscalModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        # Perform a debit to populate state
        module.debit("ops", 100.0, "fuel", 1.0)
        assert len(module.debit_records["ops"]) == 1

        module.finalize()

        assert module.accounts == {}
        assert module.cumulative_debits == {}
        assert module.debit_records == {}
