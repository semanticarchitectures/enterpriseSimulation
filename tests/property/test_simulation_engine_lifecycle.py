"""Property tests for Simulation Engine Lifecycle.

**Property 29: Module Lifecycle Ordering**
**Property 30: Initialization Failure Cleanup**
**Property 31: Finalization Fault Tolerance**
**Validates: Requirements 13.2, 13.3, 13.4, 13.7**

Property 29: For any set of Domain_Modules with a valid (acyclic) dependency graph,
the Simulation_Engine SHALL call initialize in a topological order consistent with
declared dependencies, and finalize in the exact reverse of initialization order.

Property 30: For any set of Domain_Modules where one fails during initialization,
the Simulation_Engine SHALL call finalize on all previously-initialized modules
(those initialized before the failure) in reverse initialization order, and return
an error identifying the failed module.

Property 31: For any set of Domain_Modules where one or more raise exceptions during
finalization, the Simulation_Engine SHALL log the error and continue finalizing the
remaining modules in order.
"""

from __future__ import annotations

from typing import ClassVar, Set
from unittest.mock import MagicMock

import simpy
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.engine.module_registry import ModuleRegistry
from enterprise_sim.engine.simulation_engine import SimulationEngine
from enterprise_sim.modules.base import DomainModule


# === Test Infrastructure: Concrete DomainModule for testing ===


class TrackedModule(DomainModule):
    """A concrete DomainModule that records lifecycle calls for testing."""

    module_type: ClassVar[str] = "default"
    dependencies: ClassVar[Set[str]] = set()

    def __init__(self, name: str, deps: set[str] | None = None,
                 fail_on_init: bool = False, fail_on_finalize: bool = False):
        self._name = name
        self._deps = deps or set()
        self._fail_on_init = fail_on_init
        self._fail_on_finalize = fail_on_finalize
        self.init_called = False
        self.finalize_called = False

    @property  # type: ignore[override]
    def module_type(self) -> str:  # type: ignore[override]
        return self._name

    @property  # type: ignore[override]
    def dependencies(self) -> Set[str]:  # type: ignore[override]
        return self._deps

    def initialize(self, env, event_bus, config) -> None:
        if self._fail_on_init:
            raise RuntimeError(f"Module '{self._name}' initialization failed")
        self.init_called = True

    def create_processes(self, env) -> list:
        return []

    def finalize(self) -> None:
        if self._fail_on_finalize:
            self.finalize_called = True
            raise RuntimeError(f"Module '{self._name}' finalization failed")
        self.finalize_called = True


class _FakeMissionConfig:
    """Minimal fake MissionConfiguration for testing engine lifecycle."""

    def __init__(self, active_modules: list[str] | None = None):
        self.active_modules = active_modules or []


# === Hypothesis Strategies ===

# Strategy for valid module names
module_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
    min_size=1,
    max_size=15,
)


@st.composite
def chain_dependency_graph(draw):
    """Generate a chain (linear) dependency graph: A -> B -> C -> ..."""
    num_modules = draw(st.integers(min_value=2, max_value=6))
    names = draw(
        st.lists(
            module_name_st,
            min_size=num_modules,
            max_size=num_modules,
            unique=True,
        )
    )
    # Each module depends on the previous one
    modules = []
    for i, name in enumerate(names):
        deps = {names[i - 1]} if i > 0 else set()
        modules.append(TrackedModule(name=name, deps=deps))
    return modules


@st.composite
def diamond_dependency_graph(draw):
    """Generate a diamond dependency graph: root -> [middle_1, middle_2, ...] -> leaf."""
    num_middle = draw(st.integers(min_value=2, max_value=4))
    all_names = draw(
        st.lists(
            module_name_st,
            min_size=num_middle + 2,
            max_size=num_middle + 2,
            unique=True,
        )
    )
    root_name = all_names[0]
    middle_names = all_names[1 : num_middle + 1]
    leaf_name = all_names[num_middle + 1]

    modules = [TrackedModule(name=root_name, deps=set())]
    for mid_name in middle_names:
        modules.append(TrackedModule(name=mid_name, deps={root_name}))
    modules.append(TrackedModule(name=leaf_name, deps=set(middle_names)))
    return modules


