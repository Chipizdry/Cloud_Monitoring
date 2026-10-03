from __future__ import annotations
from pydantic import Field
from backend.schemas.base import BaseSchema


class FeedbackRatingScheema(BaseSchema):
    rating: int = Field(..., ge=1, le=5, description="Оценка от 1 до 5")
    comment: str = Field(
        ..., min_length=2, max_length=800, description="Комментарий до 800 символов"
    )


class FeedbackProposalsScheema(BaseSchema):
    proposal: str = Field(..., min_length=2, max_length=800, description="Предложения")
