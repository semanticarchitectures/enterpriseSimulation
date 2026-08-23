"""Unit tests for the configuration loader."""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from enterprise_sim.config.loader import load, load_from_json, load_from_yaml
from enterprise_sim.engine.exceptions import ConfigurationError
from enterprise_sim.models.mission import MissionConfiguration


def _minimal_valid_config() -> dict:
    """Return a minimal valid MissionConfiguration as a dict."""
    return {
        "mission_id": "test-001",
        "mission_name": "Test Mission",
        "route_graph": {
            "nodes": [
                {"entity_id": "n1", "name": "origin_node"},
                {"entity_id": "n2", "name": "dest_node"},
            ],
            "edges": [
                {"source": "origin_node", "destination": "dest_node", "distance_nm": 100.0}
            ],
            "origin": "origin_node",
            "destination": "dest_node",
        },
        "aircraft": {
            "entity_id": "ac1",
            "aircraft_type": "C-17",
            "lift_to_drag_ratio": 15.0,
            "specific_fuel_consumption": 0.6,
            "initial_fuel_weight": 50000.0,
            "max_fuel_capacity": 60000.0,
        },
        "active_modules": [],
    }


class TestLoadFromYaml:
    """Tests for load_from_yaml function."""

    def test_loads_valid_yaml(self, tmp_path):
        config_data = _minimal_valid_config()
        yaml_file = tmp_path / "mission.yaml"
        yaml_file.write_text(yaml.dump(config_data), encoding="utf-8")

        result = load_from_yaml(yaml_file)

        assert isinstance(result, MissionConfiguration)
        assert result.mission_id == "test-001"
        assert result.mission_name == "Test Mission"

    def test_file_not_found_raises_configuration_error(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist.yaml"

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_yaml(nonexistent)

        assert "path" in exc_info.value.invalid_fields
        assert "not found" in exc_info.value.message.lower() or "does not exist" in exc_info.value.violations[0].lower()

    def test_invalid_yaml_syntax_raises_configuration_error(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("{{{{invalid: yaml: content: [", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_yaml(yaml_file)

        assert "format" in exc_info.value.invalid_fields

    def test_non_mapping_yaml_raises_configuration_error(self, tmp_path):
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_yaml(yaml_file)

        assert "format" in exc_info.value.invalid_fields

    def test_validation_failure_reports_invalid_fields(self, tmp_path):
        config_data = _minimal_valid_config()
        # Introduce invalid fields
        config_data["aircraft"]["lift_to_drag_ratio"] = -1.0  # must be > 0
        config_data["aircraft"]["initial_fuel_weight"] = -100.0  # must be > 0

        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text(yaml.dump(config_data), encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_yaml(yaml_file)

        err = exc_info.value
        assert len(err.invalid_fields) >= 2
        assert len(err.violations) >= 2

    def test_loads_yml_extension(self, tmp_path):
        config_data = _minimal_valid_config()
        yml_file = tmp_path / "mission.yml"
        yml_file.write_text(yaml.dump(config_data), encoding="utf-8")

        result = load_from_yaml(yml_file)
        assert isinstance(result, MissionConfiguration)

    def test_accepts_string_path(self, tmp_path):
        config_data = _minimal_valid_config()
        yaml_file = tmp_path / "mission.yaml"
        yaml_file.write_text(yaml.dump(config_data), encoding="utf-8")

        result = load_from_yaml(str(yaml_file))
        assert isinstance(result, MissionConfiguration)


class TestLoadFromJson:
    """Tests for load_from_json function."""

    def test_loads_valid_json(self, tmp_path):
        config_data = _minimal_valid_config()
        json_file = tmp_path / "mission.json"
        json_file.write_text(json.dumps(config_data), encoding="utf-8")

        result = load_from_json(json_file)

        assert isinstance(result, MissionConfiguration)
        assert result.mission_id == "test-001"
        assert result.mission_name == "Test Mission"

    def test_file_not_found_raises_configuration_error(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist.json"

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_json(nonexistent)

        assert "path" in exc_info.value.invalid_fields

    def test_invalid_json_syntax_raises_configuration_error(self, tmp_path):
        json_file = tmp_path / "bad.json"
        json_file.write_text("{not valid json!!!", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_json(json_file)

        assert "format" in exc_info.value.invalid_fields

    def test_non_object_json_raises_configuration_error(self, tmp_path):
        json_file = tmp_path / "array.json"
        json_file.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_json(json_file)

        assert "format" in exc_info.value.invalid_fields

    def test_validation_failure_reports_all_invalid_fields(self, tmp_path):
        config_data = _minimal_valid_config()
        # Remove required fields to trigger multiple violations
        del config_data["mission_id"]
        del config_data["mission_name"]

        json_file = tmp_path / "invalid.json"
        json_file.write_text(json.dumps(config_data), encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_from_json(json_file)

        err = exc_info.value
        assert len(err.invalid_fields) >= 2
        assert len(err.violations) >= 2

    def test_accepts_string_path(self, tmp_path):
        config_data = _minimal_valid_config()
        json_file = tmp_path / "mission.json"
        json_file.write_text(json.dumps(config_data), encoding="utf-8")

        result = load_from_json(str(json_file))
        assert isinstance(result, MissionConfiguration)


class TestLoad:
    """Tests for the auto-detecting load function."""

    def test_detects_yaml_extension(self, tmp_path):
        config_data = _minimal_valid_config()
        yaml_file = tmp_path / "mission.yaml"
        yaml_file.write_text(yaml.dump(config_data), encoding="utf-8")

        result = load(yaml_file)
        assert isinstance(result, MissionConfiguration)

    def test_detects_yml_extension(self, tmp_path):
        config_data = _minimal_valid_config()
        yml_file = tmp_path / "mission.yml"
        yml_file.write_text(yaml.dump(config_data), encoding="utf-8")

        result = load(yml_file)
        assert isinstance(result, MissionConfiguration)

    def test_detects_json_extension(self, tmp_path):
        config_data = _minimal_valid_config()
        json_file = tmp_path / "mission.json"
        json_file.write_text(json.dumps(config_data), encoding="utf-8")

        result = load(json_file)
        assert isinstance(result, MissionConfiguration)

    def test_unsupported_extension_raises_configuration_error(self, tmp_path):
        xml_file = tmp_path / "mission.xml"
        xml_file.write_text("<config/>", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load(xml_file)

        assert "path" in exc_info.value.invalid_fields
        assert ".xml" in exc_info.value.violations[0]

    def test_case_insensitive_extension(self, tmp_path):
        config_data = _minimal_valid_config()
        yaml_file = tmp_path / "mission.YAML"
        yaml_file.write_text(yaml.dump(config_data), encoding="utf-8")

        result = load(yaml_file)
        assert isinstance(result, MissionConfiguration)

    def test_accepts_string_path(self, tmp_path):
        config_data = _minimal_valid_config()
        json_file = tmp_path / "mission.json"
        json_file.write_text(json.dumps(config_data), encoding="utf-8")

        result = load(str(json_file))
        assert isinstance(result, MissionConfiguration)