@st.composite
def independent_modules(draw):
    """Generate a set of independent modules with no dependencies."""
    num_modules = draw(st.integers(min_value=2, max_value=6))
    names = draw(
        st.lists(
            module_name_st,
            min_size=num_modules,
            max_size=num_modules,
            unique=True,
        )
    )
    return [TrackedModule(name=name, deps=set()) for name in names]


# Combined strategy for any acyclic dependency graph shape
acyclic_graph_st = st.one_of(
    chain_dependency_graph(),
    diamond_dependency_graph(),
    independent_modules(),
)


@st.composite
def modules_with_failure_point(draw):
    """Generate a set of modules with one designated to fail during initialization."""
    modules = draw(acyclic_graph_st)
    assume(len(modules) >= 2)
    # Choose a failure index (not the first module, to have something to clean up)
    fail_idx = draw(st.integers(min_value=1, max_value=len(modules) - 1))
    # Mark the module at fail_idx to fail on init
    modules[fail_idx]._fail_on_init = True
    return modules, fail_idx


@st.composite
def modules_with_finalize_faults(draw):
    """Generate modules where one or more will fault during finalization."""
    modules = draw(acyclic_graph_st)
    assume(len(modules) >= 2)
    # Choose which modules will fault (at least 1, up to len-1)
    num_faults = draw(st.integers(min_value=1, max_value=max(1, len(modules) - 1)))
    fault_indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=len(modules) - 1),
            min_size=num_faults,
            max_size=num_faults,
            unique=True,
        )
    )
    for idx in fault_indices:
        modules[idx]._fail_on_finalize = True
    return modules, set(fault_indices)


# === Helper Functions ===


def _build_engine_with_modules(modules: list[TrackedModule]) -> tuple[SimulationEngine, list[TrackedModule]]:
    """Register modules in a registry and return engine + ordered modules."""
    registry = ModuleRegistry()
    event_bus = EventBus()
    for module in modules:
        registry.register(module)
    engine = SimulationEngine(registry, event_bus)
    # Get the topological order the engine would use
    ordered = registry.resolve_initialization_order()
    return engine, ordered


def _is_valid_topological_order(modules: list[TrackedModule], order: list[str]) -> bool:
    """Check if the given order is a valid topological order for the dependency graph."""
    position = {name: idx for idx, name in enumerate(order)}
    for module in modules:
        for dep in module.dependencies:
            if dep in position and module.module_type in position:
                if position[dep] >= position[module.module_type]:
                    return False
    return True


# === Property 29: Module Lifecycle Ordering ===


class TestModuleLifecycleOrdering:
    """Property 29: Module Lifecycle Ordering.

    **Validates: Requirements 13.2, 13.3**

    For any set of Domain_Modules with a valid (acyclic) dependency graph,
    the Simulation_Engine SHALL call initialize in a topological order
    consistent with declared dependencies, and finalize in the exact reverse
    of initialization order.
    """

    @given(modules=acyclic_graph_st)
    @settings(max_examples=100)
    def test_initialization_respects_topological_order(self, modules: list[TrackedModule]):
        """Initialize is called in topological dependency order."""
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        # Track initialization order
        init_order: list[str] = []
        for module in modules:
            original_init = module.initialize

            def tracking_init(env, event_bus, config, m=module, orig=original_init):
                init_order.append(m.module_type)
                orig(env, event_bus, config)

            module.initialize = tracking_init  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        engine.run(config)  # type: ignore[arg-type]

        # Verify topological ordering: for each module, all deps come before it
        assert _is_valid_topological_order(modules, init_order)

    @given(modules=acyclic_graph_st)
    @settings(max_examples=100)
    def test_finalization_is_reverse_of_initialization(self, modules: list[TrackedModule]):
        """Finalize is called in exact reverse of initialization order."""
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        # Track both init and finalize order
        init_order: list[str] = []
        finalize_order: list[str] = []

        for module in modules:
            original_init = module.initialize
            original_finalize = module.finalize

            def tracking_init(env, event_bus, config, m=module, orig=original_init):
                init_order.append(m.module_type)
                orig(env, event_bus, config)

            def tracking_finalize(m=module, orig=original_finalize):
                finalize_order.append(m.module_type)
                orig()

            module.initialize = tracking_init  # type: ignore[assignment]
            module.finalize = tracking_finalize  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        engine.run(config)  # type: ignore[arg-type]

        # Finalization order must be exact reverse of initialization order
        assert finalize_order == list(reversed(init_order))

    @given(modules=chain_dependency_graph())
    @settings(max_examples=50)
    def test_chain_dependency_strict_order(self, modules: list[TrackedModule]):
        """Chain dependencies produce a strict linear initialization order."""
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        init_order: list[str] = []
        for module in modules:
            original_init = module.initialize

            def tracking_init(env, event_bus, config, m=module, orig=original_init):
                init_order.append(m.module_type)
                orig(env, event_bus, config)

            module.initialize = tracking_init  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        engine.run(config)  # type: ignore[arg-type]

        # In a chain A->B->C, init order must match the chain order
        expected_names = [m.module_type for m in modules]
        assert init_order == expected_names

    @given(modules=diamond_dependency_graph())
    @settings(max_examples=50)
    def test_diamond_dependency_root_before_middle_before_leaf(self, modules: list[TrackedModule]):
        """Diamond dependencies: root initializes first, leaf last."""
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        init_order: list[str] = []
        for module in modules:
            original_init = module.initialize

            def tracking_init(env, event_bus, config, m=module, orig=original_init):
                init_order.append(m.module_type)
                orig(env, event_bus, config)

            module.initialize = tracking_init  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        engine.run(config)  # type: ignore[arg-type]

        # Root (modules[0]) must be first, leaf (modules[-1]) must be last
        assert init_order[0] == modules[0].module_type
        assert init_order[-1] == modules[-1].module_type


