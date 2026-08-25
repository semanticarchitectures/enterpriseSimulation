cr# Implementation Plan: Personnel Module

## Overview

Implement the Personnel Module as a new domain module that introduces named human actors into the simulation. The module follows the same bottom-up pattern as the existing framework: data models → internal components (decision processor, message router, credential checker) → module lifecycle → integration wiring. The module extends `DomainModule` and integrates with Authorization, C2 Message, Cross-Domain Gateway, and Airdrop modules via the synchronous EventBus.

## Tasks

- [x] 1. Personnel data models
  - [x] 1.1 Implement personnel core models in `enterprise_sim/models/personnel.py`
    - Implement `ReadinessState(str, Enum)` with READY, UNAVAILABLE, INCAPACITATED
    - Implement `ClearanceLevel(IntEnum)` with UNCLASSIFIED=0, SECRET=1, TOP_SECRET=2
    - Implement `CredentialType(str, Enum)` with CAC_PKI
    - Implement `AuthenticationCredential(BaseModel)` with credential_type, clearance_level, authorized_enclaves (min_length=1)
    - Implement `Personnel(Agent)` with entity_type="personnel", name (1-128 chars, not blank), role, duty_station, credential, readiness_state (default READY)
    - Add `name_not_blank` field_validator to reject whitespace-only names
    - _Requirements: 1.1, 1.2, 2.1, 2.4, 6.1_

  - [x] 1.2 Implement decision point models in `enterprise_sim/models/personnel.py`
    - Implement `DecisionType(str, Enum)` with GO_NO_GO, APPROVE_DENY, VERIFY_REJECT
    - Implement `DecisionOutcome(str, Enum)` with APPROVE, DENY, DEFER
    - Implement `DecisionPointConfig(BaseModel)` with decision_id (min_length=1), assigned_role, decision_type, deliberation_duration (gt=0), timeout (optional, gt=0)
    - _Requirements: 3.1, 3.8_

  - [x] 1.3 Implement configuration models in `enterprise_sim/models/personnel.py`
    - Implement `MessageRoutingRule(BaseModel)` with message_type, destination_role
    - Implement `AuthorizationGateBinding(BaseModel)` with gate_name, approving_role
    - Implement `PersonnelConfig(BaseModel)` with roster (1-100 entries), message_routing_rules, authorization_gate_bindings, decision_points
    - Add `unique_names` validator on roster to reject duplicates
    - _Requirements: 1.2, 1.3, 1.5, 4.1, 5.1, 9.1, 9.5_

  - [x] 1.4 Write property tests for personnel data model round-trip
    - **Property 1: Personnel Configuration Round-Trip**
    - **Validates: Requirements 1.9, 9.4**
    - Use Hypothesis strategies to generate valid PersonnelConfig instances and verify JSON serialization round-trip equality

  - [x] 1.5 Write property tests for roster validation
    - **Property 2: Roster Unique Name Validation**
    - **Property 3: Roster Size Validation**
    - **Validates: Requirements 1.2, 1.3, 1.5, 1.6**
    - Generate rosters with duplicate names and verify rejection; generate rosters of valid/invalid sizes

- [x] 2. Personnel event types
  - [x] 2.1 Implement personnel events in `enterprise_sim/models/personnel_events.py`
    - Implement readiness events: `ReadinessChangedEvent`, `AssignmentRejectedEvent`
    - Implement decision events: `DecisionRenderedEvent`, `DecisionDeferredEvent`, `DecisionTimeoutEvent`, `MissionBlockedEvent`
    - Implement message routing events: `MessageReceivedEvent`, `MessageUnroutedEvent`, `MessageDelayedEvent`, `MessageUnroutableEvent`, `SenderUnresolvedEvent`, `ReceiverUnresolvedEvent`
    - Implement authorization gate events: `GateBlockedEvent`, `GateTimeoutPersonnelEvent`
    - Implement credential events: `AccessDeniedEvent`, `AuthenticationSuccessEvent`, `ClearanceInsufficientEvent`
    - Implement airdrop integration events: `CargoReleaseConfirmedEvent`, `CargoReleaseDeniedEvent`
    - All events extend `SimulationEvent` with event_type and source_module="personnel"
    - _Requirements: 2.2, 2.3, 3.4, 3.5, 3.6, 3.7, 4.3, 4.4, 4.5, 4.7, 5.2, 5.4, 5.6, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3, 8.4, 8.5_

