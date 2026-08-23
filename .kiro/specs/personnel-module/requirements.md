# Requirements Document

## Introduction

This document specifies requirements for the Personnel Module, a new Domain_Module that models individual personnel involved in mission execution. The module introduces personnel as named agents with roles, decision authorities, communication responsibilities, authentication credentials, and readiness states. Personnel are tied to mission decision points (go/no-go, approve/deny, verify/reject), message routing, network authentication, and cargo operations. The module integrates with the existing Authorization, C2 Message, Cross-Domain Gateway, and Airdrop modules to bind abstract gates, messages, and actions to specific human actors.

The motivating scenario is a C-130 humanitarian airdrop mission from Hickam AFB to northwest Luzon with 5 aircrew members and 4 ground personnel.

## Glossary

- **Personnel_Module**: A Domain_Module responsible for modeling individual personnel, their roles, decisions, communications, authentication credentials, and readiness states within a mission simulation
- **Personnel**: An Agent (extending the BFO-aligned Continuant base class) representing a named individual with a role, duty station, decision authority scope, authentication credentials, and readiness state
- **Role**: A classification that determines a Personnel member's decision authority, message routing responsibilities, and system access permissions (e.g., Pilot, Loadmaster, AOC_Commander)
- **Decision_Point**: A simulation event where a specific Personnel member exercises authority to approve, deny, or defer an action that affects mission flow
- **Decision_Outcome**: The result of a Decision_Point evaluation: one of APPROVE, DENY, or DEFER
- **Readiness_State**: The current availability status of a Personnel member: one of READY, UNAVAILABLE, or INCAPACITATED
- **Authentication_Credential**: A set of attributes (credential type, clearance level, authorized enclaves) that determine a Personnel member's access to security enclaves via the Cross_Domain_Gateway
- **Credential_Type**: The authentication mechanism used by a Personnel member (e.g., CAC_PKI)
- **Duty_Station**: The location identifier where a Personnel member is assigned, corresponding to a node in the Route_Graph or an external command node
- **Message_Routing_Rule**: A mapping from a C2 message type to the Personnel Role responsible for handling that message at its destination
- **Authorization_Gate_Binding**: A mapping from an Authorization_Module gate to the specific Personnel Role authorized to approve that gate
- **Personnel_Roster**: The complete set of Personnel defined in the Mission_Configuration for a given mission

## Requirements

### Requirement 1: Personnel Data Model

**User Story:** As a simulation developer, I want to define personnel as typed agents with roles, credentials, and duty stations, so that the simulation can reference specific individuals in decision and communication flows.

#### Acceptance Criteria

1. THE Personnel_Module SHALL model each Personnel as an Agent with fields: name (non-empty string, 1 to 128 characters), role, duty_station, credential (Authentication_Credential), and readiness_state
2. THE Personnel_Module SHALL accept a Personnel_Roster from the Mission_Configuration containing one or more Personnel definitions, up to a maximum of 100 Personnel entries
3. WHEN a Personnel_Roster is loaded, THE Personnel_Module SHALL validate that each Personnel has a unique name within the roster
4. WHEN a Personnel_Roster is loaded, THE Personnel_Module SHALL validate that each Personnel role is a recognized Role value defined in the Mission_Configuration
5. IF a Personnel_Roster contains duplicate names, THEN THE Personnel_Module SHALL return a validation error identifying the duplicated name
6. IF a Personnel_Roster is empty or absent from the Mission_Configuration, THEN THE Personnel_Module SHALL return a validation error indicating that at least one Personnel is required
7. IF a Personnel_Roster contains a Personnel with a role value not defined in the Mission_Configuration, THEN THE Personnel_Module SHALL return a validation error identifying the Personnel name and the unrecognized role value
8. WHEN a Personnel_Roster is loaded, THE Personnel_Module SHALL validate that each Personnel duty_station references a node name present in the Route_Graph, and return a validation error identifying the Personnel name and unrecognized duty_station if the reference is invalid
9. FOR ALL valid Personnel_Roster definitions, serializing to JSON then deserializing SHALL produce a roster with field-by-field equality to the original (round-trip property)

### Requirement 2: Personnel Readiness States

