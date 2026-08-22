# Implementation Plan: Modular Enterprise Simulation

## Overview

Build a modular discrete-event simulation framework from scratch using Python, SimPy, and Pydantic. The implementation proceeds bottom-up: project scaffolding → data models → core engine infrastructure → domain modules → reporting → configuration loading → integration wiring. Each task is concrete, testable, and builds on prior steps.

## Tasks

- [ ] 1. Project scaffolding and dependencies
  - [ ] 1.1 Create package structure and install dependencies
    - Create the `enterprise_sim/` package directory tree as specified in the design (engine/, models/, modules/, reporting/, config/ with `__init__.py` files)
    - Create `tests/` directory tree (unit/test_models/, unit/test_engine/, unit/test_modules/, property/, integration/)
    - Create `pyproject.toml` with dependencies: simpy, pydantic, pyyaml, hypothesis, pytest, pytest-hypothesis
    - Create a minimal `enterprise_sim/__init__.py` exposing package version
    - _Requirements: 1.1, 3.1_

- [ ] 2. Base data models (ontology-aligned hierarchy)
  - [ ] 2.1 Implement ontology-aligned base classes in `enterprise_sim/models/base.py`
    - Implement `Entity` (entity_id, entity_type), `Continuant`, `Occurrent` (timestamp)
    - Implement `InformationEntity`, `Agent`, `SpatialRegion` (latitude, longitude, altitude_ft), `Process` (start_time, end_time)
    - All classes inherit from Pydantic `BaseModel` with zero SimPy imports
    - Add field validators and constraints as specified (ge=0 for timestamps, etc.)
    - _Requirements: 3.1, 3.2_

  - [ ]* 2.2 Write property test for data model serialization round-trip
    - **Property 5: Data Model Serialization Round-Trip**
    - **Validates: Requirements 3.3, 3.5**
    - Use Hypothesis strategies to generate valid instances of each base class and verify JSON round-trip equality

  - [ ] 2.3 Implement event type definitions in `enterprise_sim/models/events.py`
    - Implement `SimulationEvent` base class extending `Occurrent` with event_type and source_module fields
    - Implement all event types: GateCompletionEvent, GateTimeoutEvent, ChainCompletionEvent, FundsExhaustedEvent, LowBalanceWarningEvent, MessageDeliveryFailureEvent, ClassificationViolationEvent, SanitizationCompleteEvent, FuelConsumedEvent, FuelInsufficientEvent, ExtractionStartEvent, ExtractionCompleteEvent, WeightExceededEvent
    - Each event carries the fields specified in the design
    - _Requirements: 3.1, 10.1_

- [ ] 3. Event bus implementation
  - [ ] 3.1 Implement EventBus in `enterprise_sim/engine/event_bus.py`
    - Implement `publish(event)` delivering synchronously to all type-matched subscribers before returning
    - Implement `subscribe(event_type, handler, subscriber_id)` for type-specific subscriptions
    - Implement `subscribe_all(handler, subscriber_id)` for wildcard subscriptions (Reporter use case)
    - Deliver events in subscription registration order
    - On subscriber exception: log error with subscriber_id and event_type, skip faulting subscriber, continue delivery to remaining subscribers
    - Discard events silently when no subscribers registered for the type
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 3.2 Write property tests for event bus guarantees
    - **Property 24: Event Bus Synchronous Delivery**
    - **Property 25: Event Bus Fault Isolation**
    - **Property 26: Event Bus Subscription Timing**
    - **Validates: Requirements 10.2, 10.3, 10.5, 10.6**
    - Use Hypothesis to generate arbitrary subscriber sets, event sequences, and fault scenarios

- [ ] 4. Module registry implementation
  - [ ] 4.1 Implement ModuleRegistry in `enterprise_sim/engine/module_registry.py`
    - Implement `register(module)` with interface validation (initialize, create_processes, finalize must exist)
    - Reject registration if module_type already exists (raise RegistrationError)
    - On validation failure, return error identifying module name and each missing/non-compliant method
    - Implement `get(module_type)`, `get_all()`
    - Implement `resolve_initialization_order()` using Kahn's algorithm for topological sort
    - Raise `CycleError` with involved modules when circular dependencies detected
    - _Requirements: 1.2, 1.5, 1.6, 13.2, 13.6_

  - [ ]* 4.2 Write property tests for module registry
    - **Property 1: Module Registration Validation Completeness**
    - **Property 3: Missing Module Detection Completeness**
    - **Property 32: Dependency Cycle Detection**
    - **Validates: Requirements 1.2, 1.5, 2.2, 2.3, 13.6**