- [x] 3. Checkpoint - Data models complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Decision point processor
  - [x] 4.1 Implement DecisionPointProcessor in `enterprise_sim/modules/personnel/decision_processor.py`
    - Implement `get_assigned_personnel(decision_id, roster)` to resolve the first READY personnel member in roster order whose role matches the decision point's assigned_role
    - Implement `trigger_decision(decision_id, env, event_bus, roster)` as a SimPy process:
      - Resolve assigned personnel; if none READY, publish DecisionDeferredEvent, wait for readiness change or timeout
      - If timeout expires without READY personnel, set outcome to DEFER, publish DecisionTimeoutEvent
      - If READY personnel found, yield env.timeout(deliberation_duration)
      - If personnel becomes non-READY during deliberation, set outcome to DEFER, publish DecisionDeferredEvent
      - On completion, publish DecisionRenderedEvent with outcome
      - If outcome is DENY, publish MissionBlockedEvent
    - Store decision_point configs and track in-progress decisions
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 2.3, 2.6_

  - [x] 4.2 Write property tests for decision point processor
    - **Property 10: Decision Point Role Resolution (Roster Order)**
    - **Property 11: Decision Evaluation Timing and Event**
    - **Property 12: DENY Outcome Publishes Mission-Blocked Event**
    - **Property 13: Decision Deferral and Timeout**
    - **Property 14: Duplicate Decision ID Validation**
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.9**

- [x] 5. Message router
  - [x] 5.1 Implement MessageRouter in `enterprise_sim/modules/personnel/message_router.py`
    - Implement `resolve_recipient(message_type, destination_node, roster, routing_rules)` to find the personnel member at duty_station matching routing rule's destination_role
    - Implement `resolve_sender(message_type, source_node, roster, routing_rules)` to find the personnel member at source node matching the routing rule
    - Implement `queue_message(message_type, personnel_name, timestamp)` to store messages for delayed delivery
    - Implement `deliver_queued_messages(personnel_name, event_bus, timestamp)` to flush queue when personnel becomes READY
    - On message delivery: publish MessageReceivedEvent
    - On no routing rule: publish MessageUnroutedEvent
    - On non-READY recipient: queue message, publish MessageDelayedEvent
    - On no matching personnel at destination: publish MessageUnroutableEvent
    - On no matching sender at source: publish SenderUnresolvedEvent
    - On no matching receiver at destination: publish ReceiverUnresolvedEvent
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 8.2, 8.3, 8.4, 8.5_

  - [x] 5.2 Write property tests for message router
    - **Property 15: Message Routing Rule Cross-Reference Validation**
    - **Property 16: Message Routing to Correct Personnel**
    - **Property 17: Missing Routing Rule Publishes Unrouted Event**
    - **Property 18: Non-READY Recipient Queues Message**
    - **Property 19: No Matching Personnel Publishes Unroutable Event**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7**

- [x] 6. Credential checker
  - [x] 6.1 Implement CredentialChecker in `enterprise_sim/modules/personnel/credential_checker.py`
    - Implement `check_enclave_access(personnel, enclave_name)` returning bool based on authorized_enclaves list
    - Implement `check_clearance_level(personnel, required_level)` returning bool based on IntEnum comparison
    - Implement `authenticate(personnel, enclave_name, enclave_classification, event_bus, timestamp)`:
      - If enclave not in authorized_enclaves: publish AccessDeniedEvent, return False
      - If clearance_level < enclave_classification: publish ClearanceInsufficientEvent, return False
      - Otherwise: publish AuthenticationSuccessEvent, return True
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 6.2 Write property tests for credential checker
    - **Property 23: Enclave Access Enforcement**
    - **Property 24: Clearance Level Ordering Enforcement**
    - **Property 25: Authorized Enclaves Cross-Reference Validation**
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.5, 6.6**

