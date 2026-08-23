"""All event type definitions for the simulation framework.

This module defines typed events that domain modules publish to the Event Bus.
All event classes extend SimulationEvent, which extends the ontology-aligned
Occurrent base class. Events carry no SimPy dependencies and are pure Pydantic models.

Event categories:
    - Authorization: GateCompletionEvent, GateTimeoutEvent, ChainCompletionEvent
    - Fiscal: FundsExhaustedEvent, LowBalanceWarningEvent
    - C2 Messaging: MessageDeliveryFailureEvent
    - Cross-Domain: ClassificationViolationEvent, SanitizationCompleteEvent
    - Aerodynamics: FuelConsumedEvent, FuelInsufficientEvent
    - Airdrop: ExtractionStartEvent, ExtractionCompleteEvent, WeightExceededEvent
"""

from enterprise_sim.models.base import Occurrent


class SimulationEvent(Occurrent):
    """Base class for all simulation events.

    Every event carries an event_type discriminator and a source_module identifier
    indicating which domain module published the event.
    """

    entity_type: str = "simulation_event"
    event_type: str
    source_module: str


# === Authorization events ===


class GateCompletionEvent(SimulationEvent):
    """Published when an authorization gate completes processing."""

    event_type: str = "gate_completion"
    gate_name: str
    chain_id: str


class GateTimeoutEvent(SimulationEvent):
    """Published when an authorization gate exceeds its configured timeout."""

    event_type: str = "gate_timeout"
    gate_name: str
    chain_id: str


class ChainCompletionEvent(SimulationEvent):
    """Published when all gates in an authorization chain complete successfully."""

    event_type: str = "chain_completion"
    chain_id: str
    elapsed_time: float


# === Fiscal events ===


class FundsExhaustedEvent(SimulationEvent):
    """Published when a debit would reduce a fiscal account balance below zero."""

    event_type: str = "funds_exhausted"
    account_id: str
    requested_amount: float
    current_balance: float


class LowBalanceWarningEvent(SimulationEvent):
    """Published when a fiscal account balance is below the warning threshold."""

    event_type: str = "low_balance_warning"
    account_id: str
    current_balance: float
    threshold_pct: float


# === C2 Messaging events ===


class MessageDeliveryFailureEvent(SimulationEvent):
    """Published when a C2 message cannot be delivered to its destination."""

    event_type: str = "message_delivery_failure"
    message_type: str
    source: str
    intended_destination: str
    generation_time: float


class MessageDeliveredEvent(SimulationEvent):
    """Published when a C2 message is successfully delivered to its destination."""

    event_type: str = "message_delivered"
    message_type: str
    source_node: str
    destination_node: str
    delivery_time: float
    sender_personnel: str | None = None
    receiver_personnel: str | None = None


# === Cross-domain events ===


class ClassificationViolationEvent(SimulationEvent):
    """Published when a message classification is not permitted to traverse a gateway."""

    event_type: str = "classification_violation"
    message_id: str
    source_enclave: str
    destination_enclave: str
    message_classification: str


class SanitizationCompleteEvent(SimulationEvent):
    """Published when a message completes sanitization through a cross-domain gateway."""

    event_type: str = "sanitization_complete"
    sanitization_duration: float
    source_enclave: str
    destination_enclave: str


# === Aerodynamics events ===


class FuelConsumedEvent(SimulationEvent):
    """Published when a flight leg is completed and fuel has been consumed."""

    event_type: str = "fuel_consumed"
    leg_id: str
    fuel_quantity: float


class FuelInsufficientEvent(SimulationEvent):
    """Published when computed fuel-burn for a leg exceeds remaining fuel."""

    event_type: str = "fuel_insufficient"
    leg_id: str
    required_fuel: float
    available_fuel: float


# === Airdrop events ===


class ExtractionStartEvent(SimulationEvent):
    """Published when the airdrop extraction phase begins."""

    event_type: str = "extraction_start"
    drop_zone_id: str


class ExtractionCompleteEvent(SimulationEvent):
    """Published when the airdrop extraction phase completes."""

    event_type: str = "extraction_complete"
    drop_zone_id: str
    cargo_manifest: list[str]


class WeightExceededEvent(SimulationEvent):
    """Published when cargo weight exceeds the aircraft cargo weight limit."""

    event_type: str = "weight_exceeded"
    cargo_weight: float
    weight_limit: float