# === Property 30: Initialization Failure Cleanup ===


class TestInitializationFailureCleanup:
    """Property 30: Initialization Failure Cleanup.

    **Validates: Requirements 13.4**

    For any set of Domain_Modules where one fails during initialization,
    the Simulation_Engine SHALL call finalize on all previously-initialized
    modules (those initialized before the failure) in reverse initialization
    order, and return an error identifying the failed module.
    """

    @given(data=modules_with_failure_point())
    @settings(max_examples=100)
    def test_finalize_called_on_previously_initialized(self, data):
        """Previously-initialized modules get finalized when one fails."""
        modules, fail_idx = data
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        # Track initialization and finalization
        init_order: list[str] = []
        finalize_order: list[str] = []

        for module in modules:
            original_init = module.initialize
            original_finalize = module.finalize

            def tracking_init(env, event_bus, config, m=module, orig=original_init):
                init_order.append(m.module_type)
                orig(env, event_bus, config)

            def tracking_finalize(m=module, orig=original_finalize):
                finalize_order.append(m.module_type)
                orig()

            module.initialize = tracking_init  # type: ignore[assignment]
            module.finalize = tracking_finalize  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        result = engine.run(config)  # type: ignore[arg-type]

        # The run should fail
        assert result.success is False

        # Modules initialized before the failure (excluding the failed one)
        # should be finalized in reverse init order
        failed_module_name = modules[fail_idx].module_type
        successfully_initialized = [
            name for name in init_order if name != failed_module_name
        ]
        assert finalize_order == list(reversed(successfully_initialized))

    @given(data=modules_with_failure_point())
    @settings(max_examples=100)
    def test_error_identifies_failed_module(self, data):
        """The error result identifies which module failed."""
        modules, fail_idx = data
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        result = engine.run(config)  # type: ignore[arg-type]

        assert result.success is False
        assert result.failed_module == modules[fail_idx].module_type
        assert result.error is not None
        assert modules[fail_idx].module_type in result.error

    @given(data=modules_with_failure_point())
    @settings(max_examples=100)
    def test_failed_module_not_finalized(self, data):
        """The module that failed initialization is NOT finalized."""
        modules, fail_idx = data
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        finalize_order: list[str] = []
        for module in modules:
            original_finalize = module.finalize

            def tracking_finalize(m=module, orig=original_finalize):
                finalize_order.append(m.module_type)
                orig()

            module.finalize = tracking_finalize  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        engine.run(config)  # type: ignore[arg-type]

        # The failed module should NOT appear in finalize_order
        assert modules[fail_idx].module_type not in finalize_order

    @given(modules=chain_dependency_graph())
    @settings(max_examples=50)
    def test_first_module_failure_means_no_finalization(self, modules: list[TrackedModule]):
        """If the first module fails, no finalization calls happen."""
        # Make the first module in topological order fail
        modules[0]._fail_on_init = True

        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        finalize_order: list[str] = []
        for module in modules:
            original_finalize = module.finalize

            def tracking_finalize(m=module, orig=original_finalize):
                finalize_order.append(m.module_type)
                orig()

            module.finalize = tracking_finalize  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        result = engine.run(config)  # type: ignore[arg-type]

        assert result.success is False
        # No finalization should happen since nothing was initialized before failure
        assert finalize_order == []


