"""
api/validation.py — Input validation helpers for API endpoints.

Provides safe JSON parsing and type validation with proper HTTP error responses.
"""
from __future__ import annotations

from typing import Any, Optional

from aiohttp import web


async def safe_json(request: web.Request) -> dict[str, Any]:
    """
    Safely parse JSON from request body.

    Returns empty dict if body is empty or not parseable.
    Raises HTTPBadRequest with descriptive message on malformed JSON.
    """
    if not request.can_read_body:
        return {}

    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except ValueError as exc:
        raise web.HTTPBadRequest(
            text=f"Request body must be valid JSON: {exc}",
            content_type="application/json"
        )
    except Exception as exc:
        raise web.HTTPBadRequest(
            text=f"Failed to parse request body: {exc}",
            content_type="application/json"
        )


def validate_string(
    data: dict,
    key: str,
    *,
    required: bool = True,
    default: str = "",
    choices: Optional[list[str]] = None,
    max_length: Optional[int] = None,
) -> str:
    """
    Validate a string field.

    Args:
        data: Request data dict
        key: Field name to validate
        required: If True, raises HTTPBadRequest if field missing
        default: Default value if field missing (only if not required)
        choices: If provided, value must be in this list
        max_length: Maximum string length

    Returns:
        Validated string value

    Raises:
        HTTPBadRequest: If validation fails
    """
    if key not in data:
        if required:
            raise web.HTTPBadRequest(text=f"Missing required field: {key}")
        return default

    value = str(data[key]).strip()

    if required and not value:
        raise web.HTTPBadRequest(text=f"Field '{key}' cannot be empty")

    if choices and value not in choices:
        raise web.HTTPBadRequest(
            text=f"Field '{key}' must be one of: {', '.join(choices)}. Got: {value}"
        )

    if max_length and len(value) > max_length:
        raise web.HTTPBadRequest(
            text=f"Field '{key}' exceeds maximum length of {max_length} characters"
        )

    return value


def validate_number(
    data: dict,
    key: str,
    *,
    required: bool = True,
    default: float = 0.0,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    integer: bool = False,
) -> float | int:
    """
    Validate a numeric field.

    Args:
        data: Request data dict
        key: Field name to validate
        required: If True, raises HTTPBadRequest if field missing
        default: Default value if field missing (only if not required)
        min_value: Minimum allowed value
        max_value: Maximum allowed value
        integer: If True, convert to int and validate as integer

    Returns:
        Validated numeric value (int or float)

    Raises:
        HTTPBadRequest: If validation fails
    """
    if key not in data:
        if required:
            raise web.HTTPBadRequest(text=f"Missing required field: {key}")
        return int(default) if integer else default

    try:
        value = float(data[key])

        if integer:
            if not value.is_integer():
                raise ValueError("Value must be an integer")
            value = int(value)
    except (ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(
            text=f"Field '{key}' must be a valid number. Got: {data[key]}"
        )

    if min_value is not None and value < min_value:
        raise web.HTTPBadRequest(
            text=f"Field '{key}' must be at least {min_value}. Got: {value}"
        )

    if max_value is not None and value > max_value:
        raise web.HTTPBadRequest(
            text=f"Field '{key}' must be at most {max_value}. Got: {value}"
        )

    return value


def validate_boolean(
    data: dict,
    key: str,
    *,
    required: bool = True,
    default: bool = False,
) -> bool:
    """
    Validate a boolean field.

    Args:
        data: Request data dict
        key: Field name to validate
        required: If True, raises HTTPBadRequest if field missing
        default: Default value if field missing (only if not required)

    Returns:
        Validated boolean value

    Raises:
        HTTPBadRequest: If validation fails
    """
    if key not in data:
        if required:
            raise web.HTTPBadRequest(text=f"Missing required field: {key}")
        return default

    value = data[key]

    if isinstance(value, bool):
        return value

    # Try to convert string representations
    if isinstance(value, str):
        value_lower = value.lower().strip()
        if value_lower in {"true", "1", "yes", "on"}:
            return True
        if value_lower in {"false", "0", "no", "off"}:
            return False

    # Try numeric values
    if isinstance(value, (int, float)):
        return bool(value)

    raise web.HTTPBadRequest(
        text=f"Field '{key}' must be a boolean value. Got: {value}"
    )


def validate_enum(
    data: dict,
    key: str,
    choices: list[str],
    *,
    required: bool = True,
    default: Optional[str] = None,
) -> str:
    """
    Validate an enum/choice field (alias for validate_string with choices).

    Args:
        data: Request data dict
        key: Field name to validate
        choices: List of valid choices
        required: If True, raises HTTPBadRequest if field missing
        default: Default value if field missing (only if not required)

    Returns:
        Validated enum value

    Raises:
        HTTPBadRequest: If validation fails
    """
    return validate_string(
        data,
        key,
        required=required,
        default=default or "",
        choices=choices
    )