**User Story:** As a mission planner, I want to model personnel availability and readiness, so that the simulation reflects how personnel unavailability impacts mission execution.

#### Acceptance Criteria

1. THE Personnel_Module SHALL maintain a Readiness_State for each Personnel member with valid states: READY, UNAVAILABLE, INCAPACITATED
2. WHEN a Personnel member's Readiness_State changes, THE Personnel_Module SHALL publish a readiness-changed event to the Event_Bus containing the personnel name, previous state, new state, and simulation timestamp
3. IF a Decision_Point assignment is attempted for a Personnel member in UNAVAILABLE or INCAPACITATED state, THEN THE Personnel_Module SHALL reject the assignment and publish an assignment-rejected event to the Event_Bus containing the personnel name, current Readiness_State, and the decision_id
4. THE Personnel_Module SHALL initialize each Personnel member's Readiness_State to the value specified in the Mission_Configuration, defaulting to READY when not specified
5. IF a Readiness_State transition is requested to an unrecognized state value, THEN THE Personnel_Module SHALL reject the transition, preserve the Personnel member's current Readiness_State unchanged, and return an error identifying the invalid state value
6. IF a Personnel member's Readiness_State transitions to UNAVAILABLE or INCAPACITATED while that member is the assigned actor for an in-progress Decision_Point, THEN THE Personnel_Module SHALL set the Decision_Point outcome to DEFER and publish a decision-deferred event to the Event_Bus containing the decision_id, personnel name, and the new Readiness_State

### Requirement 3: Decision Point Modeling

**User Story:** As an operations analyst, I want to model personnel decisions that gate mission flow, so that the simulation captures how individual authority affects mission timelines and outcomes.

#### Acceptance Criteria

1. THE Personnel_Module SHALL model each Decision_Point with fields: decision_id (unique within the configuration), assigned_role, decision_type (GO_NO_GO, APPROVE_DENY, VERIFY_REJECT), configurable deliberation_duration in simulation time units as a positive numeric value, and outcome (APPROVE, DENY, DEFER)
2. WHEN a Decision_Point is triggered, THE Personnel_Module SHALL identify the Personnel member whose role matches the assigned_role and whose Readiness_State is READY; if multiple Personnel members match, the module SHALL select the first member in Personnel_Roster order
3. WHEN a Decision_Point is evaluated, THE Personnel_Module SHALL consume the configured deliberation_duration as simulation time before publishing the outcome
4. WHEN a Decision_Point evaluation completes, THE Personnel_Module SHALL publish a decision-rendered event to the Event_Bus containing the decision_id, personnel name, decision_type, outcome, and simulation timestamp
5. IF a Decision_Point outcome is DENY, THEN THE Personnel_Module SHALL publish a mission-blocked event to the Event_Bus containing the decision_id, blocking personnel name, and reason
6. IF no Personnel member with the assigned_role is in READY state when a Decision_Point is triggered, THEN THE Personnel_Module SHALL publish a decision-deferred event to the Event_Bus and delay evaluation until a matching Personnel member transitions to READY or a configurable timeout expires
7. IF a Decision_Point timeout expires without a READY personnel member, THEN THE Personnel_Module SHALL set the outcome to DEFER and publish a decision-timeout event to the Event_Bus containing the decision_id and the assigned_role
8. IF a configured deliberation_duration is zero or negative, THEN THE Personnel_Module SHALL reject the configuration and return a validation error identifying the invalid Decision_Point
9. IF duplicate decision_id values exist in the configuration, THEN THE Personnel_Module SHALL return a validation error identifying the duplicated decision_id

### Requirement 4: Role-Based Message Routing

**User Story:** As a communications analyst, I want C2 messages routed to specific personnel based on role, so that the simulation models realistic message handling where different personnel are responsible for different message types.

#### Acceptance Criteria

