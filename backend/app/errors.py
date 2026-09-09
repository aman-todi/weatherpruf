"""Domain errors shared by the REST and MCP surfaces.

Both entry points raise the same errors from the same service functions and
translate them at their own boundary: FastAPI to an HTTP status, MCP to a
structured tool error the assistant can relay to the user in plain language.
"""

from __future__ import annotations


class WardrobeError(Exception):
    """Base class. ``code`` is stable and safe to show to an assistant."""

    code = "error"
    http_status = 400

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"error": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class NotFoundError(WardrobeError):
    code = "not_found"
    http_status = 404


class ValidationError(WardrobeError):
    code = "validation_failed"
    http_status = 422


class UnknownCategoryError(ValidationError):
    code = "unknown_category"


class ClosetFullError(WardrobeError):
    code = "closet_full"
    http_status = 409


class UsageLimitExceededError(WardrobeError):
    code = "daily_limit_reached"
    http_status = 429


class UnsafeQueryError(WardrobeError):
    """The assistant's ``where_clause`` did not survive validation (spec §4.1)."""

    code = "unsafe_query"
    http_status = 400