- [x] 7. Checkpoint - Internal components complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Personnel module lifecycle
  - [x] 8.1 Implement PersonnelModule in `enterprise_sim/modules/personnel/__init__.py`
    - Create `enterprise_sim/modules/personnel/` package with `__init__.py`
    - Implement `PersonnelModule(DomainModule)` with module_type="personnel", dependencies={"authorization", "c2_message", "cross_domain", "airdrop"}
    - Implement `validate_config(config)`:
      - Validate roster (unique names, size 1-100, valid roles, valid duty stations against route graph)
      - Validate message routing rules (message types exist in c2_messages, roles exist in roster)
      - Validate authorization gate bindings (gate names exist in authorization_chain, roles exist in roster)
      - Validate decision points (unique decision_ids, positive deliberation_duration, assigned_roles exist in roster)
      - Validate credentials (authorized_enclaves exist in cross_domain_gateway config)
      - Return aggregated list of all errors found
    - Implement `initialize(env, event_bus, config)`:
      - Store roster, build internal lookup structures
      - Instantiate DecisionPointProcessor, MessageRouter, CredentialChecker
      - Subscribe to GateActivationEvent, MessageDeliveredEvent, ExtractionReadyEvent
    - Implement `create_processes(env)`:
      - Create SimPy processes for active decision points
      - Create message queue delivery process
    - Implement `finalize()`:
      - Clear roster state, queues, and internal references
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.1, 2.4, 9.1, 9.2, 9.3_

  - [x] 8.2 Write property tests for configuration validation
    - **Property 4: Role Validation Against Configuration**
    - **Property 5: Duty Station Cross-Reference Validation**
    - **Property 20: Authorization Gate Binding Validation**
    - **Property 29: Configuration Validation Error Aggregation**
    - **Validates: Requirements 1.4, 1.7, 1.8, 5.1, 5.5, 9.2_

- [x] 9. Readiness state management
  - [x] 9.1 Implement readiness state transitions in `enterprise_sim/modules/personnel/__init__.py`
    - Implement `change_readiness(personnel_name, new_state, timestamp)`:
      - Validate new_state is a valid ReadinessState; reject and return error if invalid
      - Update personnel readiness_state
      - Publish ReadinessChangedEvent with personnel name, previous state, new state, timestamp
      - If personnel was assigned to an in-progress decision, set outcome to DEFER and publish DecisionDeferredEvent
      - Trigger delivery of queued messages if transitioning to READY
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 9.2 Write property tests for readiness state transitions
    - **Property 6: Readiness State Transition Event Publication**
    - **Property 7: Non-READY Personnel Rejects Decision Assignment**
    - **Property 8: Invalid Readiness State Transition Preserves Current State**
    - **Property 9: In-Progress Decision Deferred on Readiness Loss**
    - **Validates: Requirements 2.2, 2.3, 2.5, 2.6**

- [x] 10. Checkpoint - Module lifecycle complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Authorization gate integration
  - [x] 11.1 Implement authorization gate binding logic in `enterprise_sim/modules/personnel/__init__.py`
    - Subscribe to gate activation events from the Authorization module
    - On gate activation: look up bound role from AuthorizationGateBinding
    - If READY personnel with role exists: allow gate to proceed, record approving personnel name
    - If no READY personnel with role: publish GateBlockedEvent, block until READY or gate timeout
    - On gate timeout while blocked: publish GateTimeoutPersonnelEvent, gate transitions to failed
    - On gate approval: ensure gate completion event includes approving personnel name
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 11.2 Write property tests for authorization gate binding
    - **Property 21: Gate Personnel Readiness Check**
    - **Property 22: Gate Approval Records Personnel**
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.6**

- [x] 12. Airdrop integration
  - [x] 12.1 Implement loadmaster decision logic in `enterprise_sim/modules/personnel/__init__.py`
    - Subscribe to ExtractionReadyEvent from the Airdrop module
    - On extraction ready: trigger DecisionPoint for Loadmaster role at drop zone node
    - If APPROVE: publish CargoReleaseConfirmedEvent, enable airdrop extraction to proceed
    - If DENY: publish CargoReleaseDeniedEvent, extraction does NOT initiate
    - If no READY Loadmaster: block extraction, publish DecisionDeferredEvent, wait for READY or timeout
    - If timeout expires: set outcome to DEFER, publish DecisionTimeoutEvent, extraction does NOT initiate
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 12.2 Write property tests for airdrop integration
    - **Property 26: Loadmaster Decision Gates Extraction**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**

