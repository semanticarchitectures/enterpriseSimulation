"""AuthorizationModule implementation.

Models sequential and parallel authorization gate chains using SimPy processes.
Gates have configurable durations and optional timeouts. Events are published
to the Event Bus on gate completion, timeout, and chain completion.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Set

import simpy

from enterprise_sim.models.events import (
    ChainCompletionEvent,
    GateCompletionEvent,
    GateTimeoutEvent,
)
from enterprise_sim.modules.base import DomainModule

if TYPE_CHECKING:
    from enterprise_sim.engine.event_bus import EventBus
    from enterprise_sim.models.authorization import AuthorizationChainConfig
    from enterprise_sim.models.mission import MissionConfiguration


class AuthorizationModule(DomainModule):
    """Domain module for authorization chain processing.

    Supports sequential chains (gates processed one after another) and
    parallel chains (all gates processed concurrently). Publishes events
    on gate completion, gate timeout, and chain completion.
    """

    module_type: ClassVar[str] = "authorization"
    dependencies: ClassVar[Set[str]] = set()

    def __init__(self) -> None:
        self._env: simpy.Environment | None = None
        self._event_bus: EventBus | None = None
        self._chain_config: AuthorizationChainConfig | None = None
        self._gate_states: dict[str, str] = {}

    def initialize(
        self,
        env: simpy.Environment,
        event_bus: EventBus,
        config: MissionConfiguration,
    ) -> None:
        """Set up internal state, store chain config, subscribe to events."""
        self._env = env
        self._event_bus = event_bus
        self._chain_config = config.authorization_chain
        self._gate_states = {}

        if self._chain_config:
            for gate in self._chain_config.gates:
                self._gate_states[gate.gate_name] = "pending"

    def create_processes(self, env: simpy.Environment) -> list[simpy.events.Process]:
        """Return SimPy processes for chain execution."""
        if not self._chain_config:
            return []

        if self._chain_config.chain_type == "sequential":
            return [env.process(self._run_sequential_chain(env))]
        else:
            return [env.process(self._run_parallel_chain(env))]

    def finalize(self) -> None:
        """Clean up internal state."""
        self._env = None
        self._event_bus = None
        self._chain_config = None
        self._gate_states = {}

    def _run_sequential_chain(self, env: simpy.Environment):
        """Process gates sequentially. Each gate blocks until complete or timeout."""
        assert self._chain_config is not None
        assert self._event_bus is not None

        chain_start = env.now
        all_succeeded = True

        for gate in self._chain_config.gates:
            self._gate_states[gate.gate_name] = "active"

            if gate.timeout is not None and gate.timeout < gate.duration:
                # Gate will timeout before completion
                yield env.timeout(gate.timeout)
                self._gate_states[gate.gate_name] = "failed"

                self._event_bus.publish(
                    GateTimeoutEvent(
                        entity_id=f"timeout_{gate.gate_name}",
                        timestamp=env.now,
                        source_module="authorization",
                        gate_name=gate.gate_name,
                        chain_id=self._chain_config.chain_id,
                    )
                )

                # Cancel all remaining gates in sequential chain
                remaining_gates = self._chain_config.gates[
                    self._chain_config.gates.index(gate) + 1 :
                ]
                for remaining in remaining_gates:
                    self._gate_states[remaining.gate_name] = "failed"

                all_succeeded = False
                break
            else:
                # Gate completes normally
                yield env.timeout(gate.duration)
                self._gate_states[gate.gate_name] = "completed"

                self._event_bus.publish(
                    GateCompletionEvent(
                        entity_id=f"completion_{gate.gate_name}",
                        timestamp=env.now,
                        source_module="authorization",
                        gate_name=gate.gate_name,
                        chain_id=self._chain_config.chain_id,
                    )
                )

        if all_succeeded:
            elapsed = env.now - chain_start
            self._event_bus.publish(
                ChainCompletionEvent(
                    entity_id=f"chain_completion_{self._chain_config.chain_id}",
                    timestamp=env.now,
                    source_module="authorization",
                    chain_id=self._chain_config.chain_id,
                    elapsed_time=elapsed,
                )
            )

    def _run_parallel_chain(self, env: simpy.Environment):
        """Process all gates concurrently using SimPy AllOf."""
        assert self._chain_config is not None
        assert self._event_bus is not None

        chain_start = env.now

        # Start all gate processes concurrently
        gate_processes = []
        for gate in self._chain_config.gates:
            proc = env.process(self._run_single_gate(env, gate))
            gate_processes.append(proc)

        # Wait for ALL gates to complete (or timeout)
        yield simpy.events.AllOf(env, gate_processes)

        # Check if all gates succeeded
        all_succeeded = all(
            self._gate_states[gate.gate_name] == "completed"
            for gate in self._chain_config.gates
        )

        if all_succeeded:
            elapsed = env.now - chain_start
            self._event_bus.publish(
                ChainCompletionEvent(
                    entity_id=f"chain_completion_{self._chain_config.chain_id}",
                    timestamp=env.now,
                    source_module="authorization",
                    chain_id=self._chain_config.chain_id,
                    elapsed_time=elapsed,
                )
            )

    def _run_single_gate(self, env: simpy.Environment, gate):
        """Process a single gate (used in parallel chains)."""
        assert self._event_bus is not None
        assert self._chain_config is not None

        self._gate_states[gate.gate_name] = "active"

        if gate.timeout is not None and gate.timeout < gate.duration:
            # Gate will timeout
            yield env.timeout(gate.timeout)
            self._gate_states[gate.gate_name] = "failed"

            self._event_bus.publish(
                GateTimeoutEvent(
                    entity_id=f"timeout_{gate.gate_name}",
                    timestamp=env.now,
                    source_module="authorization",
                    gate_name=gate.gate_name,
                    chain_id=self._chain_config.chain_id,
                )
            )
        else:
            # Gate completes normally
            yield env.timeout(gate.duration)
            self._gate_states[gate.gate_name] = "completed"

            self._event_bus.publish(
                GateCompletionEvent(
                    entity_id=f"completion_{gate.gate_name}",
                    timestamp=env.now,
                    source_module="authorization",
                    gate_name=gate.gate_name,
                    chain_id=self._chain_config.chain_id,
                )
            )
