"""FiscalModule implementation.

A passive domain module that tracks mission costs against configured fiscal
accounts. The module exposes a `debit()` method that other modules call to
record cost-accruing events. It also subscribes to FuelConsumedEvent on the
Event Bus to automatically debit fuel costs when the aerodynamics module
reports fuel consumption.

It publishes FundsExhaustedEvent when a debit would overdraft, and
LowBalanceWarningEvent when the balance drops below the configured
warning threshold.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 8.1
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Set

import simpy

from enterprise_sim.models.events import (
    FuelConsumedEvent,
    FundsExhaustedEvent,
    LowBalanceWarningEvent,
    SimulationEvent,
)
from enterprise_sim.models.fiscal import DebitRecord, FiscalAccount
from enterprise_sim.modules.base import DomainModule

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.mission import MissionConfiguration


class FiscalModule(DomainModule):
    """Passive fiscal tracking module.

    Maintains fiscal account balances and records debits. Does not create
    SimPy processes — it responds to calls from other modules via the
    `debit()` method and cost computation helpers.
    """

    module_type: ClassVar[str] = "fiscal"
    dependencies: ClassVar[Set[str]] = set()

    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Set up accounts from config, initialize tracking state, subscribe to events."""
        self._env = env
        self._event_bus = event_bus

        # Set up accounts dict keyed by account_id
        self._accounts: dict[str, FiscalAccount] = {}
        self._initial_allocations: dict[str, float] = {}
        self._cumulative_debits: dict[str, float] = {}
        self._debit_records: dict[str, list[DebitRecord]] = {}

        if config.fiscal_parameters is not None:
            for account in config.fiscal_parameters.accounts:
                self._accounts[account.account_id] = account.model_copy()
                self._initial_allocations[account.account_id] = account.initial_allocation
                self._cumulative_debits[account.account_id] = 0.0
                self._debit_records[account.account_id] = []

            # Store cost rates from config
            self._cost_per_flying_hour = config.fiscal_parameters.cost_per_flying_hour
            self._cost_per_fuel_unit = config.fiscal_parameters.cost_per_fuel_unit
        else:
            self._cost_per_flying_hour = 0.0
            self._cost_per_fuel_unit = 0.0

        # Subscribe to FuelConsumedEvent to automatically debit fuel costs
        # This wires Aerodynamics → Fiscal communication (Requirement 8.1)
        event_bus.subscribe(
            FuelConsumedEvent,
            self._handle_fuel_consumed,
            subscriber_id="fiscal",
        )

    def debit(
        self,
        account_id: str,
        amount: float,
        category: str,
        timestamp: float,
    ) -> bool:
        """Attempt to debit the specified account.

        Args:
            account_id: The fiscal account to debit.
            amount: The amount to debit (must be positive).
            category: Cost category (e.g., "flying_hour", "fuel", "other").
            timestamp: The simulation timestamp for this debit.

        Returns:
            True if the debit succeeded, False if insufficient funds.
        """
        account = self._accounts[account_id]
        current_balance = account.current_balance

        # Overdraft check: if amount > current balance, reject
        if amount > current_balance:
            self._event_bus.publish(
                FundsExhaustedEvent(
                    entity_id=f"funds_exhausted_{account_id}_{timestamp}",
                    timestamp=timestamp,
                    source_module="fiscal",
                    account_id=account_id,
                    requested_amount=amount,
                    current_balance=current_balance,
                )
            )
            return False

        # Debit succeeds: subtract amount from balance
        new_balance = current_balance - amount
        account.current_balance = new_balance

        # Record the debit
        record = DebitRecord(
            entity_id=f"debit_{account_id}_{timestamp}",
            timestamp=timestamp,
            account_id=account_id,
            amount=amount,
            cost_category=category,
            balance_after=new_balance,
        )
        self._debit_records[account_id].append(record)

        # Update cumulative debits
        self._cumulative_debits[account_id] += amount

        # Low balance warning check
        warning_threshold = (
            account.warning_threshold_pct * self._initial_allocations[account_id]
        )
        if new_balance < warning_threshold:
            self._event_bus.publish(
                LowBalanceWarningEvent(
                    entity_id=f"low_balance_{account_id}_{timestamp}",
                    timestamp=timestamp,
                    source_module="fiscal",
                    account_id=account_id,
                    current_balance=new_balance,
                    threshold_pct=account.warning_threshold_pct,
                )
            )

        return True

    def compute_flying_hour_cost(self, hours: float) -> float:
        """Compute cost for the given number of flying hours.

        Args:
            hours: Number of flying hours.

        Returns:
            The cost amount (hours * cost_per_flying_hour).
        """
        return hours * self._cost_per_flying_hour

    def compute_fuel_cost(self, fuel_units: float) -> float:
        """Compute cost for the given fuel quantity.

        Args:
            fuel_units: Quantity of fuel consumed.

        Returns:
            The cost amount (fuel_units * cost_per_fuel_unit).
        """
        return fuel_units * self._cost_per_fuel_unit

    def _handle_fuel_consumed(self, event: SimulationEvent) -> None:
        """Handle FuelConsumedEvent by debiting fuel cost from the first account.

        This is the event bus handler that wires Aerodynamics → Fiscal.
        When the Aerodynamics module publishes a FuelConsumedEvent, this handler
        computes the fuel cost and debits the first available fiscal account.

        Args:
            event: A FuelConsumedEvent with leg_id and fuel_quantity fields.
        """
        if not isinstance(event, FuelConsumedEvent):
            return
        if not self._accounts:
            return

        fuel_cost = self.compute_fuel_cost(event.fuel_quantity)
        if fuel_cost <= 0:
            return

        # Debit the first available account
        account_id = next(iter(self._accounts))
        self.debit(account_id, fuel_cost, "fuel", event.timestamp)

    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Return empty list — fiscal module is passive."""
        return []

    def finalize(self) -> None:
        """Clean up internal state."""
        self._accounts = {}
        self._initial_allocations = {}
        self._cumulative_debits = {}
        self._debit_records = {}

    @property
    def accounts(self) -> dict[str, FiscalAccount]:
        """Access current account states."""
        return self._accounts

    @property
    def cumulative_debits(self) -> dict[str, float]:
        """Access cumulative debit totals per account."""
        return self._cumulative_debits

    @property
    def debit_records(self) -> dict[str, list[DebitRecord]]:
        """Access recorded debit line items per account."""
        return self._debit_records
