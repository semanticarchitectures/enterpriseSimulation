"""Property tests for Fiscal Module.

**Property 10: Fiscal Balance Invariant**
**Property 11: Fiscal Overdraft Prevention**
**Property 12: Fiscal Cost Computation**
**Property 13: Fiscal Low-Balance Warning**
**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

Property 10: For any fiscal account after a sequence of debit operations, the
current_balance SHALL equal the initial_allocation minus the sum of all successful
debit amounts, and the cumulative_debits counter SHALL equal the sum of all
successful debit amounts.

Property 11: For any fiscal account and debit amount where the debit exceeds the
current balance, the balance SHALL remain unchanged after the operation, and a
funds-exhausted event SHALL be published.

Property 12: For any flying-hour quantity and fuel quantity, the computed cost line
items SHALL equal (hours × cost_per_flying_hour) and (fuel × cost_per_fuel_unit)
respectively.

Property 13: For any fiscal account where the current balance is below
(warning_threshold_pct × initial_allocation), every subsequent successful debit
SHALL trigger a low-balance warning event.
"""

import simpy
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.events import (
    FundsExhaustedEvent,
    LowBalanceWarningEvent,
    SimulationEvent,
)
from enterprise_sim.models.fiscal import FiscalAccount, FiscalConfig
from enterprise_sim.models.mission import MissionConfiguration
from enterprise_sim.models.route import RouteGraphConfig, RouteNode, RouteEdge
from enterprise_sim.models.aerodynamics import AircraftConfig
from enterprise_sim.modules.fiscal import FiscalModule


# === Hypothesis Strategies ===

# Positive floats for monetary amounts
positive_float = st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False)

# Non-negative floats for timestamps
non_negative_float = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)

# Warning threshold between 0 and 1
threshold_pct_st = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# Cost rate strategies
cost_rate_st = st.floats(min_value=0.01, max_value=1e4, allow_nan=False, allow_infinity=False)

# Debit amount strategies (positive values)
debit_amount_st = st.floats(min_value=0.01, max_value=1e5, allow_nan=False, allow_infinity=False)

# Lists of debit amounts
debit_amounts_st = st.lists(debit_amount_st, min_size=1, max_size=20)


def _make_minimal_route_graph() -> RouteGraphConfig:
    """Create a minimal valid route graph for MissionConfiguration."""
    return RouteGraphConfig(
        nodes=[
            RouteNode(
                entity_id="node_a",
                name="A",
                node_type="base",
            ),
            RouteNode(
                entity_id="node_b",
                name="B",
                node_type="base",
            ),
        ],
        edges=[
            RouteEdge(source="A", destination="B", distance_nm=100.0),
        ],
        origin="A",
        destination="B",
    )


def _make_minimal_aircraft() -> AircraftConfig:
    """Create a minimal valid aircraft config for MissionConfiguration."""
    return AircraftConfig(
        entity_id="aircraft_1",
        aircraft_type="C-17",
        lift_to_drag_ratio=15.0,
        specific_fuel_consumption=0.5,
        initial_fuel_weight=50000.0,
        max_fuel_capacity=60000.0,
    )


def _make_mission_config(
    initial_allocation: float,
    warning_threshold_pct: float,
    cost_per_flying_hour: float,
    cost_per_fuel_unit: float,
    account_id: str = "acct_1",
) -> MissionConfiguration:
    """Build a MissionConfiguration with the given fiscal parameters."""
    return MissionConfiguration(
        mission_id="test_mission",
        mission_name="Test Mission",
        route_graph=_make_minimal_route_graph(),
        aircraft=_make_minimal_aircraft(),
        fiscal_parameters=FiscalConfig(
            accounts=[
                FiscalAccount(
                    entity_id=f"fiscal_{account_id}",
                    account_id=account_id,
                    initial_allocation=initial_allocation,
                    current_balance=initial_allocation,
                    warning_threshold_pct=warning_threshold_pct,
                )
            ],
            cost_per_flying_hour=cost_per_flying_hour,
            cost_per_fuel_unit=cost_per_fuel_unit,
        ),
        active_modules=["fiscal"],
    )


def _setup_fiscal_module(
    initial_allocation: float,
    warning_threshold_pct: float = 0.1,
    cost_per_flying_hour: float = 100.0,
    cost_per_fuel_unit: float = 5.0,
    account_id: str = "acct_1",
) -> tuple[FiscalModule, EventBus, simpy.Environment]:
    """Create and initialize a FiscalModule with the given parameters."""
    env = simpy.Environment()
    event_bus = EventBus()
    module = FiscalModule()

    config = _make_mission_config(
        initial_allocation=initial_allocation,
        warning_threshold_pct=warning_threshold_pct,
        cost_per_flying_hour=cost_per_flying_hour,
        cost_per_fuel_unit=cost_per_fuel_unit,
        account_id=account_id,
    )

    module.initialize(env, event_bus, config)
    return module, event_bus, env