- [ ] 5. Domain module base class
  - [ ] 5.1 Implement DomainModule abstract base class in `enterprise_sim/modules/base.py`
    - Define `DomainModule(ABC)` with ClassVar fields: `module_type: str`, `dependencies: Set[str]`
    - Define abstract methods: `initialize(env, event_bus, config)`, `create_processes(env)`, `finalize()`
    - Define optional `validate_config(config)` with default empty-list return
    - _Requirements: 1.2, 13.2_

- [ ] 6. Simulation engine lifecycle
  - [ ] 6.1 Implement SimulationEngine in `enterprise_sim/engine/simulation_engine.py`
    - Implement `__init__(registry, event_bus)` storing references
    - Implement `load_configuration(config)` validating referenced modules against registry
    - Implement `run(config)` orchestrating: initialization → execution → finalization
    - Initialize modules in topological order; finalize in reverse order
    - On initialization failure: finalize all previously-initialized modules in reverse order, raise error identifying failed module
    - On finalization exception: log error, continue finalizing remaining modules
    - Provide SimPy environment with clock starting at 0
    - Collect processes from all modules via `create_processes()` and register with `env.process()`
    - Run SimPy environment until all processes complete
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.7_

  - [ ]* 6.2 Write property tests for simulation engine lifecycle
    - **Property 29: Module Lifecycle Ordering**
    - **Property 30: Initialization Failure Cleanup**
    - **Property 31: Finalization Fault Tolerance**
    - **Validates: Requirements 13.2, 13.3, 13.4, 13.7**

- [ ] 7. Exception hierarchy
  - [ ] 7.1 Implement custom exceptions in `enterprise_sim/engine/exceptions.py`
    - Implement `SimulationError`, `ConfigurationError` (invalid_fields, violations), `RegistrationError` (module_name, missing_methods), `CycleError` (cycle_modules), `GraphValidationError` (node_name, reason)
    - _Requirements: 1.5, 1.6, 2.6, 9.3, 13.6_

- [ ] 8. Checkpoint - Core infrastructure
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Route graph data model
  - [ ] 9.1 Implement route graph models in `enterprise_sim/models/route.py`
    - Implement `RouteNode(SpatialRegion)` with name, node_type fields
    - Implement `RouteEdge(BaseModel)` with source, destination, distance_nm (gt=0), airspace_classification
    - Implement `RouteGraphConfig(BaseModel)` with nodes (min_length=2), edges (min_length=1), origin, destination
    - Add `validate_graph_structure` model_validator: unique node names, origin/destination exist, path from origin to destination exists, no non-destination dead-end nodes
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6_

  - [ ]* 9.2 Write property tests for route graph
    - **Property 6: Route Graph Round-Trip**
    - **Property 7: Route Graph Dead-End Detection**
    - **Validates: Requirements 9.2, 9.3, 9.5**

- [ ] 10. Domain-specific data models
  - [ ] 10.1 Implement authorization models in `enterprise_sim/models/authorization.py`
    - Implement `AuthorizationGate(Process)` with gate_name, duration (gt=0), timeout, status
    - Implement `AuthorizationChainConfig(BaseModel)` with chain_id, chain_type (sequential|parallel), gates (min_length=1)
    - _Requirements: 4.1, 4.7_

  - [ ] 10.2 Implement fiscal models in `enterprise_sim/models/fiscal.py`
    - Implement `FiscalAccount(Continuant)` with account_id, initial_allocation (gt=0), current_balance (ge=0), warning_threshold_pct
    - Implement `DebitRecord(Occurrent)` with account_id, amount (gt=0), cost_category, balance_after
    - Implement `FiscalConfig(BaseModel)` with accounts, cost_per_flying_hour (gt=0), cost_per_fuel_unit (gt=0)
    - _Requirements: 5.1, 5.4_

  - [ ] 10.3 Implement messaging models in `enterprise_sim/models/messaging.py`
    - Implement `C2MessageType(InformationEntity)` with name, source_node, destination_node, priority (ge=1), transmission_latency (ge=0)
    - Implement `C2MessageInstance(Occurrent)` with message_type, generation_time, transmission_start_time, delivery_time, status
    - _Requirements: 6.1, 6.3_

  - [ ] 10.4 Implement security models in `enterprise_sim/models/security.py`
    - Implement `SecurityEnclave(Continuant)` with name, classification_level
    - Implement `RoutingRule(BaseModel)` with source_enclave, destination_enclave, permitted_classifications
    - Implement `CrossDomainGatewayConfig(BaseModel)` with sanitization_latency (ge=0), enclaves (min_length=2), routing_rules
    - _Requirements: 7.1, 7.5, 7.6_

  - [ ] 10.5 Implement aerodynamics models in `enterprise_sim/models/aerodynamics.py`
    - Implement `AircraftConfig(Continuant)` with aircraft_type, lift_to_drag_ratio (gt=0), specific_fuel_consumption (gt=0), initial_fuel_weight (gt=0), max_fuel_capacity (gt=0)
    - Implement `FuelBurnResult(Occurrent)` with leg_source, leg_destination, fuel_consumed (ge=0), remaining_fuel (ge=0)
    - _Requirements: 8.4_

  - [ ] 10.6 Implement airdrop models in `enterprise_sim/models/airdrop.py`
    - Implement `AirdropPhase(str, Enum)` with EN_ROUTE, SLOWDOWN, EXTRACTION, ACCELERATION
    - Implement `CargoItem(BaseModel)` with item_id, weight (gt=0), description
    - Implement `AirdropConfig(BaseModel)` with extraction_speed (gt=0), drop_altitude_ft (gt=0), cargo_weight_limit (gt=0), phase_durations, cargo_manifest
    - Implement `AirdropState(Occurrent)` with current_phase, drop_zone_id
    - Add validator ensuring all phase durations are positive
    - _Requirements: 11.1, 11.5, 11.7_

  - [ ]* 10.7 Write property tests for configuration validation
    - **Property 4: Configuration Validation Error Aggregation**
    - **Property 20: Aircraft Parameter Validation**
    - **Validates: Requirements 2.6, 3.2, 3.4, 8.4, 8.6**

