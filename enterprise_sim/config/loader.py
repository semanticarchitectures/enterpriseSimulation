"""YAML/JSON config parsing and validation.

Provides functions to load MissionConfiguration from YAML or JSON files,
with automatic format detection by file extension. On failure, raises
ConfigurationError with details about invalid fields and constraint violations.
"""

import json
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import ValidationError

from enterprise_sim.engine.exceptions import ConfigurationError
from enterprise_sim.models.mission import MissionConfiguration


def _validate_config(data: Dict[str, Any]) -> MissionConfiguration:
    """Validate a parsed dictionary against MissionConfiguration schema.

    Args:
        data: Dictionary of configuration data.

    Returns:
        Validated MissionConfiguration instance.

    Raises:
        ConfigurationError: If validation fails, with invalid_fields and
            violations populated from all Pydantic validation errors.
    """
    try:
        return MissionConfiguration(**data)
    except ValidationError as e:
        invalid_fields: list[str] = []
        violations: list[str] = []
        for error in e.errors():
            # Build field path from loc tuple (e.g., ("route_graph", "nodes", 0, "name"))
            field_path = ".".join(str(part) for part in error["loc"])
            if field_path and field_path not in invalid_fields:
                invalid_fields.append(field_path)
            violations.append(f"{field_path}: {error['msg']}")
        raise ConfigurationError(
            message=f"Configuration validation failed: {len(invalid_fields)} field(s) invalid",
            invalid_fields=invalid_fields,
            violations=violations,
        ) from e


def load_from_yaml(path: str | Path) -> MissionConfiguration:
    """Load and validate a MissionConfiguration from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Validated MissionConfiguration instance.

    Raises:
        ConfigurationError: If the file is not found, cannot be parsed as YAML,
            or fails schema validation.
    """
    file_path = Path(path)

    if not file_path.exists():
        raise ConfigurationError(
            message=f"Configuration file not found: {file_path}",
            invalid_fields=["path"],
            violations=[f"File does not exist: {file_path}"],
        )

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigurationError(
            message=f"Failed to read configuration file: {file_path}",
            invalid_fields=["path"],
            violations=[str(e)],
        ) from e

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ConfigurationError(
            message=f"Failed to parse YAML: {e}",
            invalid_fields=["format"],
            violations=[f"Invalid YAML syntax: {e}"],
        ) from e

    if not isinstance(data, dict):
        raise ConfigurationError(
            message="YAML content must be a mapping (dictionary)",
            invalid_fields=["format"],
            violations=["YAML content must be a mapping (dictionary), "
                        f"got {type(data).__name__}"],
        )

    return _validate_config(data)


def load_from_json(path: str | Path) -> MissionConfiguration:
    """Load and validate a MissionConfiguration from a JSON file.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        Validated MissionConfiguration instance.

    Raises:
        ConfigurationError: If the file is not found, cannot be parsed as JSON,
            or fails schema validation.
    """
    file_path = Path(path)

    if not file_path.exists():
        raise ConfigurationError(
            message=f"Configuration file not found: {file_path}",
            invalid_fields=["path"],
            violations=[f"File does not exist: {file_path}"],
        )

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigurationError(
            message=f"Failed to read configuration file: {file_path}",
            invalid_fields=["path"],
            violations=[str(e)],
        ) from e

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ConfigurationError(
            message=f"Failed to parse JSON: {e}",
            invalid_fields=["format"],
            violations=[f"Invalid JSON syntax: {e}"],
        ) from e

    if not isinstance(data, dict):
        raise ConfigurationError(
            message="JSON content must be an object (dictionary)",
            invalid_fields=["format"],
            violations=["JSON content must be an object (dictionary), "
                        f"got {type(data).__name__}"],
        )

    return _validate_config(data)


def load(path: str | Path) -> MissionConfiguration:
    """Load and validate a MissionConfiguration, auto-detecting format by extension.

    Supported extensions:
        - .yaml, .yml → YAML format
        - .json → JSON format

    Args:
        path: Path to the configuration file.

    Returns:
        Validated MissionConfiguration instance.

    Raises:
        ConfigurationError: If file extension is unsupported, file is not found,
            cannot be parsed, or fails schema validation.
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix in (".yaml", ".yml"):
        return load_from_yaml(file_path)
    elif suffix == ".json":
        return load_from_json(file_path)
    else:
        raise ConfigurationError(
            message=f"Unsupported configuration file format: '{suffix}'",
            invalid_fields=["path"],
            violations=[
                f"Unsupported file extension '{suffix}'. "
                "Supported formats: .yaml, .yml, .json"
            ],
        )
