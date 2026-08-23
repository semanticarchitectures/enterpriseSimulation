"""Unit tests for C2 messaging models."""

import pytest
from pydantic import ValidationError

from enterprise_sim.models.messaging import C2MessageType, C2MessageInstance


class TestC2MessageType:
    """Tests for C2MessageType model."""

    def test_valid_message_type(self):
        msg_type = C2MessageType(
            entity_id="mt-001",
            name="ATO",
            source_node="CAOC",
            destination_node="Wing_HQ",
            priority=1,
            transmission_latency=5.0,
        )
        assert msg_type.entity_type == "c2_message_type"
        assert msg_type.name == "ATO"
        assert msg_type.source_node == "CAOC"
        assert msg_type.destination_node == "Wing_HQ"
        assert msg_type.priority == 1
        assert msg_type.transmission_latency == 5.0
        assert msg_type.classification is None

    def test_message_type_with_classification(self):
        msg_type = C2MessageType(
            entity_id="mt-002",
            name="OPREP3",
            source_node="Unit_A",
            destination_node="Unit_B",
            priority=3,
            transmission_latency=2.5,
            classification="SECRET",
        )
        assert msg_type.classification == "SECRET"

    def test_priority_must_be_at_least_1(self):
        with pytest.raises(ValidationError):
            C2MessageType(
                entity_id="mt-003",
                name="ACO",
                source_node="A",
                destination_node="B",
                priority=0,
                transmission_latency=1.0,
            )

    def test_transmission_latency_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            C2MessageType(
                entity_id="mt-004",
                name="ACO",
                source_node="A",
                destination_node="B",
                priority=1,
                transmission_latency=-1.0,
            )

    def test_inherits_from_information_entity(self):
        from enterprise_sim.models.base import InformationEntity

        msg_type = C2MessageType(
            entity_id="mt-005",
            name="AIRMOVE",
            source_node="X",
            destination_node="Y",
            priority=2,
            transmission_latency=0.0,
        )
        assert isinstance(msg_type, InformationEntity)


class TestC2MessageInstance:
    """Tests for C2MessageInstance model."""

    def test_valid_message_instance_defaults(self):
        msg = C2MessageInstance(
            entity_id="mi-001",
            timestamp=10.0,
            message_type="ATO",
            generation_time=10.0,
        )
        assert msg.entity_type == "c2_message_instance"
        assert msg.message_type == "ATO"
        assert msg.generation_time == 10.0
        assert msg.transmission_start_time is None
        assert msg.delivery_time is None
        assert msg.status == "generated"

    def test_message_instance_full_lifecycle(self):
        msg = C2MessageInstance(
            entity_id="mi-002",
            timestamp=10.0,
            message_type="OPREP3",
            generation_time=10.0,
            transmission_start_time=11.0,
            delivery_time=15.0,
            status="delivered",
        )
        assert msg.transmission_start_time == 11.0
        assert msg.delivery_time == 15.0
        assert msg.status == "delivered"

    def test_generation_time_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            C2MessageInstance(
                entity_id="mi-003",
                timestamp=0.0,
                message_type="ATO",
                generation_time=-1.0,
            )

    def test_inherits_from_occurrent(self):
        from enterprise_sim.models.base import Occurrent

        msg = C2MessageInstance(
            entity_id="mi-004",
            timestamp=5.0,
            message_type="ACO",
            generation_time=5.0,
        )
        assert isinstance(msg, Occurrent)

    def test_failed_status(self):
        msg = C2MessageInstance(
            entity_id="mi-005",
            timestamp=20.0,
            message_type="CARP_LOG",
            generation_time=20.0,
            transmission_start_time=21.0,
            status="failed",
        )
        assert msg.status == "failed"
        assert msg.delivery_time is None