- [ ] 11. Mission configuration schema
  - [ ] 11.1 Implement MissionConfiguration in `enterprise_sim/models/mission.py`
    - Implement `MissionConfiguration(BaseModel)` with mission_id, mission_name, route_graph, aircraft, authorization_chain (optional), fiscal_parameters (optional), c2_messages (optional), cross_domain_gateway (optional), airdrop_parameters (optional), active_modules list
    - Import and reference all domain config models
    - _Requirements: 2.1, 2.4_

  - [ ]* 11.2 Write property test for mission configuration round-trip
    - **Property 2: Mission Configuration Round-Trip**
    - **Validates: Requirements 2.5**
    - Use Hypothesis to generate valid MissionConfiguration instances and verify JSON serialization round-trip

- [ ] 12. Checkpoint - Data models complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Authorization module
  - [ ] 13.1 Implement AuthorizationModule in `enterprise_sim/modules/authorization.py`
    - Extend DomainModule with module_type="authorization", dependencies=set()
    - In `initialize`: store chain config, subscribe to relevant events, set up gate_states dict
    - In `create_processes`: return SimPy processes for sequential or parallel chain execution
    - Sequential chain: process gates in order, each gate blocks until duration elapses or timeout fires
    - Parallel chain: process all gates concurrently using SimPy AllOf
    - On gate completion: publish GateCompletionEvent
    - On gate timeout: set gate to failed, cancel dependent gates, publish GateTimeoutEvent
    - On chain completion: publish ChainCompletionEvent with elapsed time
    - In `finalize`: clean up internal state
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 13.2 Write property tests for authorization module
    - **Property 8: Authorization Chain Timing**
    - **Property 9: Authorization Gate Timeout Cascading**
    - **Validates: Requirements 4.3, 4.5, 4.6**

- [ ] 14. Fiscal module
  - [ ] 14.1 Implement FiscalModule in `enterprise_sim/modules/fiscal.py`
    - Extend DomainModule with module_type="fiscal", dependencies=set()
    - In `initialize`: set up accounts dict from config, initialize cumulative_debits, subscribe to cost events
    - Implement `debit(account_id, amount, category, timestamp)` method:
      - If amount > balance: publish FundsExhaustedEvent, return False, leave balance unchanged
      - Otherwise: subtract amount, record DebitRecord, update cumulative_debits
      - If balance below warning threshold: publish LowBalanceWarningEvent
    - Implement `compute_flying_hour_cost(hours)` and `compute_fuel_cost(fuel_units)` using config rates
    - In `create_processes`: return empty list (passive module, responds to events)
    - In `finalize`: clean up
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 14.2 Write property tests for fiscal module
    - **Property 10: Fiscal Balance Invariant**
    - **Property 11: Fiscal Overdraft Prevention**
    - **Property 12: Fiscal Cost Computation**
    - **Property 13: Fiscal Low-Balance Warning**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