class TestFiscalBalanceInvariant:
    """Property 10: Fiscal Balance Invariant.

    **Validates: Requirements 5.1, 5.6**

    For any fiscal account after a sequence of debit operations, the current_balance
    SHALL equal the initial_allocation minus the sum of all successful debit amounts,
    and the cumulative_debits counter SHALL equal the sum of all successful debit
    amounts.
    """

    @given(
        initial_allocation=positive_float,
        debit_amounts=debit_amounts_st,
    )
    @settings(max_examples=200)
    def test_balance_equals_initial_minus_successful_debits(
        self,
        initial_allocation: float,
        debit_amounts: list[float],
    ):
        """Balance after debits equals initial_allocation - sum(successful debits)."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
        )

        successful_sum = 0.0
        for i, amount in enumerate(debit_amounts):
            result = module.debit("acct_1", amount, "other", float(i))
            if result:
                successful_sum += amount

        account = module.accounts["acct_1"]
        expected_balance = initial_allocation - successful_sum

        # Allow small floating-point tolerance
        assert abs(account.current_balance - expected_balance) < 1e-9, (
            f"Expected balance {expected_balance}, got {account.current_balance}"
        )

    @given(
        initial_allocation=positive_float,
        debit_amounts=debit_amounts_st,
    )
    @settings(max_examples=200)
    def test_cumulative_debits_equals_sum_of_successful(
        self,
        initial_allocation: float,
        debit_amounts: list[float],
    ):
        """Cumulative debits counter equals sum of all successful debit amounts."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
        )

        successful_sum = 0.0
        for i, amount in enumerate(debit_amounts):
            result = module.debit("acct_1", amount, "other", float(i))
            if result:
                successful_sum += amount

        cumulative = module.cumulative_debits["acct_1"]

        assert abs(cumulative - successful_sum) < 1e-9, (
            f"Expected cumulative_debits {successful_sum}, got {cumulative}"
        )

    @given(
        initial_allocation=positive_float,
        debit_amounts=debit_amounts_st,
    )
    @settings(max_examples=200)
    def test_balance_and_cumulative_sum_to_initial(
        self,
        initial_allocation: float,
        debit_amounts: list[float],
    ):
        """current_balance + cumulative_debits == initial_allocation (conservation)."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
        )

        for i, amount in enumerate(debit_amounts):
            module.debit("acct_1", amount, "other", float(i))

        account = module.accounts["acct_1"]
        cumulative = module.cumulative_debits["acct_1"]

        total = account.current_balance + cumulative
        assert abs(total - initial_allocation) < 1e-9, (
            f"Balance ({account.current_balance}) + cumulative ({cumulative}) = {total}, "
            f"expected {initial_allocation}"
        )


class TestFiscalOverdraftPrevention:
    """Property 11: Fiscal Overdraft Prevention.

    **Validates: Requirements 5.3**

    For any fiscal account and debit amount where the debit exceeds the current
    balance, the balance SHALL remain unchanged after the operation, and a
    funds-exhausted event SHALL be published.
    """

    @given(
        initial_allocation=positive_float,
        overdraft_factor=st.floats(min_value=1.01, max_value=100.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_overdraft_leaves_balance_unchanged(
        self,
        initial_allocation: float,
        overdraft_factor: float,
    ):
        """Debit exceeding balance leaves balance unchanged."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
        )

        overdraft_amount = initial_allocation * overdraft_factor
        balance_before = module.accounts["acct_1"].current_balance

        result = module.debit("acct_1", overdraft_amount, "other", 0.0)

        assert result is False
        assert module.accounts["acct_1"].current_balance == balance_before

    @given(
        initial_allocation=positive_float,
        overdraft_factor=st.floats(min_value=1.01, max_value=100.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_overdraft_publishes_funds_exhausted_event(
        self,
        initial_allocation: float,
        overdraft_factor: float,
    ):
        """Overdraft attempt publishes FundsExhaustedEvent with correct data."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
        )

        events_received: list[SimulationEvent] = []
        event_bus.subscribe(
            FundsExhaustedEvent,
            lambda e: events_received.append(e),
            "test_subscriber",
        )

        overdraft_amount = initial_allocation * overdraft_factor
        module.debit("acct_1", overdraft_amount, "other", 1.0)

        assert len(events_received) == 1
        event = events_received[0]
        assert isinstance(event, FundsExhaustedEvent)
        assert event.account_id == "acct_1"
        assert event.requested_amount == overdraft_amount
        assert event.current_balance == initial_allocation

    @given(
        initial_allocation=positive_float,
        successful_debits=st.lists(
            st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=5,
        ),
        overdraft_factor=st.floats(min_value=1.01, max_value=10.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_overdraft_after_partial_debits(
        self,
        initial_allocation: float,
        successful_debits: list[float],
        overdraft_factor: float,
    ):
        """After partial debits, an overdraft attempt still leaves balance unchanged."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
        )

        # Apply some debits that may or may not succeed
        for i, amount in enumerate(successful_debits):
            module.debit("acct_1", amount, "other", float(i))

        balance_before = module.accounts["acct_1"].current_balance
        assume(balance_before > 0)  # Ensure there's still a balance to protect

        # Attempt overdraft
        overdraft_amount = balance_before * overdraft_factor
        result = module.debit("acct_1", overdraft_amount, "other", float(len(successful_debits)))

        assert result is False
        assert module.accounts["acct_1"].current_balance == balance_before


class TestFiscalCostComputation:
    """Property 12: Fiscal Cost Computation.

    **Validates: Requirements 5.2, 5.4**

    For any flying-hour quantity and fuel quantity, the computed cost line items
    SHALL equal (hours × cost_per_flying_hour) and (fuel × cost_per_fuel_unit)
    respectively, recorded as separate line items with correct cost categories.
    """

    @given(
        hours=positive_float,
        cost_per_flying_hour=cost_rate_st,
    )
    @settings(max_examples=200)
    def test_flying_hour_cost_computation(
        self,
        hours: float,
        cost_per_flying_hour: float,
    ):
        """compute_flying_hour_cost returns hours * cost_per_flying_hour."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=1e9,  # Large enough to not matter
            cost_per_flying_hour=cost_per_flying_hour,
        )

        result = module.compute_flying_hour_cost(hours)
        expected = hours * cost_per_flying_hour

        assert abs(result - expected) < 1e-6, (
            f"Expected {expected}, got {result}"
        )

    @given(
        fuel_units=positive_float,
        cost_per_fuel_unit=cost_rate_st,
    )
    @settings(max_examples=200)
    def test_fuel_cost_computation(
        self,
        fuel_units: float,
        cost_per_fuel_unit: float,
    ):
        """compute_fuel_cost returns fuel_units * cost_per_fuel_unit."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=1e9,
            cost_per_fuel_unit=cost_per_fuel_unit,
        )

        result = module.compute_fuel_cost(fuel_units)
        expected = fuel_units * cost_per_fuel_unit

        assert abs(result - expected) < 1e-6, (
            f"Expected {expected}, got {result}"
        )

    @given(
        initial_allocation=st.floats(min_value=1e6, max_value=1e9, allow_nan=False, allow_infinity=False),
        hours=positive_float,
        fuel_units=positive_float,
        cost_per_flying_hour=cost_rate_st,
        cost_per_fuel_unit=cost_rate_st,
    )
    @settings(max_examples=200)
    def test_cost_recorded_as_separate_line_items(
        self,
        initial_allocation: float,
        hours: float,
        fuel_units: float,
        cost_per_flying_hour: float,
        cost_per_fuel_unit: float,
    ):
        """Flying hour and fuel costs are recorded as separate debit line items."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
            cost_per_flying_hour=cost_per_flying_hour,
            cost_per_fuel_unit=cost_per_fuel_unit,
        )

        flying_cost = module.compute_flying_hour_cost(hours)
        fuel_cost = module.compute_fuel_cost(fuel_units)

        # Only debit if amounts fit within the allocation
        assume(flying_cost + fuel_cost <= initial_allocation)
        assume(flying_cost > 0)
        assume(fuel_cost > 0)

        # Debit flying hour cost
        result1 = module.debit("acct_1", flying_cost, "flying_hour", 1.0)
        assert result1 is True

        # Debit fuel cost
        result2 = module.debit("acct_1", fuel_cost, "fuel", 2.0)
        assert result2 is True

        # Check debit records
        records = module.debit_records["acct_1"]
        assert len(records) == 2

        # First record: flying hour
        assert records[0].cost_category == "flying_hour"
        assert abs(records[0].amount - flying_cost) < 1e-9

        # Second record: fuel
        assert records[1].cost_category == "fuel"
        assert abs(records[1].amount - fuel_cost) < 1e-9


class TestFiscalLowBalanceWarning:
    """Property 13: Fiscal Low-Balance Warning.

    **Validates: Requirements 5.5**

    For any fiscal account where the current balance is below
    (warning_threshold_pct × initial_allocation), every subsequent successful
    debit SHALL trigger a low-balance warning event.
    """

    @given(
        initial_allocation=st.floats(min_value=100.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        warning_threshold_pct=st.floats(min_value=0.1, max_value=0.9, allow_nan=False, allow_infinity=False),
        subsequent_debits=st.lists(
            st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=200)
    def test_low_balance_warning_on_every_successful_debit_below_threshold(
        self,
        initial_allocation: float,
        warning_threshold_pct: float,
        subsequent_debits: list[float],
    ):
        """Every successful debit while below threshold triggers a warning event."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
            warning_threshold_pct=warning_threshold_pct,
        )

        warning_events: list[LowBalanceWarningEvent] = []
        event_bus.subscribe(
            LowBalanceWarningEvent,
            lambda e: warning_events.append(e),
            "test_warning_subscriber",
        )

        threshold_amount = warning_threshold_pct * initial_allocation

        # First, bring balance below the threshold with a large debit
        # Debit enough to drop below threshold
        drop_amount = initial_allocation - (threshold_amount * 0.5)
        assume(drop_amount > 0)
        assume(drop_amount <= initial_allocation)

        result = module.debit("acct_1", drop_amount, "other", 0.0)
        assume(result is True)  # Must succeed to get below threshold

        balance_after_drop = module.accounts["acct_1"].current_balance
        assume(balance_after_drop < threshold_amount)  # Confirm we're below threshold

        # Clear warning events from the initial drop
        initial_warnings = len(warning_events)

        # Now do subsequent debits — each successful one should trigger a warning
        successful_subsequent = 0
        for i, amount in enumerate(subsequent_debits):
            current_balance = module.accounts["acct_1"].current_balance
            if amount <= current_balance:
                result = module.debit("acct_1", amount, "other", float(i + 1))
                if result:
                    successful_subsequent += 1

        # Each successful debit while below threshold should have generated a warning
        warnings_after_drop = len(warning_events) - initial_warnings
        assert warnings_after_drop == successful_subsequent, (
            f"Expected {successful_subsequent} warnings for subsequent debits, "
            f"got {warnings_after_drop}"
        )

    @given(
        initial_allocation=st.floats(min_value=1000.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        warning_threshold_pct=st.floats(min_value=0.1, max_value=0.5, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_no_warning_when_above_threshold(
        self,
        initial_allocation: float,
        warning_threshold_pct: float,
    ):
        """No low-balance warning when balance remains above threshold."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
            warning_threshold_pct=warning_threshold_pct,
        )

        warning_events: list[LowBalanceWarningEvent] = []
        event_bus.subscribe(
            LowBalanceWarningEvent,
            lambda e: warning_events.append(e),
            "test_warning_subscriber",
        )

        threshold_amount = warning_threshold_pct * initial_allocation

        # Debit an amount that keeps balance above threshold
        safe_debit = initial_allocation - threshold_amount - 1.0
        assume(safe_debit > 0)

        # Only debit a small portion that stays well above threshold
        small_debit = safe_debit * 0.5
        assume(small_debit > 0)

        result = module.debit("acct_1", small_debit, "other", 0.0)
        assert result is True

        remaining = module.accounts["acct_1"].current_balance
        assume(remaining >= threshold_amount)  # Confirm still above threshold

        assert len(warning_events) == 0, (
            f"Should have no warnings when above threshold, got {len(warning_events)}"
        )

    @given(
        initial_allocation=st.floats(min_value=100.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        warning_threshold_pct=st.floats(min_value=0.1, max_value=0.9, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_warning_event_contains_correct_data(
        self,
        initial_allocation: float,
        warning_threshold_pct: float,
    ):
        """Low-balance warning event contains the correct account and threshold data."""
        module, event_bus, env = _setup_fiscal_module(
            initial_allocation=initial_allocation,
            warning_threshold_pct=warning_threshold_pct,
        )

        warning_events: list[LowBalanceWarningEvent] = []
        event_bus.subscribe(
            LowBalanceWarningEvent,
            lambda e: warning_events.append(e),
            "test_warning_subscriber",
        )

        threshold_amount = warning_threshold_pct * initial_allocation

        # Debit to go below threshold
        drop_amount = initial_allocation - (threshold_amount * 0.5)
        assume(drop_amount > 0)
        assume(drop_amount <= initial_allocation)

        result = module.debit("acct_1", drop_amount, "other", 5.0)
        assume(result is True)

        balance_after = module.accounts["acct_1"].current_balance
        assume(balance_after < threshold_amount)

        # Should have one warning event
        assert len(warning_events) >= 1

        event = warning_events[0]
        assert event.account_id == "acct_1"
        assert event.threshold_pct == warning_threshold_pct
        assert abs(event.current_balance - balance_after) < 1e-9
