"""Small JSON Schema subset shared by tool execution and OpenAPI export."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, TypeGuard

JsonObject = dict[str, Any]

_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean"})
_COMMON = frozenset({"type", "description", "default", "enum"})
_KEYWORDS = {
    "object": _COMMON | {"properties", "required", "additionalProperties"},
    "array": _COMMON | {"items"},
    "string": _COMMON | {"minLength", "maxLength"},
    "integer": _COMMON | {"minimum", "maximum"},
    "number": _COMMON | {"minimum", "maximum"},
    "boolean": _COMMON,
}


class SchemaDefinitionError(ValueError):
    """A registry schema is malformed or uses an unsupported keyword."""


class SchemaValidationError(ValueError):
    """A value does not satisfy a validated registry schema."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def validate_schema_definition(schema: Mapping[str, object], *, path: str = "$") -> None:
    """Validate the dependency-free schema subset accepted by the registry."""
    raw_type = schema.get("type")
    if not isinstance(raw_type, str) or raw_type not in _TYPES:
        raise SchemaDefinitionError(f"{path}.type must be one of {sorted(_TYPES)}")
    unknown = set(schema) - _KEYWORDS[raw_type]
    if unknown:
        raise SchemaDefinitionError(
            f"{path} uses unsupported keyword(s): {', '.join(sorted(unknown))}"
        )
    description = schema.get("description")
    if description is not None and not isinstance(description, str):
        raise SchemaDefinitionError(f"{path}.description must be a string")
    enum = schema.get("enum")
    if enum is not None and (not _is_sequence(enum) or not enum):
        raise SchemaDefinitionError(f"{path}.enum must be a non-empty array")

    if raw_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping) or any(
            not isinstance(name, str) or not isinstance(child, Mapping)
            for name, child in properties.items()
        ):
            raise SchemaDefinitionError(f"{path}.properties must map names to schemas")
        required = schema.get("required", ())
        if not _is_sequence(required) or any(not isinstance(name, str) for name in required):
            raise SchemaDefinitionError(f"{path}.required must be an array of strings")
        required_names = [name for name in required if isinstance(name, str)]
        missing = set(required_names) - set(properties)
        if missing:
            raise SchemaDefinitionError(
                f"{path}.required names undefined properties: {', '.join(sorted(missing))}"
            )
        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, bool):
            raise SchemaDefinitionError(f"{path}.additionalProperties must be boolean")
        for name, child in properties.items():
            assert isinstance(name, str) and isinstance(child, Mapping)
            validate_schema_definition(child, path=f"{path}.properties.{name}")
    elif raw_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise SchemaDefinitionError(f"{path}.items must be a schema")
        validate_schema_definition(items, path=f"{path}.items")
    elif raw_type == "string":
        _validate_nonnegative_integer_keyword(schema, "minLength", path)
        _validate_nonnegative_integer_keyword(schema, "maxLength", path)
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
            raise SchemaDefinitionError(f"{path}.minLength exceeds maxLength")
    elif raw_type in {"integer", "number"}:
        for keyword in ("minimum", "maximum"):
            value = schema.get(keyword)
            if value is not None and (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise SchemaDefinitionError(f"{path}.{keyword} must be a finite number")
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if (
            isinstance(minimum, int | float)
            and not isinstance(minimum, bool)
            and isinstance(maximum, int | float)
            and not isinstance(maximum, bool)
            and minimum > maximum
        ):
            raise SchemaDefinitionError(f"{path}.minimum exceeds maximum")


def _validate_nonnegative_integer_keyword(
    schema: Mapping[str, object], keyword: str, path: str
) -> None:
    value = schema.get(keyword)
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value < 0
    ):
        raise SchemaDefinitionError(f"{path}.{keyword} must be a non-negative integer")


def validate_instance(value: object, schema: Mapping[str, object], *, path: str = "$") -> None:
    """Validate one JSON-compatible value without injecting schema defaults."""
    schema_type = schema["type"]
    enum = schema.get("enum")
    if _is_sequence(enum) and value not in enum:
        raise SchemaValidationError(path, "value is not in the allowed enum")

    if schema_type == "object":
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise SchemaValidationError(path, "must be an object with string keys")
        properties = schema.get("properties", {})
        assert isinstance(properties, Mapping)
        required = schema.get("required", ())
        assert _is_sequence(required)
        missing = [name for name in required if isinstance(name, str) and name not in value]
        if missing:
            raise SchemaValidationError(path, f"missing required property: {missing[0]}")
        if schema.get("additionalProperties", True) is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise SchemaValidationError(path, f"unexpected property: {unknown[0]}")
        for name, child_value in value.items():
            child_schema = properties.get(name)
            if child_schema is not None:
                assert isinstance(child_schema, Mapping)
                validate_instance(child_value, child_schema, path=f"{path}.{name}")
        return

    if schema_type == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(path, "must be an array")
        items = schema["items"]
        assert isinstance(items, Mapping)
        for index, item in enumerate(value):
            validate_instance(item, items, path=f"{path}[{index}]")
        return

    if schema_type == "string":
        if not isinstance(value, str):
            raise SchemaValidationError(path, "must be a string")
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise SchemaValidationError(path, f"must contain at least {minimum} characters")
        if isinstance(maximum, int) and len(value) > maximum:
            raise SchemaValidationError(path, f"must contain at most {maximum} characters")
        return

    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise SchemaValidationError(path, "must be a boolean")
        return

    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SchemaValidationError(path, "must be an integer")
        _validate_numeric_instance(value, schema, path)
        return

    if schema_type == "number":
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise SchemaValidationError(path, "must be a finite number")
        _validate_numeric_instance(value, schema, path)
        return

    raise AssertionError(f"validated schema has unknown type: {schema_type}")


def _validate_numeric_instance(
    value: int | float, schema: Mapping[str, object], path: str
) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int | float) and not isinstance(minimum, bool) and value < minimum:
        raise SchemaValidationError(path, f"must be at least {minimum}")
    if isinstance(maximum, int | float) and not isinstance(maximum, bool) and value > maximum:
        raise SchemaValidationError(path, f"must be at most {maximum}")


def mutable_json(value: object) -> Any:
    """Return mutable JSON containers from frozen registry data."""
    if isinstance(value, Mapping):
        return {str(key): mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [mutable_json(item) for item in value]
    return value