- [ ] 15. C2 message module
  - [ ] 15.1 Implement C2MessageModule in `enterprise_sim/modules/c2_message.py`
    - Extend DomainModule with module_type="c2_message", dependencies={"cross_domain"} (optional soft dependency)
    - In `initialize`: store message type definitions, set up message_log, obtain gateway reference if available
    - In `create_processes`: return SimPy processes for each configured message type flow
    - Message flow process: generate message instance, check destination reachability, apply transmission latency, check cross-domain routing, deliver or fail
    - On unreachable destination: publish MessageDeliveryFailureEvent, retain generation/transmission timestamps, discard message
    - On cross-domain traversal: add sanitization_latency to total delivery time
    - Record generation_time, transmission_start_time, delivery_time on each instance
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 15.2 Write property tests for C2 message module
    - **Property 14: C2 Message Delivery Timing**
    - **Property 15: C2 Message Cross-Domain Latency Composition**
    - **Property 16: C2 Message Failure Handling**
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.5, 6.6**

- [ ] 16. Cross-domain gateway module
  - [ ] 16.1 Implement CrossDomainGatewayModule in `enterprise_sim/modules/cross_domain.py`
    - Extend DomainModule with module_type="cross_domain", dependencies=set()
    - In `initialize`: store enclave definitions, build routing_rules lookup dict, store sanitization_latency
    - Implement `check_routing(classification, source_enclave, dest_enclave)` → bool
    - Implement `process_message(env, message_id, classification, source, dest)` as SimPy process:
      - If routing denied: publish ClassificationViolationEvent, return None (message discarded)
      - If permitted: yield env.timeout(sanitization_latency), publish SanitizationCompleteEvent, return latency
    - In `create_processes`: return empty list (reactive module, called by C2 module)
    - In `finalize`: clean up
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 16.2 Write property tests for cross-domain gateway
    - **Property 17: Cross-Domain Gateway Routing Rule Enforcement**
    - **Validates: Requirements 7.2, 7.3**

- [ ] 17. Aerodynamics module
  - [ ] 17.1 Implement AerodynamicsModule in `enterprise_sim/modules/aerodynamics.py`
    - Extend DomainModule with module_type="aerodynamics", dependencies={"fiscal"} (for cost debiting)
    - In `initialize`: validate aircraft parameters (all positive), store config, initialize cumulative_fuel_consumed=0
    - Implement `compute_fuel_burn(distance_nm)` using Breguet range equation as specified in design
    - In `create_processes`: return SimPy processes for each leg in route graph
    - Flight leg process: compute fuel burn, check against remaining fuel, if insufficient publish FuelInsufficientEvent, otherwise simulate flight time, update cumulative consumption, publish FuelConsumedEvent, debit fiscal account for fuel cost
    - In `finalize`: clean up
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 17.2 Write property tests for aerodynamics module
    - **Property 18: Aerodynamic Fuel Accounting Invariant**
    - **Property 19: Fuel Insufficiency Detection**
    - **Validates: Requirements 8.1, 8.2, 8.5, 8.7**

- [ ] 18. Airdrop module
  - [ ] 18.1 Implement AirdropModule in `enterprise_sim/modules/airdrop.py`
    - Extend DomainModule with module_type="airdrop", dependencies={"aerodynamics"}
    - In `initialize`: store airdrop config, validate phase durations (all > 0), set current_phase=EN_ROUTE
    - In `create_processes`: return SimPy process for airdrop sequence (triggered when aircraft reaches drop zone)
    - Airdrop sequence: check total cargo weight against limit, if exceeded publish WeightExceededEvent and remain in en_route
    - If weight valid: transition through slowdown → extraction → acceleration → en_route with configured durations
    - Publish ExtractionStartEvent at extraction phase start, ExtractionCompleteEvent at extraction phase end (with cargo manifest IDs)
    - Ensure aircraft occupies exactly one phase at any simulation time
    - In `finalize`: clean up
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [ ]* 18.2 Write property tests for airdrop module
    - **Property 21: Airdrop State Machine Exclusivity**
    - **Property 22: Airdrop Weight Limit Enforcement**
    - **Property 23: Airdrop Event Lifecycle**
    - **Validates: Requirements 11.1, 11.3, 11.4, 11.6**

