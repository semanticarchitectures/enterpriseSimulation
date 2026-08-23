"""Unit tests for AirdropModule.

Tests phase transitions, weight limit enforcement, event publishing,
and lifecycle methods.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import simpy

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.airdrop import AirdropConfig, AirdropPhase, CargoItem
from enterprise_sim.models.events import (
    ExtractionCompleteEvent,
    ExtractionStartEvent,
    WeightExceededEvent,
)
from enterprise_sim.modules.airdrop import AirdropModule


def _make_cargo_manifest(
    items: list[tuple[str, float]] | None = None,
) -> list[CargoItem]:
    """Create a cargo manifest for testing."""
    if items is None:
        items = [("item_1", 100.0), ("item_2", 200.0)]
    return [
        CargoItem(item_id=item_id, weight=weight, description=f"Cargo {item_id}")
        for item_id, weight in items
    ]


def _make_airdrop_config(
    cargo_weight_limit: float = 1000.0,
    phase_durations: dict[AirdropPhase, float] | None = None,
    cargo_manifest: list[CargoItem] | None = None,
) -> AirdropConfig:
    """Create an AirdropConfig with configurable parameters."""
    if phase_durations is None:
        phase_durations = {
            AirdropPhase.EN_ROUTE: 5.0,
            AirdropPhase.SLOWDOWN: 10.0,
            AirdropPhase.EXTRACTION: 20.0,
            AirdropPhase.ACCELERATION: 15.0,
        }
    if cargo_manifest is None:
        cargo_manifest = _make_cargo_manifest()
    return AirdropConfig(
        extraction_speed=150.0,
        drop_altitude_ft=1000.0,
        cargo_weight_limit=cargo_weight_limit,
        phase_durations=phase_durations,
        cargo_manifest=cargo_manifest,
    )


def _make_config(airdrop_parameters: AirdropConfig | None = None):
    """Create a mock MissionConfiguration with airdrop parameters."""
    if airdrop_parameters is None:
        airdrop_parameters = _make_airdrop_config()
    config = MagicMock()
    config.airdrop_parameters = airdrop_parameters
    return config


class TestAirdropModuleMetadata:
    """Test class-level metadata."""

    def test_module_type(self):
        assert AirdropModule.module_type == "airdrop"

    def test_dependencies_includes_aerodynamics(self):
        assert AirdropModule.dependencies == {"aerodynamics"}


class TestAirdropModuleInitialize:
    """Test initialization behavior."""

    def test_initialize_stores_airdrop_config(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()
        airdrop_config = _make_airdrop_config()
        config = _make_config(airdrop_parameters=airdrop_config)

        module.initialize(env, event_bus, config)

        assert module._airdrop_config is airdrop_config

    def test_initialize_sets_current_phase_to_en_route(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()

        module.initialize(env, event_bus, config)

        assert module.current_phase == AirdropPhase.EN_ROUTE

    def test_initialize_with_no_airdrop_config(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = MagicMock()
        config.airdrop_parameters = None

        module.initialize(env, event_bus, config)

        assert module._airdrop_config is None
        assert module.current_phase == AirdropPhase.EN_ROUTE


class TestAirdropWeightLimitEnforcement:
    """Test cargo weight limit enforcement."""

    def test_weight_exceeded_publishes_event_and_stays_in_en_route(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        # Total cargo weight = 100 + 200 = 300, limit = 250
        cargo = _make_cargo_manifest([("a", 100.0), ("b", 200.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=250.0, cargo_manifest=cargo
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        weight_events: list[WeightExceededEvent] = []
        event_bus.subscribe(
            WeightExceededEvent,
            lambda e: weight_events.append(e),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        assert len(weight_events) == 1
        assert weight_events[0].cargo_weight == 300.0
        assert weight_events[0].weight_limit == 250.0
        assert module.current_phase == AirdropPhase.EN_ROUTE

    def test_weight_exactly_at_limit_proceeds(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        # Total cargo weight = 100 + 200 = 300, limit = 300 (not exceeded)
        cargo = _make_cargo_manifest([("a", 100.0), ("b", 200.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=300.0, cargo_manifest=cargo
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        weight_events: list[WeightExceededEvent] = []
        event_bus.subscribe(
            WeightExceededEvent,
            lambda e: weight_events.append(e),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        # No weight exceeded event; proceeds through phases
        assert len(weight_events) == 0
        assert module.current_phase == AirdropPhase.EN_ROUTE

    def test_weight_under_limit_proceeds(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        cargo = _make_cargo_manifest([("a", 50.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0, cargo_manifest=cargo
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        extraction_starts: list[ExtractionStartEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: extraction_starts.append(e),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        # Should proceed to extraction
        assert len(extraction_starts) == 1


class TestAirdropPhaseTransitions:
    """Test phase state transitions during airdrop sequence."""

    def test_full_phase_sequence_timing(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        durations = {
            AirdropPhase.EN_ROUTE: 5.0,
            AirdropPhase.SLOWDOWN: 10.0,
            AirdropPhase.EXTRACTION: 20.0,
            AirdropPhase.ACCELERATION: 15.0,
        }
        cargo = _make_cargo_manifest([("a", 50.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0,
            phase_durations=durations,
            cargo_manifest=cargo,
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        module.create_processes(env)
        env.run()

        # Total sim time = slowdown(10) + extraction(20) + acceleration(15) = 45
        assert env.now == pytest.approx(45.0)
        assert module.current_phase == AirdropPhase.EN_ROUTE

    def test_phase_is_slowdown_during_slowdown_period(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        durations = {
            AirdropPhase.EN_ROUTE: 5.0,
            AirdropPhase.SLOWDOWN: 10.0,
            AirdropPhase.EXTRACTION: 20.0,
            AirdropPhase.ACCELERATION: 15.0,
        }
        cargo = _make_cargo_manifest([("a", 50.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0,
            phase_durations=durations,
            cargo_manifest=cargo,
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        # Record phase at different time points
        phase_log: list[tuple[float, AirdropPhase]] = []

        def monitor(env, module, phase_log):
            """Monitor process that samples phase at each time step."""
            yield env.timeout(0)  # Start at t=0
            phase_log.append((env.now, module.current_phase))
            yield env.timeout(5.0)  # t=5 (during slowdown)
            phase_log.append((env.now, module.current_phase))
            yield env.timeout(6.0)  # t=11 (during extraction)
            phase_log.append((env.now, module.current_phase))
            yield env.timeout(20.0)  # t=31 (during acceleration)
            phase_log.append((env.now, module.current_phase))
            yield env.timeout(15.0)  # t=46 (back to en_route)
            phase_log.append((env.now, module.current_phase))

        module.create_processes(env)
        env.process(monitor(env, module, phase_log))
        env.run()

        # t=0: just started, already transitioned to SLOWDOWN
        assert phase_log[0] == (0.0, AirdropPhase.SLOWDOWN)
        # t=5: still in SLOWDOWN (duration is 10)
        assert phase_log[1] == (5.0, AirdropPhase.SLOWDOWN)
        # t=11: in EXTRACTION (slowdown ended at t=10)
        assert phase_log[2] == (11.0, AirdropPhase.EXTRACTION)
        # t=31: in ACCELERATION (extraction ended at t=30)
        assert phase_log[3] == (31.0, AirdropPhase.ACCELERATION)
        # t=46: back to EN_ROUTE (acceleration ended at t=45)
        assert phase_log[4] == (46.0, AirdropPhase.EN_ROUTE)


class TestAirdropEventPublishing:
    """Test event publishing during airdrop sequence."""

    def test_extraction_start_event_published(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        cargo = _make_cargo_manifest([("item_1", 50.0), ("item_2", 75.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0, cargo_manifest=cargo
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        start_events: list[ExtractionStartEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: start_events.append(e),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        assert len(start_events) == 1
        assert start_events[0].source_module == "airdrop"
        assert start_events[0].drop_zone_id == "drop_zone_1"
        # Extraction start happens after slowdown (duration 10)
        assert start_events[0].timestamp == pytest.approx(10.0)

    def test_extraction_complete_event_published_with_manifest(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        cargo = _make_cargo_manifest([("item_1", 50.0), ("item_2", 75.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0, cargo_manifest=cargo
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        complete_events: list[ExtractionCompleteEvent] = []
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: complete_events.append(e),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        assert len(complete_events) == 1
        assert complete_events[0].source_module == "airdrop"
        assert complete_events[0].drop_zone_id == "drop_zone_1"
        assert complete_events[0].cargo_manifest == ["item_1", "item_2"]
        # Extraction complete happens after slowdown(10) + extraction(20) = 30
        assert complete_events[0].timestamp == pytest.approx(30.0)

    def test_extraction_duration_equals_configured_duration(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        durations = {
            AirdropPhase.EN_ROUTE: 5.0,
            AirdropPhase.SLOWDOWN: 8.0,
            AirdropPhase.EXTRACTION: 12.0,
            AirdropPhase.ACCELERATION: 6.0,
        }
        cargo = _make_cargo_manifest([("a", 50.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0,
            phase_durations=durations,
            cargo_manifest=cargo,
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        start_events: list[ExtractionStartEvent] = []
        complete_events: list[ExtractionCompleteEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: start_events.append(e),
            "test_start",
        )
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: complete_events.append(e),
            "test_complete",
        )

        module.create_processes(env)
        env.run()

        extraction_duration = (
            complete_events[0].timestamp - start_events[0].timestamp
        )
        assert extraction_duration == pytest.approx(12.0)

    def test_no_extraction_events_when_weight_exceeded(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        # Overweight cargo
        cargo = _make_cargo_manifest([("a", 600.0), ("b", 500.0)])
        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0, cargo_manifest=cargo
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        start_events: list[ExtractionStartEvent] = []
        complete_events: list[ExtractionCompleteEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: start_events.append(e),
            "test_start",
        )
        event_bus.subscribe(
            ExtractionCompleteEvent,
            lambda e: complete_events.append(e),
            "test_complete",
        )

        module.create_processes(env)
        env.run()

        assert len(start_events) == 0
        assert len(complete_events) == 0


class TestAirdropModuleLifecycle:
    """Test lifecycle methods."""

    def test_create_processes_returns_one_process(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        processes = module.create_processes(env)

        assert len(processes) == 1

    def test_create_processes_returns_empty_without_config(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = MagicMock()
        config.airdrop_parameters = None
        module.initialize(env, event_bus, config)

        processes = module.create_processes(env)

        assert processes == []

    def test_finalize_resets_state(self):
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()
        config = _make_config()
        module.initialize(env, event_bus, config)

        module.create_processes(env)
        env.run()

        module.finalize()

        assert module.current_phase == AirdropPhase.EN_ROUTE
        assert module._airdrop_config is None

    def test_empty_cargo_manifest_proceeds(self):
        """Empty manifest means zero weight, which is within any limit."""
        module = AirdropModule()
        env = simpy.Environment()
        event_bus = EventBus()

        airdrop_config = _make_airdrop_config(
            cargo_weight_limit=1000.0,
            cargo_manifest=[],
        )
        config = _make_config(airdrop_parameters=airdrop_config)
        module.initialize(env, event_bus, config)

        start_events: list[ExtractionStartEvent] = []
        event_bus.subscribe(
            ExtractionStartEvent,
            lambda e: start_events.append(e),
            "test_sub",
        )

        module.create_processes(env)
        env.run()

        # Empty manifest weight is 0, which does not exceed limit
        assert len(start_events) == 1
        assert module.current_phase == AirdropPhase.EN_ROUTE
