"""Property test for data model serialization round-trip.

**Property 5: Data Model Serialization Round-Trip**
**Validates: Requirements 3.3, 3.5**

For any valid Data_Model_Layer entity (across all entity types), serializing to JSON
and deserializing SHALL produce an entity with field-by-field equality to the original,
including nested entities and collection fields.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from enterprise_sim.models.base import (
    Agent,
    Continuant,
    Entity,
    InformationEntity,
    Occurrent,
    Process,
    SpatialRegion,
)


# === Hypothesis Strategies ===

# Reusable strategy for non-empty strings (entity_id, entity_type, etc.)
non_empty_str = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=50,
)

# Strategy for non-negative floats (timestamps, start_time)
non_negative_float = st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False)

# Strategy for optional floats (latitude, longitude, altitude_ft)
optional_float = st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False))

# Strategy for optional non-negative floats (end_time)
optional_non_negative_float = st.one_of(
    st.none(), st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False)
)

# Strategy for optional strings (classification)
optional_str = st.one_of(st.none(), non_empty_str)


@st.composite
def entity_strategy(draw):
    """Generate a valid Entity instance."""
    return Entity(
        entity_id=draw(non_empty_str),
        entity_type=draw(non_empty_str),
    )


@st.composite
def continuant_strategy(draw):
    """Generate a valid Continuant instance."""
    return Continuant(
        entity_id=draw(non_empty_str),
    )


@st.composite
def occurrent_strategy(draw):
    """Generate a valid Occurrent instance."""
    return Occurrent(
        entity_id=draw(non_empty_str),
        timestamp=draw(non_negative_float),
    )


@st.composite
def information_entity_strategy(draw):
    """Generate a valid InformationEntity instance."""
    return InformationEntity(
        entity_id=draw(non_empty_str),
        classification=draw(optional_str),
    )


@st.composite
def agent_strategy(draw):
    """Generate a valid Agent instance."""
    return Agent(
        entity_id=draw(non_empty_str),
    )


@st.composite
def spatial_region_strategy(draw):
    """Generate a valid SpatialRegion instance."""
    return SpatialRegion(
        entity_id=draw(non_empty_str),
        latitude=draw(optional_float),
        longitude=draw(optional_float),
        altitude_ft=draw(optional_float),
    )


@st.composite
def process_strategy(draw):
    """Generate a valid Process instance."""
    start_time = draw(non_negative_float)
    end_time = draw(optional_non_negative_float)
    return Process(
        entity_id=draw(non_empty_str),
        start_time=start_time,
        end_time=end_time,
        timestamp=draw(non_negative_float),
    )


# === Property Tests ===


class TestDataModelSerializationRoundTrip:
    """Property 5: Data Model Serialization Round-Trip.

    Validates: Requirements 3.3, 3.5

    For any valid entity instance, serializing to JSON and deserializing from that
    JSON produces an entity with field-by-field equality to the original.
    """

    @given(entity=entity_strategy())
    @settings(max_examples=100)
    def test_entity_roundtrip(self, entity: Entity):
        """Entity serialization round-trip preserves all fields."""
        json_str = entity.model_dump_json()
        restored = Entity.model_validate_json(json_str)
        assert restored == entity

    @given(continuant=continuant_strategy())
    @settings(max_examples=100)
    def test_continuant_roundtrip(self, continuant: Continuant):
        """Continuant serialization round-trip preserves all fields."""
        json_str = continuant.model_dump_json()
        restored = Continuant.model_validate_json(json_str)
        assert restored == continuant

    @given(occurrent=occurrent_strategy())
    @settings(max_examples=100)
    def test_occurrent_roundtrip(self, occurrent: Occurrent):
        """Occurrent serialization round-trip preserves all fields."""
        json_str = occurrent.model_dump_json()
        restored = Occurrent.model_validate_json(json_str)
        assert restored == occurrent

    @given(info_entity=information_entity_strategy())
    @settings(max_examples=100)
    def test_information_entity_roundtrip(self, info_entity: InformationEntity):
        """InformationEntity serialization round-trip preserves all fields."""
        json_str = info_entity.model_dump_json()
        restored = InformationEntity.model_validate_json(json_str)
        assert restored == info_entity

    @given(agent=agent_strategy())
    @settings(max_examples=100)
    def test_agent_roundtrip(self, agent: Agent):
        """Agent serialization round-trip preserves all fields."""
        json_str = agent.model_dump_json()
        restored = Agent.model_validate_json(json_str)
        assert restored == agent

    @given(spatial_region=spatial_region_strategy())
    @settings(max_examples=100)
    def test_spatial_region_roundtrip(self, spatial_region: SpatialRegion):
        """SpatialRegion serialization round-trip preserves all fields."""
        json_str = spatial_region.model_dump_json()
        restored = SpatialRegion.model_validate_json(json_str)
        assert restored == spatial_region

    @given(process=process_strategy())
    @settings(max_examples=100)
    def test_process_roundtrip(self, process: Process):
        """Process serialization round-trip preserves all fields."""
        json_str = process.model_dump_json()
        restored = Process.model_validate_json(json_str)
        assert restored == process
