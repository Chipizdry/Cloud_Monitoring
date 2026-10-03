from __future__ import annotations

from pydantic import Field
from backend.schemas.base import BaseSchema


class PasswordGeneratorSettings(BaseSchema):
    length: int = Field(12, ge=8, le=128)
    include_uppercase: bool = True
    include_lowercase: bool = True
    include_digits: bool = True
    include_special: bool = True