1. THE Personnel_Module SHALL accept Message_Routing_Rules from the Mission_Configuration, where each rule maps a C2 message type name to a destination Personnel Role, and SHALL return a validation error if a rule references a message type name not defined in the c2_messages configuration or a role not present in the Personnel_Roster
2. WHEN a C2 message is delivered to a destination node, THE Personnel_Module SHALL identify the Personnel member at that duty_station whose role matches the Message_Routing_Rule for the message type
3. WHEN a message is routed to a Personnel member, THE Personnel_Module SHALL publish a message-received event to the Event_Bus containing the message type, recipient personnel name, recipient role, and simulation timestamp
4. IF no Message_Routing_Rule exists for a delivered message type, THEN THE Personnel_Module SHALL accept the message without role-based filtering and publish a message-unrouted event to the Event_Bus containing the message type, destination node, and simulation timestamp
5. IF the designated Personnel member for a message is not in READY state, THEN THE Personnel_Module SHALL publish a message-delayed event to the Event_Bus containing the message type, personnel name, current readiness state, and simulation timestamp, and queue the message for delivery when the Personnel member transitions to READY
6. WHEN a queued message is delivered after a Personnel member transitions to READY, THE Personnel_Module SHALL publish a message-received event reflecting the actual delivery timestamp
7. IF no Personnel member at the destination node has a role matching the Message_Routing_Rule for the delivered message type, THEN THE Personnel_Module SHALL publish a message-unroutable event to the Event_Bus containing the message type, expected role, and destination node

### Requirement 5: Authorization Gate Binding

**User Story:** As a mission planner, I want authorization gates mapped to specific approving personnel, so that the simulation reflects real-world authority chains where named individuals hold approval power.

#### Acceptance Criteria

1. THE Personnel_Module SHALL accept Authorization_Gate_Bindings from the Mission_Configuration, where each binding maps an authorization gate name to a Personnel Role, and SHALL return a validation error if a binding references a gate name not defined in the authorization_chain configuration
2. WHEN an authorization gate becomes active, THE Personnel_Module SHALL verify that a Personnel member with the bound Role is in READY state
3. WHEN an authorization gate is approved, THE Personnel_Module SHALL record the approving personnel name on the gate completion event published to the Event_Bus
4. IF the bound Personnel member is not in READY state when a gate becomes active, THEN THE Personnel_Module SHALL publish a gate-blocked event to the Event_Bus containing the gate name, required role, and simulation timestamp, and block gate processing until a Personnel member with the required role transitions to READY or the gate's configured timeout expires
5. IF an Authorization_Gate_Binding references a role not present in the Personnel_Roster, THEN THE Personnel_Module SHALL return a validation error identifying the unmatched gate and role
6. IF the gate's configured timeout expires while blocked waiting for a READY Personnel member, THEN THE Personnel_Module SHALL publish a gate-timeout event to the Event_Bus containing the gate name and required role, and the gate SHALL transition to a failed state

### Requirement 6: Network Authentication and Access Control

**User Story:** As a security architect, I want personnel authentication credentials to determine network access, so that the simulation models realistic per-person access control to classified networks.

#### Acceptance Criteria

1. THE Personnel_Module SHALL model each Authentication_Credential with fields: credential_type (e.g., CAC_PKI), clearance_level (one of UNCLASSIFIED, SECRET, TOP_SECRET ordered by increasing privilege), and authorized_enclaves (list of 1 or more enclave names the credential grants access to)
2. WHEN a Personnel member initiates a communication that traverses the Cross_Domain_Gateway, THE Personnel_Module SHALL verify that the member's Authentication_Credential includes the destination enclave in authorized_enclaves
3. IF a Personnel member's credential does not authorize access to the required enclave, THEN THE Personnel_Module SHALL block the communication, publish an access-denied event to the Event_Bus containing the personnel name, credential_type, denied enclave, and simulation timestamp, and discard the message
4. WHEN a Personnel member successfully authenticates to an enclave, THE Personnel_Module SHALL publish an authentication-success event to the Event_Bus containing the personnel name, enclave name, and simulation timestamp
5. IF a Personnel member's clearance_level is below the enclave's classification_level according to the ordering UNCLASSIFIED < SECRET < TOP_SECRET, THEN THE Personnel_Module SHALL block access and publish a clearance-insufficient event to the Event_Bus containing the personnel name, personnel clearance_level, enclave name, and required classification_level
6. THE Personnel_Module SHALL validate at initialization that each Personnel member's authorized_enclaves reference enclave names defined in the Cross_Domain_Gateway configuration, and return a validation error identifying the personnel name and each unrecognized enclave name

