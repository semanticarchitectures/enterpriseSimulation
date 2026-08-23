"""FiscalAccount, DebitRecord, FiscalConfig models.

Implements fiscal domain entities for tracking mission costs against
configured fund accounts. FiscalAccount is a Continuant (persists through
time with identity), DebitRecord is an Occurrent (a time-stamped event),
and FiscalConfig groups configuration parameters for the fiscal module.
"""

from pydantic import BaseModel, Field

from enterprise_sim.models.base import Continuant, Occurrent


class FiscalAccount(Continuant):
    """A fiscal account with balance tracking.

    Represents a persistent fund account that accrues debits over the
    course of a mission. Tracks both the initial allocation and the
    current remaining balance.
    """

    entity_type: str = "fiscal_account"
    account_id: str
    initial_allocation: float = Field(..., gt=0)
    current_balance: float = Field(..., ge=0)
    warning_threshold_pct: float = Field(0.1, ge=0, le=1)


class DebitRecord(Occurrent):
    """A single debit line item.

    Records a cost event against a fiscal account, capturing the amount,
    category, and resulting balance at a specific simulation timestamp.
    """

    entity_type: str = "debit_record"
    account_id: str
    amount: float = Field(..., gt=0)
    cost_category: str  # "flying_hour" | "fuel" | "other"
    balance_after: float


class FiscalConfig(BaseModel):
    """Mission fiscal configuration.

    Groups the fiscal accounts and cost rate parameters used by the
    Fiscal Module during simulation execution.
    """

    accounts: list[FiscalAccount]
    cost_per_flying_hour: float = Field(..., gt=0)
    cost_per_fuel_unit: float = Field(..., gt=0)
