"""Pydantic models shared by the REST API and the MCP tools.

The item write model is deliberately one type used by both surfaces so that
``add_item`` over MCP and ``POST /api/items`` accept exactly the same shape and
enforce exactly the same rules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Formality = Literal["casual", "smart_casual", "formal", "athletic"]
UnitPreference = Literal["fahrenheit", "celsius"]
FieldType = Literal["text", "enum", "number", "boolean"]

MAX_COLORS = 5


def _clean_strings(values: list[str] | None, *, lower: bool = False) -> list[str]:
    """Trim, drop blanks, and de-duplicate while preserving order."""
    if not values:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if lower:
            text = text.lower()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


class ItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colors: list[str] = Field(
        default_factory=list,
        description=f"Up to {MAX_COLORS} colour names, e.g. ['navy', 'white'].",
    )
    brand: str | None = None
    warmth_rating: int | None = Field(
        default=None, ge=1, le=5, description="1 = very light, 5 = heaviest."
    )
    formality: Formality | None = None
    tags: list[str] = Field(
        default_factory=list,
        description="Free-form labels, e.g. ['date-night', 'floral'].",
    )
    notes: str | None = Field(
        default=None, max_length=2000, description="Anything that does not fit a structured field."
    )
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Category-specific fields, validated against the category's template.",
    )

    @field_validator("colors")
    @classmethod
    def _check_colors(cls, value: list[str]) -> list[str]:
        cleaned = _clean_strings(value, lower=True)
        if len(cleaned) > MAX_COLORS:
            raise ValueError(f"at most {MAX_COLORS} colors are allowed, got {len(cleaned)}")
        return cleaned

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        return _clean_strings(value, lower=True)

    @field_validator("brand")
    @classmethod
    def _strip_brand(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ItemCreate(ItemBase):
    category: str = Field(description="A category id from get_closet_structure, e.g. 'tshirt'.")

    @field_validator("category")
    @classmethod
    def _normalise_category(cls, value: str) -> str:
        return value.strip().lower()


class ItemUpdate(BaseModel):
    """Every field optional — only what is supplied is changed."""

    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    colors: list[str] | None = None
    brand: str | None = None
    warmth_rating: int | None = Field(default=None, ge=1, le=5)
    formality: Formality | None = None
    tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)
    fields: dict[str, Any] | None = None

    @field_validator("category")
    @classmethod
    def _normalise_category(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else None

    @field_validator("colors")
    @classmethod
    def _check_colors(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = _clean_strings(value, lower=True)
        if len(cleaned) > MAX_COLORS:
            raise ValueError(f"at most {MAX_COLORS} colors are allowed, got {len(cleaned)}")
        return cleaned

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_strings(value, lower=True)


class Item(ItemBase):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    category: str
    created_at: datetime
    updated_at: datetime


class CategoryFieldDef(BaseModel):
    field_name: str
    field_type: FieldType
    required: bool
    allowed_values: list[str] | None = None
    display_order: int = 0


class Category(BaseModel):
    id: str
    display_name: str
    sort_order: int
    fields: list[CategoryFieldDef] = Field(default_factory=list)


class UserProfile(BaseModel):
    home_location: str | None = None
    unit_preference: UnitPreference = "fahrenheit"


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home_location: str | None = None
    unit_preference: UnitPreference | None = None
