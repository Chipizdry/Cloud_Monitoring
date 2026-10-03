"""
Base classes for Pydantic schemas with common configurations.
"""

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """
    Base Pydantic schema with default configuration.
    Allows model population from ORM objects (SQLAlchemy models).
    """

    model_config = ConfigDict(from_attributes=True)


class BaseResponseSchema(BaseSchema):
    """Base schema for response models with ORM compatibility."""

    pass
