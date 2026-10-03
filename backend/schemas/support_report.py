from __future__ import annotations
from pydantic import Field
from backend.schemas.base import BaseSchema


class SupportReportScheema(BaseSchema):
    product_name: str = Field(
        ..., min_length=2, max_length=20, description="Название продукта"
    )
    report_text: str = Field(
        ..., min_length=2, max_length=800, description="Текст ошибки"
    )
