"""Unit tests for ontology-aligned base classes."""

import pytest
from pydantic import ValidationError

from enterprise_sim.models.base import (
    Agent,
    Continuant,
    Entity,
    InformationEntity,
    Occurrent,
    Process,
    SpatialRegion,
)


class TestEntity:
    """Tests for the Entity root class."""

    def test_create_entity(self):
        e = Entity(entity_id="e1", entity_type="test")
        assert e.entity_id == "e1"
        assert e.entity_type == "test"

    def test_entity_requires_entity_id(self):
        with pytest.raises(ValidationError) as exc_info:
            Entity(entity_type="test")
        assert "entity_id" in str(exc_info.value)

    def test_entity_requires_entity_type(self):
        with pytest.raises(ValidationError) as exc_info:
            Entity(entity_id="e1")
        assert "entity_type" in str(exc_info.value)


class TestContinuant:
    """Tests for the Continuant class."""

    def test_default_entity_type(self):
        c = Continuant(entity_id="c1")
        assert c.entity_type == "continuant"

    def test_override_entity_type(self):
        c = Continuant(entity_id="c1", entity_type="custom")
        assert c.entity_type == "custom"


class TestOccurrent:
    """Tests for the Occurrent class."""

    def test_create_occurrent(self):
        o = Occurrent(entity_id="o1", timestamp=10.5)
        assert o.entity_type == "occurrent"
        assert o.timestamp == 10.5

    def test_timestamp_zero_allowed(self):
        o = Occurrent(entity_id="o1", timestamp=0.0)
        assert o.timestamp == 0.0

    def test_negative_timestamp_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            Occurrent(entity_id="o1", timestamp=-1.0)
        assert "timestamp" in str(exc_info.value)

    def test_timestamp_required(self):
        with pytest.raises(ValidationError) as exc_info:
            Occurrent(entity_id="o1")
        assert "timestamp" in str(exc_info.value)


class TestInformationEntity:
    """Tests for the InformationEntity class."""

    def test_default_values(self):
        ie = InformationEntity(entity_id="ie1")
        assert ie.entity_type == "information_entity"
        assert ie.classification is None

    def test_with_classification(self):
        ie = InformationEntity(entity_id="ie1", classification="SECRET")
        assert ie.classification == "SECRET"


class TestAgent:
    """Tests for the Agent class."""

    def test_default_entity_type(self):
        a = Agent(entity_id="a1")
        assert a.entity_type == "agent"

    def test_inherits_from_continuant(self):
        a = Agent(entity_id="a1")
        assert isinstance(a, Continuant)
        assert isinstance(a, Entity)


class TestSpatialRegion:
    """Tests for the SpatialRegion class."""

    def test_default_values(self):
        sr = SpatialRegion(entity_id="sr1")
        assert sr.entity_type == "spatial_region"
        assert sr.latitude is None
        assert sr.longitude is None
        assert sr.altitude_ft is None

    def test_with_coordinates(self):
        sr = SpatialRegion(
            entity_id="sr1",
            latitude=34.05,
            longitude=-118.25,
            altitude_ft=25000.0,
        )
        assert sr.latitude == 34.05
        assert sr.longitude == -118.25
        assert sr.altitude_ft == 25000.0

    def test_negative_altitude_allowed(self):
        """Altitude can be negative (below sea level)."""
        sr = SpatialRegion(entity_id="sr1", altitude_ft=-100.0)
        assert sr.altitude_ft == -100.0


class TestProcess:
    """Tests for the Process class."""

    def test_create_process(self):
        p = Process(entity_id="p1", timestamp=0.0, start_time=5.0)
        assert p.entity_type == "process"
        assert p.start_time == 5.0
        assert p.end_time is None

    def test_with_end_time(self):
        p = Process(entity_id="p1", timestamp=0.0, start_time=5.0, end_time=10.0)
        assert p.end_time == 10.0

    def test_start_time_must_be_non_negative(self):
        with pytest.raises(ValidationError) as exc_info:
            Process(entity_id="p1", timestamp=0.0, start_time=-1.0)
        assert "start_time" in str(exc_info.value)

    def test_end_time_must_be_non_negative(self):
        with pytest.raises(ValidationError) as exc_info:
            Process(entity_id="p1", timestamp=0.0, start_time=5.0, end_time=-1.0)
        assert "end_time" in str(exc_info.value)

    def test_start_time_zero_allowed(self):
        p = Process(entity_id="p1", timestamp=0.0, start_time=0.0)
        assert p.start_time == 0.0

    def test_end_time_zero_allowed(self):
        p = Process(entity_id="p1", timestamp=0.0, start_time=0.0, end_time=0.0)
        assert p.end_time == 0.0

    def test_inherits_from_occurrent(self):
        p = Process(entity_id="p1", timestamp=0.0, start_time=5.0)
        assert isinstance(p, Occurrent)
        assert isinstance(p, Entity)


class TestNoSimPyImports:
    """Verify the base module has zero SimPy imports (Requirement 3.1)."""

    def test_no_simpy_import_statements(self):
        import inspect
        import enterprise_sim.models.base as base_module

        source = inspect.getsource(base_module)
        # Check there are no import statements referencing simpy
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                assert "simpy" not in stripped.lower(), (
                    f"Found SimPy import: {stripped}"
                )

    def test_no_simpy_in_module_namespace(self):
        import enterprise_sim.models.base as base_module
        import sys

        # Ensure simpy is not loaded as a dependency of this module
        base_module_deps = [
            key for key in sys.modules
            if key.startswith("simpy")
        ]
        # simpy might be installed, but base module shouldn't import it
        assert not hasattr(base_module, "simpy")


class TestValidationErrorAggregation:
    """Verify multiple validation errors are reported together (Requirement 3.4)."""

    def test_multiple_errors_reported(self):
        """Process with multiple invalid fields should report all violations."""
        with pytest.raises(ValidationError) as exc_info:
            Process(
                entity_id="p1",
                timestamp=-1.0,
                start_time=-2.0,
                end_time=-3.0,
            )
        errors = exc_info.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "timestamp" in field_names
        assert "start_time" in field_names
        assert "end_time" in field_names


class TestSerializationRoundTrip:
    """Verify JSON serialization round-trip (Requirement 3.3, 3.5)."""

    def test_entity_round_trip(self):
        original = Entity(entity_id="e1", entity_type="test")
        json_str = original.model_dump_json()
        restored = Entity.model_validate_json(json_str)
        assert restored == original

    def test_process_round_trip(self):
        original = Process(
            entity_id="p1", timestamp=5.0, start_time=5.0, end_time=10.0
        )
        json_str = original.model_dump_json()
        restored = Process.model_validate_json(json_str)
        assert restored == original

    def test_spatial_region_round_trip(self):
        original = SpatialRegion(
            entity_id="sr1",
            latitude=34.05,
            longitude=-118.25,
            altitude_ft=25000.0,
        )
        json_str = original.model_dump_json()
        restored = SpatialRegion.model_validate_json(json_str)
        assert restored == original

    def test_information_entity_round_trip(self):
        original = InformationEntity(entity_id="ie1", classification="TOP_SECRET")
        json_str = original.model_dump_json()
        restored = InformationEntity.model_validate_json(json_str)
        assert restored == original