- [ ] 19. Checkpoint - All domain modules
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 20. Reporter implementation
  - [ ] 20.1 Implement Reporter in `enterprise_sim/reporting/reporter.py`
    - In `__init__`: subscribe_all on event bus, initialize empty events list
    - Implement `_collect_event(event)` appending to internal list
    - Implement `get_timeline()`: return events sorted by timestamp (stable sort preserves publication order for same-timestamp events), each entry as TimelineEntry with event_type, timestamp, source_module, payload
    - Implement `get_metrics()`: compute total_duration (max timestamp), total_costs (sum of fiscal debits), message counts (sent/delivered/failed), mean_delivery_time, chain_completion_times
    - Handle zero-events case: return empty timeline and zero-value metrics
    - Implement `export(format)` delegating to formatters
    - _Requirements: 12.1, 12.2, 12.3, 12.5, 12.6, 12.7_

  - [ ] 20.2 Implement formatters in `enterprise_sim/reporting/formatters.py`
    - Implement `JSONFormatter.format(timeline, metrics)` producing JSON string output
    - Implement `CSVFormatter.format(timeline, metrics)` producing CSV string output
    - Define `TimelineEntry` and `SummaryMetrics` Pydantic models for structured output
    - _Requirements: 12.4_

  - [ ]* 20.3 Write property tests for reporter
    - **Property 27: Reporter Event Completeness and Ordering**
    - **Property 28: Reporter Metrics Computation**
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.7**

- [ ] 21. Mission configuration loader
  - [ ] 21.1 Implement configuration loader in `enterprise_sim/config/loader.py`
    - Implement `load_from_yaml(path)` and `load_from_json(path)` returning MissionConfiguration
    - Parse file content, validate against Pydantic schema, return validated model
    - On parse/validation failure: raise ConfigurationError with all invalid fields and constraint violations
    - Support both YAML and JSON formats detected by file extension
    - _Requirements: 2.4, 2.6_

- [ ] 22. Integration wiring
  - [ ] 22.1 Create main entry point in `enterprise_sim/__init__.py`
    - Export public API: SimulationEngine, ModuleRegistry, EventBus, Reporter, MissionConfiguration
    - Export all domain modules for registration
    - Create convenience `run_simulation(config_path)` function that:
      - Loads configuration from file
      - Creates EventBus, ModuleRegistry, Reporter
      - Registers all available domain modules
      - Creates SimulationEngine
      - Calls engine.run(config) and returns SimulationResult
    - _Requirements: 1.1, 1.3, 2.4, 13.1_

  - [ ] 22.2 Wire module dependencies and cross-module communication
    - Ensure Aerodynamics module debits Fiscal module on fuel consumption (via event bus subscription)
    - Ensure C2 Message module obtains CrossDomainGateway reference during initialization for latency queries
    - Ensure Airdrop module triggers based on route graph progression from Aerodynamics module
    - Verify all event subscriptions are correctly established during initialization
    - _Requirements: 6.5, 8.1, 11.2_

- [ ] 23. Checkpoint - Full system wired
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 24. Integration tests
  - [ ]* 24.1 Write integration test for full mission lifecycle
    - Create a test mission configuration with all modules active
    - Run complete simulation and verify: initialization order matches topological sort, all events captured by reporter, finalization in reverse order
    - _Requirements: 1.1, 1.3, 13.1, 13.2, 13.3_

  - [ ]* 24.2 Write integration test for multi-module interaction
    - Test authorization blocking downstream processes
    - Test end-to-end message routing through cross-domain gateway
    - Test aerodynamics fuel consumption triggering fiscal debit
    - Test airdrop triggered by route graph progression
    - _Requirements: 4.4, 6.5, 7.1, 8.1, 11.2_

- [ ] 25. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Properties 1-32)
- Unit tests validate specific examples and edge cases
- The implementation uses Python with SimPy for discrete-event simulation, Pydantic for data models, and Hypothesis for property-based testing
- All domain modules communicate exclusively through the Event Bus — no direct module-to-module calls
- The Fiscal module is passive (no SimPy processes); it exposes a `debit()` method called by other modules via event subscription

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "7.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "5.1"] },
    { "id": 3, "tasks": ["3.1", "4.1"] },
    { "id": 4, "tasks": ["3.2", "4.2", "6.1"] },
    { "id": 5, "tasks": ["6.2", "9.1"] },
    { "id": 6, "tasks": ["9.2", "10.1", "10.2", "10.3", "10.4", "10.5", "10.6"] },
    { "id": 7, "tasks": ["10.7", "11.1"] },
    { "id": 8, "tasks": ["11.2", "13.1", "14.1", "16.1"] },
    { "id": 9, "tasks": ["13.2", "14.2", "15.1", "16.2"] },
    { "id": 10, "tasks": ["15.2", "17.1"] },
    { "id": 11, "tasks": ["17.2", "18.1"] },
    { "id": 12, "tasks": ["18.2", "20.1"] },
    { "id": 13, "tasks": ["20.2", "20.3", "21.1"] },
    { "id": 14, "tasks": ["22.1"] },
    { "id": 15, "tasks": ["22.2"] },
    { "id": 16, "tasks": ["24.1", "24.2"] }
  ]
}
```