# === Property 31: Finalization Fault Tolerance ===


class TestFinalizationFaultTolerance:
    """Property 31: Finalization Fault Tolerance.

    **Validates: Requirements 13.7**

    For any set of Domain_Modules where one or more raise exceptions during
    finalization, the Simulation_Engine SHALL log the error and continue
    finalizing the remaining modules in order.
    """

    @given(data=modules_with_finalize_faults())
    @settings(max_examples=100)
    def test_all_modules_finalized_despite_faults(self, data):
        """All modules are finalized even when some raise exceptions."""
        modules, fault_indices = data
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        result = engine.run(config)  # type: ignore[arg-type]

        # The run should succeed (finalization faults don't fail the simulation)
        assert result.success is True

        # Verify that finalize was called on ALL modules
        ordered = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        active_ordered = [m for m in ordered if m.module_type in active_set]
        for module in active_ordered:
            assert module.finalize_called, (
                f"Module '{module.module_type}' was not finalized"
            )

    @given(data=modules_with_finalize_faults())
    @settings(max_examples=100)
    def test_finalization_order_preserved_despite_faults(self, data):
        """Finalization order is preserved even when some modules fault."""
        modules, fault_indices = data
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        # Track finalization order
        finalize_order: list[str] = []
        for module in modules:
            original_finalize = module.finalize

            def tracking_finalize(m=module, orig=original_finalize):
                finalize_order.append(m.module_type)
                orig()

            module.finalize = tracking_finalize  # type: ignore[assignment]

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        engine.run(config)  # type: ignore[arg-type]

        # Finalization should be in reverse init order (all modules present)
        ordered = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        active_ordered = [m for m in ordered if m.module_type in active_set]
        expected_finalize_order = [m.module_type for m in reversed(active_ordered)]
        assert finalize_order == expected_finalize_order

    @given(data=modules_with_finalize_faults())
    @settings(max_examples=50)
    def test_finalization_faults_are_logged(self, data):
        """Finalization exceptions are logged with module identifier."""
        import logging as _logging

        modules, fault_indices = data
        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])

        # Capture log records
        log_records: list[_logging.LogRecord] = []

        class _CaptureHandler(_logging.Handler):
            def emit(self, record: _logging.LogRecord) -> None:
                log_records.append(record)

        handler = _CaptureHandler()
        sim_logger = _logging.getLogger("enterprise_sim.engine.simulation_engine")
        sim_logger.addHandler(handler)
        sim_logger.setLevel(_logging.ERROR)

        try:
            engine.run(config)  # type: ignore[arg-type]
        finally:
            sim_logger.removeHandler(handler)

        # Should have logged an error for each faulting module
        ordered = registry.resolve_initialization_order()
        active_set = set(config.active_modules)
        active_ordered = [m for m in ordered if m.module_type in active_set]
        faulting_names = {active_ordered[i].module_type for i in fault_indices if i < len(active_ordered)}

        logged_messages = [r.getMessage() for r in log_records]
        for faulting_name in faulting_names:
            assert any(
                faulting_name in msg for msg in logged_messages
            ), f"Expected log entry for faulting module '{faulting_name}'"

    @given(modules=independent_modules())
    @settings(max_examples=50)
    def test_single_module_finalize_fault_does_not_fail_run(self, modules: list[TrackedModule]):
        """A single module's finalization fault does not cause run() to fail."""
        # Make the first module fault on finalize
        modules[0]._fail_on_finalize = True

        registry = ModuleRegistry()
        event_bus = EventBus()
        for module in modules:
            registry.register(module)
        engine = SimulationEngine(registry, event_bus)

        config = _FakeMissionConfig(active_modules=[m.module_type for m in modules])
        result = engine.run(config)  # type: ignore[arg-type]

        # The simulation should report success despite finalization fault
        assert result.success is True