### Requirement 7: Personnel-Airdrop Integration

**User Story:** As an operations analyst, I want the loadmaster to explicitly confirm cargo release, so that the simulation models the human-in-the-loop requirement for airdrop execution.

#### Acceptance Criteria

1. WHEN the Airdrop_Module extraction phase is ready to begin, THE Personnel_Module SHALL require a Decision_Point evaluation from the Personnel member with the Loadmaster role at the drop zone node before extraction proceeds
2. WHEN the Loadmaster Decision_Point outcome is APPROVE, THE Personnel_Module SHALL publish a cargo-release-confirmed event to the Event_Bus containing the loadmaster name, drop zone node, and simulation timestamp, enabling the Airdrop_Module to proceed with extraction
3. IF the Loadmaster Decision_Point outcome is DENY, THEN THE Personnel_Module SHALL publish a cargo-release-denied event to the Event_Bus containing the loadmaster name, drop zone node, and simulation timestamp, and the Airdrop_Module extraction sequence SHALL NOT initiate
4. WHILE no Personnel member with the Loadmaster role is in READY state at the drop zone node, THE Personnel_Module SHALL block the airdrop extraction sequence from initiating and publish a decision-deferred event to the Event_Bus containing the required role and drop zone node
5. IF a Decision_Point timeout expires while waiting for a Loadmaster in READY state, THEN THE Personnel_Module SHALL set the Decision_Point outcome to DEFER and publish a decision-timeout event to the Event_Bus, and the Airdrop_Module extraction sequence SHALL NOT initiate

### Requirement 8: Personnel Communication Addressing

**User Story:** As a communications analyst, I want messages addressed to and from specific personnel, so that the simulation captures realistic sender/receiver relationships in C2 information flows.

#### Acceptance Criteria

1. THE Personnel_Module SHALL extend C2 message instances with sender_personnel and receiver_personnel fields identifying the Personnel members involved in the communication
2. WHEN a C2 message is generated, THE Personnel_Module SHALL resolve the sender_personnel from the Personnel member at the source node whose role matches the Message_Routing_Rule for that message type
3. WHEN a C2 message is delivered, THE Personnel_Module SHALL resolve the receiver_personnel from the Personnel member at the destination node whose role matches the Message_Routing_Rule for that message type
4. IF no Personnel member at the source node has a role matching the routing rule, THEN THE Personnel_Module SHALL publish a sender-unresolved event to the Event_Bus containing the message type, source node, and simulation timestamp, and set sender_personnel to null on the message instance
5. IF no Personnel member at the destination node has a role matching the routing rule, THEN THE Personnel_Module SHALL publish a receiver-unresolved event to the Event_Bus containing the message type, destination node, and simulation timestamp, and set receiver_personnel to null on the message instance
6. THE Personnel_Module SHALL record sender_personnel and receiver_personnel on the message delivery record for inclusion in Reporter output

### Requirement 9: Mission Configuration Schema Extension

**User Story:** As a simulation operator, I want to define personnel, their roles, and integration bindings in the mission configuration file, so that different mission scenarios can specify different personnel compositions without code changes.

#### Acceptance Criteria

1. THE Mission_Configuration SHALL accept a personnel section containing the Personnel_Roster, Message_Routing_Rules, Authorization_Gate_Bindings, and Decision_Points
2. THE Personnel_Module SHALL validate the personnel configuration section during the initialization phase and return aggregated errors for all constraint violations found, including cross-reference violations between the personnel section and other configuration sections (authorization_chain, c2_messages, cross_domain_gateway)
3. IF the personnel section is absent and the Personnel_Module is listed in active_modules, THEN THE Personnel_Module SHALL return a validation error indicating that the personnel configuration section is required
4. FOR ALL valid personnel configuration sections, parsing then serializing then parsing SHALL produce an equivalent configuration object (round-trip property)
5. THE Mission_Configuration personnel section SHALL support configurable Decision_Points with decision_id (unique within the configuration), assigned_role (matching a role in the Personnel_Roster), decision_type (one of GO_NO_GO, APPROVE_DENY, VERIFY_REJECT), and deliberation_duration (positive numeric value in simulation time units) for each decision gate in the mission flow