- [x] 13. C2 message integration
  - [x] 13.1 Implement message routing integration in `enterprise_sim/modules/personnel/__init__.py`
    - Subscribe to C2 message delivery events
    - On message delivery: resolve sender and receiver personnel via MessageRouter
    - Extend message instance with sender_personnel and receiver_personnel fields
    - If sender unresolved: publish SenderUnresolvedEvent, set sender_personnel to null
    - If receiver unresolved: publish ReceiverUnresolvedEvent, set receiver_personnel to null
    - Route message to recipient based on MessageRoutingRule and duty station
    - Record sender/receiver on message delivery record for Reporter output
    - _Requirements: 4.2, 4.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 13.2 Write property tests for message routing integration
    - **Property 27: Sender Personnel Resolution**
    - **Property 28: Receiver Personnel Resolution**
    - **Validates: Requirements 8.2, 8.3, 8.4, 8.5**

- [x] 14. Cross-domain credential integration
  - [x] 14.1 Implement credential-based access control in `enterprise_sim/modules/personnel/__init__.py`
    - Before cross-domain message transit: check personnel credentials via CredentialChecker
    - If access denied (enclave not authorized or clearance insufficient): block communication, discard message
    - If access granted: publish AuthenticationSuccessEvent, allow transit
    - _Requirements: 6.2, 6.3, 6.4, 6.5_

- [x] 15. Checkpoint - All integrations complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. Mission configuration extension
  - [x] 16.1 Extend MissionConfiguration with personnel section in `enterprise_sim/models/mission.py`
    - Import PersonnelConfig from personnel models
    - Add `personnel_parameters: Optional[PersonnelConfig] = None` field to MissionConfiguration
    - Ensure active_modules list validation checks for personnel section presence when "personnel" is active
    - _Requirements: 9.1, 9.3_

- [x] 17. Module registration and wiring
  - [x] 17.1 Register PersonnelModule in the module system
    - Add PersonnelModule import to `enterprise_sim/modules/__init__.py`
    - Register PersonnelModule in the `run_simulation()` convenience function in `enterprise_sim/__init__.py`
    - Ensure topological sort places personnel after authorization, c2_message, cross_domain, airdrop
    - _Requirements: 1.1, 9.1_

- [x] 18. Integration tests
  - [x] 18.1 Write integration test for personnel module lifecycle
    - Test initialization ordering (personnel initializes after its dependencies)
    - Test finalization in reverse order
    - Test validate_config returns aggregated errors for multiple violations
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 9.2_

  - [x] 18.2 Write integration test for decision-airdrop flow
    - Test Loadmaster APPROVE enables extraction
    - Test Loadmaster DENY blocks extraction
    - Test no READY Loadmaster defers extraction
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 18.3 Write integration test for message routing end-to-end
    - Test message routed to correct personnel by role and duty station
    - Test message queued when recipient non-READY, delivered on state change
    - Test sender/receiver resolution recorded on delivery record
    - _Requirements: 4.2, 4.3, 4.5, 4.6, 8.1, 8.2, 8.3, 8.6_

  - [x] 18.4 Write integration test for credential-based access control
    - Test authentication success allows cross-domain transit
    - Test access denied blocks and discards message
    - Test clearance insufficient blocks access
    - _Requirements: 6.2, 6.3, 6.4, 6.5_

- [x] 19. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Properties 1-29)
- Unit tests validate specific examples and edge cases
- The implementation uses Python with SimPy for discrete-event simulation, Pydantic for data models, and Hypothesis for property-based testing
- The Personnel module depends on authorization, c2_message, cross_domain, and airdrop modules — these are already fully implemented
- All cross-module communication uses the synchronous EventBus (publish/subscribe)
- The personnel module package lives at `enterprise_sim/modules/personnel/` with internal components as submodules

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["1.4", "1.5"] },
    { "id": 3, "tasks": ["4.1", "5.1", "6.1"] },
    { "id": 4, "tasks": ["4.2", "5.2", "6.2"] },
    { "id": 5, "tasks": ["8.1"] },
    { "id": 6, "tasks": ["8.2", "9.1"] },
    { "id": 7, "tasks": ["9.2", "11.1", "12.1", "13.1", "14.1"] },
    { "id": 8, "tasks": ["11.2", "12.2", "13.2", "16.1"] },
    { "id": 9, "tasks": ["17.1"] },
    { "id": 10, "tasks": ["18.1", "18.2", "18.3", "18.4"] }
  ]
}
```
