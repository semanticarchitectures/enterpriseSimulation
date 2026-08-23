"""Personnel module event type definitions.

This module defines typed events published by the Personnel module to the Event Bus.
All event classes extend SimulationEvent with source_module="personnel".

Event categories:
    - Readiness: ReadinessChangedEvent, AssignmentRejectedEvent
    - Decision: DecisionRenderedEvent, DecisionDeferredEvent, DecisionTimeoutEvent, MissionBlockedEvent
    - Message Routing: MessageReceivedEvent, MessageUnroutedEvent, MessageDelayedEvent,
                       MessageUnroutableEvent, SenderUnresolvedEvent, ReceiverUnresolvedEvent
    - Authorization Gate: GateBlockedEvent, GateTimeoutPersonnelEvent
    - Credential/Access: AccessDeniedEvent, AuthenticationSuccessEvent, ClearanceInsufficientEvent
    - Airdrop Integration: CargoReleaseConfirmedEvent, CargoReleaseDeniedEvent
"""

from typing import Optional

from enterprise_sim.models.events import SimulationEvent


# === Readiness events ===


class ReadinessChangedEvent(SimulationEvent):
    """Published when a personnel member's readiness state changes."""

    event_type: str = "readiness_changed"
    source_module: str = "personnel"
    personnel_name: str
    previous_state: str
    new_state: str


class AssignmentRejectedEvent(SimulationEvent):
    """Published when a decision assignment is rejected due to readiness."""

    event_type: str = "assignment_rejected"
    source_module: str = "personnel"
    personnel_name: str
    readiness_state: str
    decision_id: str


# === Decision events ===


class DecisionRenderedEvent(SimulationEvent):
    """Published when a decision point evaluation completes."""

    event_type: str = "decision_rendered"
    source_module: str = "personnel"
    decision_id: str
    personnel_name: str
    decision_type: str
    outcome: str


class DecisionDeferredEvent(SimulationEvent):
    """Published when a decision is deferred (no READY personnel or state change)."""

    event_type: str = "decision_deferred"
    source_module: str = "personnel"
    decision_id: str
    personnel_name: Optional[str] = None
    readiness_state: Optional[str] = None
    assigned_role: Optional[str] = None


class DecisionTimeoutEvent(SimulationEvent):
    """Published when a decision point timeout expires."""

    event_type: str = "decision_timeout"
    source_module: str = "personnel"
    decision_id: str
    assigned_role: str


class MissionBlockedEvent(SimulationEvent):
    """Published when a DENY decision blocks mission flow."""

    event_type: str = "mission_blocked"
    source_module: str = "personnel"
    decision_id: str
    blocking_personnel: str
    reason: str


# === Message routing events ===


class MessageReceivedEvent(SimulationEvent):
    """Published when a message is successfully routed to personnel."""

    event_type: str = "message_received"
    source_module: str = "personnel"
    message_type: str
    recipient_name: str
    recipient_role: str


class MessageUnroutedEvent(SimulationEvent):
    """Published when no routing rule exists for a delivered message."""

    event_type: str = "message_unrouted"
    source_module: str = "personnel"
    message_type: str
    destination_node: str


class MessageDelayedEvent(SimulationEvent):
    """Published when message delivery is delayed due to readiness."""

    event_type: str = "message_delayed"
    source_module: str = "personnel"
    message_type: str
    personnel_name: str
    readiness_state: str


class MessageUnroutableEvent(SimulationEvent):
    """Published when no personnel at destination matches routing rule."""

    event_type: str = "message_unroutable"
    source_module: str = "personnel"
    message_type: str
    expected_role: str
    destination_node: str


class SenderUnresolvedEvent(SimulationEvent):
    """Published when sender personnel cannot be resolved."""

    event_type: str = "sender_unresolved"
    source_module: str = "personnel"
    message_type: str
    source_node: str


class ReceiverUnresolvedEvent(SimulationEvent):
    """Published when receiver personnel cannot be resolved."""

    event_type: str = "receiver_unresolved"
    source_module: str = "personnel"
    message_type: str
    destination_node: str


# === Authorization gate events ===


class GateBlockedEvent(SimulationEvent):
    """Published when a gate is blocked waiting for READY personnel."""

    event_type: str = "gate_blocked"
    source_module: str = "personnel"
    gate_name: str
    required_role: str


class GateTimeoutPersonnelEvent(SimulationEvent):
    """Published when a gate times out waiting for personnel."""

    event_type: str = "gate_timeout_personnel"
    source_module: str = "personnel"
    gate_name: str
    required_role: str


# === Credential/access events ===


class AccessDeniedEvent(SimulationEvent):
    """Published when personnel credential does not authorize enclave access."""

    event_type: str = "access_denied"
    source_module: str = "personnel"
    personnel_name: str
    credential_type: str
    denied_enclave: str


class AuthenticationSuccessEvent(SimulationEvent):
    """Published when personnel successfully authenticates to an enclave."""

    event_type: str = "authentication_success"
    source_module: str = "personnel"
    personnel_name: str
    enclave_name: str


class ClearanceInsufficientEvent(SimulationEvent):
    """Published when personnel clearance is below enclave classification."""

    event_type: str = "clearance_insufficient"
    source_module: str = "personnel"
    personnel_name: str
    personnel_clearance: str
    enclave_name: str
    required_classification: str


# === Airdrop integration events ===


class CargoReleaseConfirmedEvent(SimulationEvent):
    """Published when loadmaster approves cargo release."""

    event_type: str = "cargo_release_confirmed"
    source_module: str = "personnel"
    loadmaster_name: str
    drop_zone_node: str


class CargoReleaseDeniedEvent(SimulationEvent):
    """Published when loadmaster denies cargo release."""

    event_type: str = "cargo_release_denied"
    source_module: str = "personnel"
    loadmaster_name: str
    drop_zone_node: str
